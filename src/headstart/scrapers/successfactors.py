"""SuccessFactors RMK scraper (career sites on customer vanity domains: jobs.sap.com,
careers.wipro.com, jobsearch.alstom.com, ...).

RMK ("Recruiting Marketing") is the crawlable SEO surface most SuccessFactors customers put in
front of the modern CSB job search, whose DWR POST-RPC we deliberately don't touch — CSB-only
tenants (Ericsson-class) are a known gap. A tenant's ``slug`` is its vanity host. Three listing
surfaces exist, tried cheapest-first (all probed live 2026-07-21;
experiment/ats-provider-expansion/artifacts/research_successfactors.md + 2026-07-21_rmk_board_probe*.csv):

1. ``/sitemap.xml`` as a **urlset** of ``/job/{slug}/{id}/`` URLs — most tenants; one compact
   GET enumerates the whole board.
2. ``/sitemap.xml`` as the **Google-jobs RSS feed** — a minority (SAP, Alstom, Voith, ...). The
   feed carries full descriptions but its generator trickles at ~30 KB/s, so it is never read
   whole up-front; these tenants list via the server-rendered ``/search/?startrow=N`` pages
   instead (page size varies per tenant — 10 to 100 rows, measured across sampled boards.
   Pagination steps by the size of the page it got and stops when a page adds no new ids, which
   also guards against offset wrap-around; it then checks what it read against the total the
   board advertises, because running off the end and stopping two-thirds of the way through
   otherwise look identical).
3. The patient full RSS stream — last resort for RSS tenants whose ``/search/`` is CSB-rendered
   and lists nothing (Voith, Tetra Pak). Read with a long timeout, keeping whatever arrived if
   the tenant's own generator aborts mid-feed (Voith's dies ~2 MB in): partial beats none.

The list surfaces carry no indexable fields, so a bounded detail pass fetches every job page and
extracts fields from whichever markup that tenant serves: classic RMK pages embed a JSON-LD
``JobPosting`` (title, datePosted, jobLocation, employmentType, description); CSB-rendered pages
(Wipro, Voith) have no JSON-LD but keep schema.org microdata (``itemprop="title"`` /
``"description"``), ``og:title``, a ``<title>`` of the form "{Job Title} Job Details | {Co}", and
per-tenant ``joblayouttoken`` label/value spans (City / State/Province / Posting Start Date) —
each field falls back independently, since tenants mix the shapes. A page that yields no title
drops that job for the run (there is nothing to keep it by); it returns next scrape.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from typing import Any

from headstart import http, log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)


_CLASSIFY_BYTES = 64 * 1024  # enough sitemap head to tell urlset from RSS
_SITEMAP_CAP = 30 * 1024 * 1024  # runaway guard; largest observed urlset is ~3 MB
_RSS_TIMEOUT = 300  # the RSS generator trickles (~30 KB/s); a full feed is minutes
_MAX_SEARCH_PAGES = 400  # loop bound; 4,000 rows at the smallest measured page (10)
_DETAIL_WORKERS = 6  # sync-path detail fetches; bounded since they hit one host

# ``/job/{slug}/{id}/`` — the one URL shape all three listing surfaces share. The slug part may
# span %-escapes and XML entities; the trailing numeric segment is the stable posting id.
_JOB_PATH = re.compile(r"(/job/[^\s\"'<>?#]+/(\d+)/)")

_LD_BLOCK = re.compile(
    r'<script type="application/ld\+json">\s*(.*?)\s*</script>', re.DOTALL
)
_ITEMPROP_TITLE = re.compile(r'<[^>]*itemprop="title"[^>]*>([^<]*)')
_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]*)"')
_TITLE_TAG = re.compile(r"<title>([^<|]*)", re.IGNORECASE)
_DESC_OPEN = re.compile(
    r'<(span|div)\b[^>]*itemprop="description"[^>]*>', re.IGNORECASE
)

# Vanity-host labels that are the board, not the company: jobs.sap.com -> "sap".
_BOARD_HOST_LABELS = {"jobs", "careers", "career", "jobsearch", "jobdetails"}


class SuccessFactorsScraper(BaseScraper):
    """SuccessFactors RMK scraper — ``slug`` is the board's vanity host."""

    ats = "successfactors"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    # Where SAP parks a decommissioned RMK tenant (ADR-0111). Both spellings observed live.
    alias_vendor_hosts = frozenset({"www.sap.com", "sap.com"})

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        # The ledger only knows the host, so a missing display name derives from it.
        if self.company == self.slug:
            labels = self.slug.split(".")
            if labels[0] in _BOARD_HOST_LABELS and len(labels) > 2:
                self.company = labels[1]
            else:
                self.company = labels[0]

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def _fetch_sitemap(self) -> tuple[str, str, str | None]:
        """GET ``/sitemap.xml`` streamed. Returns ``(kind, text, cut_short)`` with kind "urlset" |
        "rss" | "other". A urlset is read to the end (compact); an RSS feed is abandoned right
        after classification so the trickling generator never stalls the fetch. A torn urlset read
        raises — a silent partial board would drop jobs.

        ``cut_short`` is why the read stopped early, or None when the document arrived whole: a
        urlset cut at ``_SITEMAP_CAP`` lists only the jobs that fit. Reported, not recorded, for
        the same reason :meth:`_search_job_urls` reports — ``fetch_raw`` decides which surface is
        the Board's answer (ADR-0053)."""
        # Through the retry seam, not the raw session: a 429/5xx here used to settle on the
        # first try, and `_fetch_sitemap` maps a non-200 to ("other", "", None) — so a throttled
        # fetch read as an empty Board and `index sync` evicted its rows (ADR-0047, ADR-0053).
        response = http.fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            stream=True,
        )
        chunks: list[bytes] = []
        size = 0
        kind = ""
        capped = False
        try:
            for chunk in response.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _SITEMAP_CAP:
                    capped = True
                    break
                if not kind and size >= _CLASSIFY_BYTES:
                    kind = _sitemap_kind(b"".join(chunks).decode("utf-8", "replace"))
                    if kind != "urlset":
                        break
        finally:
            response.close()
        if response.status_code != 200:
            return "other", "", None
        text = b"".join(chunks).decode("utf-8", "replace")
        return (
            kind or _sitemap_kind(text),
            text,
            _cap_reason("sitemap") if capped else None,
        )

    def _search_job_urls(self) -> tuple[list[tuple[str, str]], str | None]:
        """Enumerate the board via the server-rendered ``/search/`` pages.

        Returns the pairs found and, when the walk was cut short rather than reaching the end,
        why. Reported rather than recorded, because whether it matters is the caller's to decide:
        this surface is only the Board's answer when it returns something, and a truncation on a
        surface that lost the fallback race must not be attached to the list that won it
        (ADR-0053)."""
        seen: dict[str, str] = {}  # id -> url, insertion-ordered
        page_size: int | None = (
            None  # both from the board's own pagination label, when it
        )
        advertised_total: int | None = (
            None  # renders one; None leaves the walk as it was
        )
        startrow = 0
        for page_index in range(_MAX_SEARCH_PAGES):
            response = http.fetch(
                "GET",
                f"https://{self.slug}/search/?startrow={startrow}",
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            if response.status_code != 200:
                # Unlike the empty-page exit below, this is the walk being cut short rather than
                # reaching the end: whatever sits past this offset is unread, not absent
                # (ADR-0053). No total to compare against here, so report the offset instead.
                return [(u, i) for i, u in seen.items()], (
                    f"HTTP {response.status_code} at startrow {startrow} — "
                    f"{len(seen)} postings read before the walk stopped"
                )
            if page_index == 0:
                paging = _advertised_paging(response.text)
                if paging:
                    page_size, advertised_total = paging
            found = _job_urls_from(response.text, self.slug)
            fresh = [(u, i) for u, i in found if i not in seen]
            if not fresh:
                break
            seen.update({i: u for u, i in fresh})
            # Step by the board's own stated page size, falling back to the links we counted.
            # A fixed 25-row floor here skipped the difference on every tenant serving fewer
            # rows than that, silently, for months (docs/pipeline/2026-08-23_false-board-
            # eviction-root-cause.md §4.2). The link count is the better guess but still only a
            # guess: not every /job/ link on a page is one of that page's results, and
            # `jobs.kaufland.com` renders 19 for a stated 15, so stepping by what we counted
            # would skip 4 rows of every window — the same bug in a new disguise. An empty page
            # cannot loop here: `fresh` is then empty and the walk breaks above, which is what
            # the floor was really guarding against.
            startrow += page_size or len(found)
        else:
            # Ran out of pages rather than reaching the end. Eightfold and Workday both mark
            # their equivalent ceilings; this one returned None and the short list read as the
            # whole Board (ADR-0053).
            return [(u, i) for i, u in seen.items()], (
                f"hit the {_MAX_SEARCH_PAGES}-page search ceiling at startrow {startrow} — "
                f"{len(seen)} postings read, the rest unread"
            )
        # Reaching the natural end is not proof the walk read everything — the stride bug above
        # exited by exactly this path for months. The board states its own total on the search
        # page, so compare against it and report a shortfall rather than presenting a short list
        # as the whole Board (ADR-0053). Only when the label parses, which is far from universal:
        # 17 of 30 sampled tenants render one, and the rest must walk exactly as before and claim
        # nothing. So this is a second line of defence over roughly half the estate, not the fix
        # — the stride above is the fix, and it applies to every board.
        #
        # `len(seen)` counts links, so a board rendering extras beyond its results (kaufland
        # again) compares slightly high and can mask a shortfall smaller than the extras. That is
        # the safe direction and deliberately left alone: under-reporting costs one run's
        # eviction, while over-reporting marks the Board unauthoritative and serves its closed
        # postings indefinitely.
        if advertised_total is not None and len(seen) < advertised_total:
            return [(u, i) for i, u in seen.items()], (
                f"read {len(seen)} of the {advertised_total} postings the board advertises — "
                "the rest were not listed"
            )
        return [(u, i) for i, u in seen.items()], None

    def _rss_job_urls(self) -> tuple[list[tuple[str, str]], str | None]:
        """Enumerate the board from the full RSS feed, patiently; keeps whatever arrived when
        the tenant's generator aborts mid-feed.

        Returns the pairs found and, when the stream ended early rather than completing, why —
        an aborted feed and a feed cut at ``_SITEMAP_CAP`` both list a knowingly short board.
        Reported rather than recorded for the same reason :meth:`_search_job_urls` reports
        (ADR-0053)."""
        response = http.fetch(  # retry seam, as in `_fetch_sitemap`
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=_RSS_TIMEOUT,
            stream=True,
        )
        chunks: list[bytes] = []
        size = 0
        cut_short: str | None = None
        try:
            for chunk in response.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _SITEMAP_CAP:
                    cut_short = _cap_reason("RSS feed")
                    break
        except http.RequestsError:
            # Server-side abort: scrape the links that did arrive, and say so. No total to
            # compare against in a feed, so report how far it got instead.
            cut_short = (
                f"the tenant's RSS feed aborted {size:,} bytes in — "
                "postings past that point were not listed"
            )
        finally:
            response.close()
        if response.status_code != 200:
            return [], None
        return (
            _job_urls_from(b"".join(chunks).decode("utf-8", "replace"), self.slug),
            cut_short,
        )

    def fetch_raw(self) -> Any:
        # Each of the three surfaces hands back *why* its list came up short, and the truncation
        # is recorded only in the branch where that surface is what the Board returns. A surface
        # that lost the fallback race can list nothing at all (a search walk that 503s on its
        # first page), and the surface that then answers may answer with the whole board — which
        # must never inherit the loser's truncation (ADR-0053).
        kind, text, sitemap_cut_short = self._fetch_sitemap()
        listed = _job_urls_from(text, self.slug) if kind == "urlset" else []
        surface = "sitemap-urlset" if listed else ""
        if listed and sitemap_cut_short:
            self.mark_truncated(sitemap_cut_short)
        if not listed:
            listed, search_cut_short = self._search_job_urls()
            if listed:
                surface = "search-pages"
                if search_cut_short:
                    self.mark_truncated(search_cut_short)
        if not listed and kind == "rss":
            listed, rss_cut_short = self._rss_job_urls()
            if listed:
                surface = "rss-stream"
                if rss_cut_short:
                    self.mark_truncated(rss_cut_short)
        # NB: all three surfaces empty is indistinguishable here from a dead vanity host, since
        # each maps its own non-200 to "nothing" and falls through — so a gone SuccessFactors
        # tenant cannot currently earn an ADR-0058 gone-verdict. A root-of-host probe was tried
        # and rejected on measurement: of 12 hosts this ledger already calls dead, 9 answer
        # `GET /` with 200 (the jobs2web parking page), so the probe would have cost one extra
        # request per empty board per run and still missed three quarters of the dead ones. The
        # real fix is for the three surfaces to distinguish "errored" from "legitimately empty"
        # and raise only when every one of them errored.
        # Which of the three surfaces answered, and how much the detail pass will cost. This
        # tenant's cost is decided here and nowhere else — the RSS stream is the patient last
        # resort — so without this line a board that takes 37 minutes for 7 jobs
        # (cbscorporation.jobs, 2026-08-12) leaves no evidence of why.
        _log.info(
            f"{self.slug}: {surface or 'nothing'} via sitemap {kind or 'unknown'} "
            f"-> {len(listed)} job pages to fetch"
        )
        # Detail pass: every field comes from the job page, so fetch each one (bounded); a
        # failed fetch leaves fields None and parse drops just that job.
        if self.async_fanout_enabled():
            fields = self.fan_out_async(
                listed,
                lambda session, pair: self._job_fields_async(session, pair[0]),
            )
        else:
            fields = self.fan_out(
                listed,
                lambda pair: self._job_fields(pair[0]),
                workers=_DETAIL_WORKERS,
            )
        lost = self.report_detail_gaps(fields, "detail fields")
        if lost:
            # Every field comes from the job page, so `parse` drops a Job whose page did not
            # arrive. That makes the returned list knowingly short, and an unmarked short list
            # is exactly what `index sync` reads as a delisting — it would evict Jobs that are
            # still posted, purely because their detail fetch failed (ADR-0053).
            self.mark_truncated(
                f"{lost}/{len(listed)} job pages unreadable — those Jobs are listed but unbuilt"
            )
        return [
            {"url": url, "id": job_id, "fields": page_fields}
            for (url, job_id), page_fields in zip(listed, fields)
        ]

    def _job_fields(self, url: str) -> dict[str, Any] | None:
        response = http.fetch(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=30
        )
        if response.status_code != 200:
            return None
        return _titled_fields(response.text, url)

    async def _job_fields_async(self, session: Any, url: str) -> dict[str, Any] | None:
        response = await http.fetch_async(
            session, "GET", url, headers={"User-Agent": USER_AGENT}, timeout=30
        )
        if response.status_code != 200:
            return None
        return _titled_fields(response.text, url)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                continue  # page unreadable — nothing to keep the job by
            location = fields.get("location")
            remote = fields.get("remote")
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{item['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=None,
                    url=item["url"],
                    posted_at=fields.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                )
            )
        return jobs


def _cap_reason(what: str) -> str:
    """Why a stream that ran into ``_SITEMAP_CAP`` left the board short (ADR-0053)."""
    return (
        f"the {what} hit the {_SITEMAP_CAP // (1024 * 1024)} MB read cap — "
        "postings past it were not listed"
    )


def _sitemap_kind(text: str) -> str:
    if "base.google.com/ns/1.0" in text or "<rss" in text:
        return "rss"
    if "<urlset" in text:
        return "urlset"
    return "other"


# The pagination label's *structure*, never its words: the surrounding text is localised per
# tenant — "Results 1 – 10 of 219", "Ergebnisse 1 – 46 von 46", "Resultados 1 – 50 de 813" — and
# matching on "of" reads every non-English board as having no total at all. The shape is stable
# across all of them: inside the label, a bold range followed by a bold grand total.
#
# Matched in two bounded steps rather than one loose pattern. A single `.*?` from the class name
# to a pair of <b> spans is not anchored to the end of the label, so a tenant whose label is some
# *other* shape lets the match run on into unrelated markup and return a number scavenged from
# elsewhere on the page. That direction is the dangerous one — see :func:`_advertised_paging`.
_PAGINATION_LABEL = re.compile(
    r"paginationLabel[^>]*>((?:(?!</span>).)*)</span>", re.IGNORECASE | re.DOTALL
)
_LABEL_FIGURES = re.compile(
    r"<b>\s*([\d,]+)\s*[–—-]\s*([\d,]+)\s*</b>[^<]*<b>\s*([\d,]+)\s*</b>",
    re.IGNORECASE,
)


def _advertised_paging(page: str) -> tuple[int, int] | None:
    """``(rows this page lists, postings the board holds)`` per the ``/search/`` page's own
    pagination label, or None when it carries none this function recognizes.

    The label reads ``Results <b>1 – 10</b> of <b>219</b>`` in English and
    ``Ergebnisse <b>1 – 46</b> von <b>46</b>`` in German, so this matches its shape rather than
    its words. It answers the walk's two questions at once, both of which it otherwise has to
    guess from the page's link count: how far to step, and whether it reached the end.

    Returns None when the label is absent or unrecognised, and the caller then steps by what it
    counted and makes no claim about completeness. That direction matters in both roles: a
    wrongly-parsed total reads as a shortfall, which marks the Board unauthoritative and takes it
    out of the eviction scope entirely (ADR-0053), so closed postings would be served forever.
    An unknown total must never become a zero, which is why the figures are matched only *inside*
    the label element and rejected unless they order sanely.
    """
    label = _PAGINATION_LABEL.search(page)
    if not label:
        return None
    figures = _LABEL_FIGURES.search(label.group(1))
    if not figures:
        return None
    first, last, total = (int(g.replace(",", "")) for g in figures.groups())
    if not 0 < first <= last <= total:
        return None
    return last - first + 1, total


def _job_urls_from(text: str, host: str) -> list[tuple[str, str]]:
    """All ``(absolute url, id)`` job links in a sitemap/feed/search payload, de-duplicated by
    id in first-seen order. Links appear relative (search hrefs) and absolute (sitemap locs);
    both normalize to ``https://{host}{path}``."""
    pairs: dict[str, str] = {}
    for match in _JOB_PATH.finditer(text):
        job_id = match.group(2)
        if job_id not in pairs:
            pairs[job_id] = f"https://{host}{unescape(match.group(1))}"
    return [(url, job_id) for job_id, url in pairs.items()]


def _page_fields(page: str, url: str | None = None) -> dict[str, Any]:
    """Every indexable field a job page yields. JSON-LD first, then the CSB microdata /
    label-token fallbacks — per field, because tenants mix the shapes.

    ``url`` is optional and used only for the location's last-resort tier
    (:func:`_location_from_slug`); every existing caller that has no URL handy keeps working
    unchanged and simply doesn't get that tier."""
    fields = _jsonld_fields(page) or {}
    if not fields.get("title"):
        fields["title"] = _csb_title(page)
    if not fields.get("description"):
        fields["description"] = _csb_description(page)
    if not fields.get("location"):
        fields["location"] = _csb_location(page)
    if not fields.get("location") and url and fields.get("title"):
        fields["location"] = _location_from_slug(fields["title"], url)
    if not fields.get("location"):
        # Last tier, because every tier above yields a place at least as fine. Some tenants'
        # job pages carry no location markup and no location in the URL either, and the only
        # geography anywhere on them is this one meta (measured on careers.theredsea.sa, 51 of
        # 70 residual nulls in a 14-board sample: the page has no JSON-LD, no
        # `careersite-propertyid="location"` and no address itemprops, only a bare "SA").
        #
        # Worth being exact about what this buys, because it is less than it looks. Where the
        # tenant configured a city the value is fully filterable ("Kuala Lumpur, MY, 50450",
        # "Iasi, RO"). Where it is a bare two-letter tag it is NOT: `geo.where("india")` is an
        # set of place *names*, so "IN" matches none of its 213 patterns (verified against
        # the live clause) -- "Karnataka, IN" only ever matched on "karnataka". So the bare-tag
        # rows gain a displayable country and stop being blank; they do not gain a place filter
        # unless one that reads country tags is added later.
        fields["location"] = _location_from_street_address(page)
    if not fields.get("posted_at"):
        fields["posted_at"] = _csb_posted_at(page)
    return fields


def _titled_fields(page: str, url: str | None = None) -> dict[str, Any] | None:
    """:func:`_page_fields`, but None on a page that loaded (200 OK) without a parseable title —
    a temporary placeholder, an anti-bot interstitial served with 200, or any page shape neither
    parser recognizes. `parse()` drops a Job with no title either way (there is nothing to keep
    it by), so a title-less page must count as a loss the same as a fetch failure: `_page_fields`
    alone always returns a dict, never None, so that loss was invisible to `report_detail_gaps`
    and `mark_truncated` never fired — `index sync` read the board as fully, authoritatively
    scraped and evicted the Job as a delisting (docs/pipeline/2026-08-23_false-board-eviction-
    root-cause.md §4)."""
    fields = _page_fields(page, url)
    return fields if fields.get("title") else None


def _jsonld_fields(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a classic RMK page's JSON-LD, or None without one."""
    for match in _LD_BLOCK.finditer(page):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if node_type != "JobPosting" and not (
                isinstance(node_type, list) and "JobPosting" in node_type
            ):
                continue
            employment = node.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(e) for e in employment) or None
            return {
                "title": node.get("title"),
                "description": node.get("description"),
                "location": _jsonld_location(node),
                "posted_at": node.get("datePosted"),
                "employment_type": employment,
                "remote": True
                if node.get("jobLocationType") == "TELECOMMUTE"
                else None,
            }
    return None


def _jsonld_location(node: dict[str, Any]) -> str | None:
    place = node.get("jobLocation")
    if isinstance(place, list):
        place = place[0] if place else None
    if not isinstance(place, dict):
        return None
    address = place.get("address")
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts = [address.get("addressLocality"), address.get("addressRegion"), country]
    joined = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
    return joined or None


def _csb_title(page: str) -> str | None:
    match = _ITEMPROP_TITLE.search(page) or _OG_TITLE.search(page)
    if match and match.group(1).strip():
        return unescape(match.group(1)).strip()
    match = _TITLE_TAG.search(page)
    if not match:
        return None
    # "<title>Lead Data Scientist Job Details | Wipro Limited</title>" -> the job title
    title = re.sub(r"\s*Job Details\b.*$", "", unescape(match.group(1))).strip()
    return title or None


def _csb_description(page: str) -> str | None:
    """The longest ``itemprop="description"`` element's inner HTML (CSB pages render a short
    teaser and the full description under the same itemprop)."""
    best = None
    for match in _DESC_OPEN.finditer(page):
        content = _matched_content(page, match)
        if content and (best is None or len(content) > len(best)):
            best = content
    return best


def _matched_content(page: str, open_match: re.Match) -> str:
    """Inner HTML of the element opened at ``open_match``, by open/close tag counting."""
    tag = open_match.group(1).lower()
    token = re.compile(rf"<{tag}\b|</{tag}\s*>", re.IGNORECASE)
    depth = 1
    for match in token.finditer(page, open_match.end()):
        depth += -1 if match.group(0).startswith("</") else 1
        if depth == 0:
            return page[open_match.end() : match.start()]
    return page[open_match.end() :]


def _label_value(page: str, *labels: str) -> str | None:
    """The value span following the first present ``joblayouttoken`` label."""
    for label in labels:
        match = re.search(
            rf'joblayouttoken-label"[^>]*>\s*{re.escape(label)}\s*</span>\s*<span[^>]*>([^<]*)',
            page,
        )
        if match and match.group(1).strip():
            return unescape(match.group(1)).strip()
    return None


def _meta_itemprop(page: str, prop: str) -> str | None:
    """A ``<meta itemprop="..." content="...">`` microdata value (CSB pages carry the
    JobPosting schema this way instead of JSON-LD)."""
    match = re.search(rf'<meta itemprop="{prop}" content="([^"]*)"', page)
    value = unescape(match.group(1)).strip() if match else ""
    return value or None


def _careersite_prop(page: str, prop: str) -> str | None:
    """The tags-stripped text of a ``data-careersite-propertyid="{prop}"`` element — RMK's
    canonical single-field value. It wins over the label spans because many boards wrap the value
    in a nested element (``<span ...><p id="job-location">Durham, NC, US</p></span>``), where a
    plain label-value regex captures only the whitespace before the nested tag."""
    match = re.search(
        rf'data-careersite-propertyid="{prop}"[^>]*>(.*?)</span>', page, re.DOTALL
    )
    if not match:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()
    return unescape(text) or None


def _csb_location(page: str) -> str | None:
    # The single ``location`` property (tags stripped) is the most reliable when present — it
    # holds the whole "City, Region, Country" even when wrapped in nested markup.
    prop = _careersite_prop(page, "location")
    if prop:
        return prop
    # else assemble from the city/state/country label spans (full names like "Sikkim, India"),
    # falling back field-by-field to the microdata metas (which hold truncated "Sikk"/"In").
    parts = [
        _label_value(page, "City:", "Location:")
        or _meta_itemprop(page, "addressLocality"),
        _label_value(page, "State/Province:", "State:")
        or _meta_itemprop(page, "addressRegion"),
        _label_value(page, "Country/Region:", "Country:")
        or _meta_itemprop(page, "addressCountry"),
    ]
    return ", ".join(p for p in parts if p) or None


# `[^\W_]+` rather than `\w+`: `\w` includes `_`, and SuccessFactors's own slug encoder uses `_`
# as its own separator (a literal "." in a title becomes "_" — measured on
# tuyendung.vietcombank.com.vn's "[II.2026_Nam ...]" titles), so keeping it as a token character
# would glue two real words together instead of splitting them.
_SLUG_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _location_from_street_address(page: str) -> str | None:
    """The ``streetAddress`` microdata, as a location, with the tenant's own junk dropped.

    Despite the name SuccessFactors puts a place here, at whatever grain the tenant configured —
    ``Kuala Lumpur, MY, 50450`` and ``Iasi, RO`` and bare ``SG`` all observed on live pages. It
    is the only geography left on tenants whose pages render no location markup and whose job
    URLs carry no location either, which is why it earns a tier at all.

    The one guard is measured, not defensive: a tenant's source data can leak a URL into the
    field (``careers.wataniaind.com`` serves ``content="SA, https://ma"``, its own job titles
    carrying the same fragment — 1 of 12 non-empty values in a 22-tenant sample). Segments
    holding a scheme are dropped rather than the whole value, so that page still yields ``SA``.
    """
    raw = _meta_itemprop(page, "streetAddress")
    if not raw:
        return None
    kept = [s.strip() for s in raw.split(",") if s.strip() and "://" not in s]
    return ", ".join(kept) or None


def _location_from_slug(title: str, url: str) -> str | None:
    """The posting's location recovered from its own URL slug, when the page carries no location
    markup at all — some CSB tenants' job pages genuinely never render one (measured 2026-08-24,
    30-board sample: 29.9% of jobs null, 62.5% of those on tenants where the page has neither
    JSON-LD nor CSB markup, only title/description). The slug itself still carries it:
    SuccessFactors builds job URLs as ``{location}-{title}[-{state}-{zip}]/{id}/`` — e.g.
    ``/job/Charlotte-Account-Manager-Customer-Development-NC-28277/1407690100/`` for a posting
    titled "Account Manager - Customer Development".

    Anchored on the title, which is already reliably extracted: tokenize both into words, then
    require the title's words, concatenated, to appear in the slug's words concatenated, starting
    exactly where a slug token starts. Whatever comes before that match is the location. Anything AFTER it is never used — that is
    where a trailing requisition id lives (``.../Foshan-City-Sr-Technician-528513/...`` for a
    title of just "Sr Technician" leaves a bare ``528513``, not a place), and appending it would
    fabricate a location worse than reporting none. This costs precision on US-style postings
    that do carry a real ``-NC-28277`` state/zip suffix — those are simply reported at city grain
    — but a location that is always genuinely a place beats one that occasionally isn't. It also
    costs the original punctuation: words are joined with plain spaces, so a multi-part prefix
    like "Gaoming District, Foshan City" (comma in the source) comes back as "Gaoming District
    Foshan City" — recovering which gap was a comma would mean guessing, so this doesn't.

    Returns None whenever the title cannot be found that way (title-cased differently than the
    slug encodes it, a title containing a literal "/" — which decodes before the path is split
    on "/" and shifts every segment after it, so the id/slug split below no longer lines up — or
    a tenant whose slug is the title with no location component at all — confirmed on
    careers.ijm.com, whose job URLs are the bare title verbatim) rather than guess from a partial
    match. Punctuation the encoder dropped mid-title is NO LONGER a None case: that is exactly
    what the concatenated match below exists to span.
    """
    from urllib.parse import unquote, urlparse

    path = unquote(urlparse(url).path)
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    # the id is its own trailing segment (`_JOB_PATH`'s own shape); the slug is the one before it
    slug = (
        segments[-2] if len(segments) >= 2 and segments[-1].isdigit() else segments[-1]
    )
    slug_words = _SLUG_WORD.findall(slug)
    title_words = _SLUG_WORD.findall(title)
    if not slug_words or not title_words:
        return None
    # Matched on the *concatenated* lowercase words rather than the token sequence, because
    # SuccessFactors's slug encoder drops punctuation without putting a separator back, gluing
    # two title words into one slug token: "Werkstudent*in" -> "Werkstudentin",
    # "Projektcontroller/Finance" -> "ProjektcontrollerFinance", "(m/w/d)" -> "(mwd)". A
    # token-sequence comparison can never span that glue, so every such posting fell through to
    # no location at all even though its slug plainly carries one (measured on jobs.dkb.de:
    # 13 of 13 nulls were this, all recoverable as "Berlin", "Gera", "FrankfurtOder").
    #
    # Anchoring BOTH ends to token boundaries is what keeps this as strict as the sequence match
    # it replaces — the title may only ever consume whole slug tokens. The start anchor is what
    # stops a title matching mid-token and splitting a word off into the location. The end anchor
    # matters for a subtler case found in review: `str.find` takes the *first* occurrence, so a
    # title whose concatenation is a PREFIX of a longer token matches there instead of at its
    # real position and truncates the location — title "Sales Rep" against
    # `Berlin-Salesrepublic-Sales-Rep` yields "Berlin" where the whole prefix is "Berlin
    # Salesrepublic". Rejecting that occurrence is not enough on its own, since the right match
    # is further along, so the scan below walks occurrences until one lines up at both ends.
    # Measured over 45 live Boards / 2,287 jobs the two anchorings are
    # indistinguishable (same 1,882, same 405 gained, 0 changed, 0 lost, 0 truncations seen), so
    # the end anchor costs nothing real and closes a class that is demonstrably reachable.
    slug_lower = [w.lower() for w in slug_words]
    title_lower = [w.lower() for w in title_words]
    # character offsets at which slug tokens start and end, within the concatenated form
    token_starts: dict[int, int] = {}
    token_ends: set[int] = set()
    offset = 0
    for index, word in enumerate(slug_lower):
        token_starts[offset] = index
        offset += len(word)
        token_ends.add(offset)
    needle = "".join(title_lower)
    haystack = "".join(slug_lower)
    # Every occurrence is tried, not just the first: the first may be a longer token that merely
    # starts with the title, and refusing there would throw away a correct match further along.
    offset_of_match = haystack.find(needle)
    while offset_of_match >= 0 and not (
        offset_of_match in token_starts and offset_of_match + len(needle) in token_ends
    ):
        offset_of_match = haystack.find(needle, offset_of_match + 1)
    if offset_of_match < 0:
        return None
    match_at = token_starts[offset_of_match]
    prefix = slug_words[:match_at]
    if not prefix or all(w.isdigit() for w in prefix):
        return None
    return " ".join(prefix)


def _csb_posted_at(page: str) -> str | None:
    # The label span renders per-locale ("6/29/26", "25 Jun 2026", "Jun 25, 2026"); the meta
    # microdata is Java Date.toString, always UTC. First candidate that parses wins.
    candidates = (
        (
            _label_value(page, "Posting Start Date:", "Posting Date:"),
            ("%m/%d/%y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"),
        ),
        (_meta_itemprop(page, "datePosted"), ("%a %b %d %H:%M:%S UTC %Y",)),
    )
    for value, formats in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date().isoformat()  # noqa: DTZ007
            except (TypeError, ValueError):
                continue
    return None
