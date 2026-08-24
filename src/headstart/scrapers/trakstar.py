"""Trakstar Hire job-board scraper ({slug}.hire.trakstar.com).

Trakstar's job API is DataDome-protected and obfuscated, but the careers landing page
server-renders the opening list into the HTML (the bot wall only guards the XHR layer, not
the document GET). So, like Zoho, we GET the page and parse the rendered job cards:
    <div class="... js-careers-page-job-list-item" data-href="/jobs/{code}/">
      <h3 class="... js-job-list-opening-name" ... title="{title}">
      <div class="... js-job-list-opening-loc" ... title="{location}">

The card has no description; each job's own page (/jobs/{code}/) embeds a schema.org
JobPosting JSON-LD block whose ``description`` we extract in a second, bounded pass. Some
tenant boards never emit that block at all — same template, real rendered content, just no
JSON-LD anywhere on the page (#179) — so when the JSON-LD parse comes back empty we fall back
to the rendered ``<div class="jobdesciption">`` container (that's the tenant template's own
spelling, not a typo here). Detail pages go through curl_cffi (TLS impersonation) since they
sit behind the same DataDome edge; a failed fetch leaves description None — the job is still
kept.

**Known gap, under active investigation (2026-08-22): the careers page above silently caps at
25 rendered job cards.** Confirmed live on a real board (exotel: 25 rendered, 45 real, via its
RSS feed) and measured at 5.4% of a 148-board live sample (``scripts/eval/trakstar_feed_compare.py
--n 150``, seed=7) hitting the cap — 154 real jobs recovered by the feed across that one run.
Trakstar also serves a per-tenant RSS feed (``/jobfeeds/{slug}``, ``_fetch_feed``/``_feed_items``/
``fetch_via_feed`` below) that carries every job with no such cap, embeds the full description
inline (no per-job detail fetch needed), and — unlike the detail pages — is reachable through
plain ``http.fetch`` with no DataDome/curl_cffi involved (confirmed: 0 errors across 148 sampled
boards). It is NOT universal — the same run found the feed truly unreachable (404 or
unparseable) on 6.1% of tenants (confirmed: sleekr 404s), distinct from a working feed that
genuinely reports zero current openings (confirmed 200s with an empty channel:
grassrootsvoter, knowingtechnologies — 2 more boards the run's own raw counts include, both
correctly reclassified out of "unreachable" once ``fetch_via_feed`` learned to tell the two
apart) — so it can't unconditionally replace the path above yet. ``fetch_via_feed``
(below) exposes it as a second, independent entry
point so ``scripts/eval/trakstar_feed_compare.py`` can call both paths on the same boards and
compare their real output at scale before any cutover — it is deliberately not wired into
``fetch_raw()``/``parse()`` yet, and a normal scrape run still uses only the path above.
"""

from __future__ import annotations

import email.utils
import html as _html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from headstart import http, log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)

#: The careers page renders at most this many job cards. Documented and measured in the
#: module docstring above; a Board landing exactly on it is very likely truncated.
_CARD_CAP = 25

_ITEM = "js-careers-page-job-list-item"
_CODE = re.compile(r'data-href="/jobs/([^/"]+)/?"')
_TITLE = re.compile(r'js-job-list-opening-name[^>]*\btitle="([^"]*)"')
_LOC = re.compile(r'js-job-list-opening-loc[^>]*\btitle="([^"]*)"')
# the card also renders the department in a bare rb-text-4 div and the employment type in
# the opening-meta span next to it — both sit in the same block, just unread until now.
_DEPT = re.compile(r'"rb-text-4">\s*([^<]+?)\s*</div>')
_EMPTYPE = re.compile(r"js-job-list-opening-meta[^>]*>\s*<span>\s*([^<]+?)\s*</span>")
_JSONLD = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
# the rendered description container present on every detail page template, JSON-LD or not
_DESC_DIV = re.compile(r'<div class="jobdesciption">', re.IGNORECASE)
_DIV_TAG = re.compile(r"<div\b|</div\s*>", re.IGNORECASE)
_DETAIL_WORKERS = 4  # detail pages sit behind DataDome — keep the concurrency gentle

# The RSS feed's own XML namespace for its job:* elements (locationCity/State/Country, team,
# positionType) — confirmed via the feed's own <channel xmlns:job="..."> declaration, not
# guessed.
_FEED_NS = {"job": "https://recruiterbox.com/rss/job/"}
_FEED_CODE = re.compile(r"/jobs/([^/]+?)/?$")
# The feed's own <description> embeds a duplicate "Location: ..." line (<h2 id="job_meta">)
# before the real content and an "Apply to this job" link (<div id="how_to_apply">) after —
# isolate just the real container, confirmed present across every tenant checked (exotel,
# dripcapital, hazelhawkins, colcare, 2026-08-22).
_FEED_DESC_DIV = re.compile(r'<div id="job_description">', re.IGNORECASE)
# Evidence: every job:positionType value seen across a 989-item, 50-board live sample
# (2026-08-22) — full_time (942), part_time (24), contract (23), the rest unset. No other value
# observed; left unmapped (None) rather than guessed at.
_FEED_POSITION_TYPE = {
    "full_time": "Full-time",
    "part_time": "Part-time",
    "contract": "Contract",
}


class TrakstarScraper(BaseScraper):
    ats = "trakstar"
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://{self.slug}.hire.trakstar.com/"

    def fetch_raw(self) -> Any:
        html = self._get()  # the careers page HTML (job cards)
        codes = _codes_from(html)
        if len(codes) >= _CARD_CAP:
            # The known cap was visible only by running scripts/eval/trakstar_feed_compare.py by
            # hand, so a run never said which Boards hit it and the cost could not be tracked.
            #
            # `>=`, not `==`: the day Trakstar raises the cap, `==` would silently stop warning on
            # every truncated Board, which is the failure mode this line exists to prevent.
            #
            # Deliberately not mark_truncated: landing on the cap is strong evidence, not proof.
            # (The docstring's 5.4% counts boards landing on exactly 25, via
            # trakstar_feed_compare.py's own `html_job_count == 25`. That measures how often the
            # cap is *reached*, not that reaching it always means truncation — exotel's 25-vs-45
            # feed comparison is the evidence for that.) ADR-0053 exclusion has no drain, so a
            # wrong call there is permanent.
            _log.warning(
                f"{self.board_key()}: {len(codes)} cards, at or over the {_CARD_CAP}-card render cap, so "
                "this Board is probably short; its RSS feed carries the full list"
            )
        # Each job page's JSON-LD JobPosting (description + datePosted), fetched concurrently
        # (bounded); failures -> None. The detail pages sit behind DataDome, so the async path
        # pins the multiplexing width to the gentle _DETAIL_WORKERS rather than the global
        # HEADSTART_H2_STREAMS.
        if self.async_fanout_enabled():
            results = self.fan_out_async(
                codes,
                lambda session, code: self._job_posting_async(session, code),
                concurrency=_DETAIL_WORKERS,
            )
        else:
            results = self.fan_out(codes, self._job_posting, workers=_DETAIL_WORKERS)
        self.report_detail_gaps(results, "JSON-LD postings")
        postings = dict(zip(codes, results))
        return {"html": html, "postings": postings}

    def fetch_via_feed(self, scraped_at: str) -> list[Job] | None:
        """Investigative alternate path, not yet wired into ``fetch_raw()``/``parse()`` — see
        the module docstring's "Known gap" note. One request to the tenant's RSS feed
        (``/jobfeeds/{slug}``) returns every job with its full description already inline, no
        per-job detail fetch and no careers-page render cap. Returns ``None`` only when the feed
        itself is unreachable (404, network error, or a 200 body that doesn't parse as XML) so a
        caller can fall back to ``fetch_raw()``/``parse()``. A working feed reporting zero
        current openings is a real, different result — an empty list, not ``None`` — confirmed
        live: `grassrootsvoter`/`knowingtechnologies` are genuine 200s with an empty
        ``<channel>``, not 404s like `sleekr`."""
        xml_text = _fetch_feed(self.slug)
        if xml_text is None:
            return None
        items = _feed_items(xml_text)
        if items is None:
            return None
        return _jobs_from_feed(self.ats, self.slug, self.company, items, scraped_at)

    def _detail_url(self, code: str) -> str:
        return f"https://{self.slug}.hire.trakstar.com/jobs/{code}/"

    @staticmethod
    def _extract_posting(response: Any) -> dict | None:
        """Pull the JobPosting JSON-LD block from a detail page (None on non-200), falling
        back to the rendered description container when a tenant's template never emits
        JSON-LD at all (#179)."""
        if response.status_code != 200:
            return None
        posting = _jsonld_posting(response.text)
        if posting is not None:
            return posting
        description = _html_description(response.text)
        return {"description": description} if description is not None else None

    def _job_posting(self, code: str) -> dict | None:
        """GET one job page and return its JSON-LD JobPosting (None on failure). Sync path."""
        try:
            response = http.fetch(
                "GET",
                self._detail_url(code),
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
        except http.RequestsError:
            return None  # a missing posting must not drop the job
        return self._extract_posting(response)

    async def _job_posting_async(self, session: Any, code: str) -> dict | None:
        """Same as :meth:`_job_posting` but over the shared multiplexed ``AsyncSession``."""
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(code),
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
        except http.RequestsError:
            return None
        return self._extract_posting(response)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        html = raw["html"] if isinstance(raw, dict) else raw
        postings = raw.get("postings", {}) if isinstance(raw, dict) else {}
        jobs: list[Job] = []
        for block in html.split(_ITEM)[1:]:
            code = _CODE.search(block)
            if not code:
                continue
            title = _TITLE.search(block)
            loc = _LOC.search(block)
            dept = _DEPT.search(block)
            emp = _EMPTYPE.search(block)
            location = _html.unescape(loc.group(1)).strip() if loc else None
            posting = postings.get(code.group(1)) or {}
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{code.group(1)}",
                    ats=self.ats,
                    company=self.company,
                    title=_html.unescape(title.group(1)).strip() if title else "",
                    location=location,
                    remote=is_remote(location),
                    department=_html.unescape(dept.group(1)).strip() if dept else None,
                    url=f"https://{self.slug}.hire.trakstar.com/jobs/{code.group(1)}/",
                    # the listing card has no date; the detail JSON-LD does
                    posted_at=posting.get("datePosted"),
                    scraped_at=scraped_at,
                    employment_type=_html.unescape(emp.group(1)).strip()
                    if emp
                    else None,
                    description=html_to_text(posting.get("description")),
                )
            )
        return jobs


def _codes_from(html: str) -> list[str]:
    """Every job code on a careers-page listing, in the order the cards appear. Shared by
    ``fetch_raw()`` and the sampling script's own bounded adapter (``_fetch_trakstar``,
    ``scripts/enrich/salary_sample.py``) so the two don't carry two copies of the same
    card-splitting logic — the same reuse ``_fetch_successfactors`` already gets from this
    module's ``_job_urls_from``-equivalent, ``successfactors.py``'s own module-level helper."""
    return [m.group(1) for block in html.split(_ITEM)[1:] if (m := _CODE.search(block))]


def _jsonld_posting(html: str) -> dict | None:
    """Pull the JobPosting object out of a detail page's schema.org JSON-LD block."""
    for match in _JSONLD.finditer(html):
        try:
            # strict=False: the JSON-LD embeds literal newlines inside string values
            data = json.loads(match.group(1), strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _isolate_div(html: str, opening_div: re.Pattern) -> str | None:
    """Inner HTML of the first div matching ``opening_div`` (None if it's missing). Open/close
    tag counting, not a non-greedy regex, because the container's content is sometimes itself
    wrapped in a nested ``<div>`` that a naive ``.*?`` match would truncate at. Shared by
    :func:`_html_description` (the detail page's ``.jobdesciption`` container) and
    :func:`_feed_description` (a feed item's ``id="job_description"`` container) — same
    technique, two different opening patterns, both within this one module.

    ``successfactors.py``'s ``_matched_content`` does the same open/close counting for a
    different tag in a different scraper; not shared with THAT one, on purpose (CLAUDE.md: no
    cross-scraper abstraction for one more caller) — flagging the resemblance rather than
    silently duplicating it unnoted."""
    match = opening_div.search(html)
    if not match:
        return None
    depth = 1
    for tag in _DIV_TAG.finditer(html, match.end()):
        depth += -1 if tag.group(0).startswith("</") else 1
        if depth == 0:
            return html[match.end() : tag.start()]
    return None


def _html_description(html: str) -> str | None:
    """Raw inner HTML of the ``.jobdesciption`` container. ``html_to_text`` (called by
    ``parse``) strips the markup and turns an empty match into None, so a present-but-blank
    container (a job with no real description body) still ends up None rather than ""."""
    return _isolate_div(html, _DESC_DIV)


def _fetch_feed(slug: str) -> str | None:
    """GET the tenant's RSS job feed. Reached through plain ``http.fetch``, not ``curl_cffi``:
    unlike the per-job detail pages, it isn't behind DataDome (confirmed live, 0 errors across
    148 sampled boards, 2026-08-22). Returns ``None`` on any non-200 (most commonly a 404 — the
    feed isn't offered for every tenant, confirmed: sleekr) so a caller treats it as "fall back
    to the HTML+JSON-LD path". A 200 with an empty channel (confirmed: grassrootsvoter,
    knowingtechnologies) is NOT this case — it's real feed text, still returned here; the "no
    jobs" vs. "no feed" distinction is made one layer up, in :func:`_feed_items`/
    ``fetch_via_feed``, never collapsed into a single ``None`` at this layer."""
    try:
        response = http.fetch(
            "GET",
            f"https://{slug}.hire.trakstar.com/jobfeeds/{slug}",
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
    except http.RequestsError:
        return None
    if response.status_code != 200:
        return None
    return response.text


def _feed_description(description_field: str) -> str | None:
    """Isolate the ``<div id="job_description">`` container's inner HTML out of a feed item's
    full ``<description>`` field, which also carries a duplicate "Location: ..." line before it
    and an "Apply to this job" link after (see the module docstring) — both excluded by only
    ever returning the one container's own content."""
    return _isolate_div(description_field, _FEED_DESC_DIV)


def _feed_posted_at(pub_date: str | None) -> str | None:
    """RFC-822 (the feed's own ``pubDate`` format, e.g. "Fri, 21 Aug 2026 00:00:00 +0530") to a
    plain ISO date, matching the JSON-LD path's own ``datePosted`` convention. ``None`` on
    anything unparseable rather than raising — a malformed date shouldn't drop the job."""
    if not pub_date:
        return None
    try:
        return email.utils.parsedate_to_datetime(pub_date).date().isoformat()
    except (TypeError, ValueError):
        return None


def _feed_items(xml_text: str) -> list[dict] | None:
    """Parse an RSS feed's ``<item>`` elements into plain dicts, one per posting. ``None`` only
    if the XML itself doesn't parse (shouldn't happen for a 200 response, but a caller must be
    able to fall back rather than crash on a malformed feed) — an empty, well-formed channel
    (confirmed live: grassrootsvoter, knowingtechnologies) is a real, different result and
    returns ``[]``, not ``None``; callers must check ``is None`` specifically, never falsy."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    items = []
    for item in root.iter("item"):
        code_m = _FEED_CODE.search(item.findtext("link") or "")
        if not code_m:
            continue
        location = ", ".join(
            part
            for part in (
                item.findtext("job:locationCity", default="", namespaces=_FEED_NS),
                item.findtext("job:locationState", default="", namespaces=_FEED_NS),
                item.findtext("job:locationCountry", default="", namespaces=_FEED_NS),
            )
            if part
        )
        items.append(
            {
                "code": code_m.group(1),
                "title": (item.findtext("title") or "").strip(),
                "location": location or None,
                "description": _feed_description(item.findtext("description") or ""),
                "posted_at": _feed_posted_at(item.findtext("pubDate")),
                "department": (
                    item.findtext("job:team", default="", namespaces=_FEED_NS).strip()
                    or None
                ),
                "employment_type": _FEED_POSITION_TYPE.get(
                    item.findtext("job:positionType", default="", namespaces=_FEED_NS)
                ),
            }
        )
    return items


def _jobs_from_feed(
    ats: str, slug: str, company: str | None, items: list[dict], scraped_at: str
) -> list[Job]:
    """Build ``Job``s directly from :func:`_feed_items`' output — every field the HTML-card +
    JSON-LD-detail path assembles across two fetches, from one. A free function (not a method)
    so it's testable against a plain items list with no live scraper instance needed."""
    return [
        Job(
            id=f"{ats}:{slug}:{item['code']}",
            ats=ats,
            company=company,
            title=item["title"],
            location=item["location"],
            remote=is_remote(item["location"]),
            department=item["department"],
            url=f"https://{slug}.hire.trakstar.com/jobs/{item['code']}/",
            posted_at=item["posted_at"],
            scraped_at=scraped_at,
            employment_type=item["employment_type"],
            description=html_to_text(item["description"]),
        )
        for item in items
    ]
