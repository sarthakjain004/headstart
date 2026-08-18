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
   instead (page size varies per tenant, 25–100 rows — pagination steps by the observed size
   and stops when a page adds no new ids, which also guards against offset wrap-around).
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
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

_USER_AGENT = "headstart/0.1 (job-board reader)"

_CLASSIFY_BYTES = 64 * 1024  # enough sitemap head to tell urlset from RSS
_SITEMAP_CAP = 30 * 1024 * 1024  # runaway guard; largest observed urlset is ~3 MB
_RSS_TIMEOUT = 300  # the RSS generator trickles (~30 KB/s); a full feed is minutes
_SEARCH_STEP_FLOOR = 25  # the smallest observed /search/ page size
_MAX_SEARCH_PAGES = 400  # loop bound: 400 pages x 25 rows covers any real board
_DETAIL_WORKERS = 6  # sync-path detail fetches; bounded since they hit one host

# ``/job/{slug}/{id}/`` — the one URL shape all three listing surfaces share. The slug part may
# span %-escapes and XML entities; the trailing numeric segment is the stable posting id.
_JOB_PATH = re.compile(r"(/job/[^\s\"'<>?#]+/(\d+)/)")

_LD_BLOCK = re.compile(
    r'<script type="application/ld\+json">\s*(.*?)\s*</script>', re.S
)
_ITEMPROP_TITLE = re.compile(r'<[^>]*itemprop="title"[^>]*>([^<]*)')
_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]*)"')
_TITLE_TAG = re.compile(r"<title>([^<|]*)", re.I)
_DESC_OPEN = re.compile(r'<(span|div)\b[^>]*itemprop="description"[^>]*>', re.I)

# Vanity-host labels that are the board, not the company: jobs.sap.com -> "sap".
_BOARD_HOST_LABELS = {"jobs", "careers", "career", "jobsearch", "jobdetails"}


class SuccessFactorsScraper(BaseScraper):
    """SuccessFactors RMK scraper — ``slug`` is the board's vanity host."""

    ats = "successfactors"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

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
        response = http.session().request(
            "GET",
            self.url(),
            headers={"User-Agent": _USER_AGENT},
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
        startrow = 0
        for _ in range(_MAX_SEARCH_PAGES):
            response = http.fetch(
                "GET",
                f"https://{self.slug}/search/?startrow={startrow}",
                headers={"User-Agent": _USER_AGENT},
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
            found = _job_urls_from(response.text, self.slug)
            fresh = [(u, i) for u, i in found if i not in seen]
            if not fresh:
                break
            seen.update({i: u for u, i in fresh})
            startrow += max(len(found), _SEARCH_STEP_FLOOR)
        return [(u, i) for i, u in seen.items()], None

    def _rss_job_urls(self) -> tuple[list[tuple[str, str]], str | None]:
        """Enumerate the board from the full RSS feed, patiently; keeps whatever arrived when
        the tenant's generator aborts mid-feed.

        Returns the pairs found and, when the stream ended early rather than completing, why —
        an aborted feed and a feed cut at ``_SITEMAP_CAP`` both list a knowingly short board.
        Reported rather than recorded for the same reason :meth:`_search_job_urls` reports
        (ADR-0053)."""
        response = http.session().request(
            "GET",
            self.url(),
            headers={"User-Agent": _USER_AGENT},
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
        self.report_detail_gaps(fields, "detail fields")
        return [
            {"url": url, "id": job_id, "fields": page_fields}
            for (url, job_id), page_fields in zip(listed, fields)
        ]

    def _job_fields(self, url: str) -> dict[str, Any] | None:
        response = http.fetch(
            "GET", url, headers={"User-Agent": _USER_AGENT}, timeout=30
        )
        if response.status_code != 200:
            return None
        return _page_fields(response.text)

    async def _job_fields_async(self, session: Any, url: str) -> dict[str, Any] | None:
        response = await http.fetch_async(
            session, "GET", url, headers={"User-Agent": _USER_AGENT}, timeout=30
        )
        if response.status_code != 200:
            return None
        return _page_fields(response.text)

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


def _page_fields(page: str) -> dict[str, Any]:
    """Every indexable field a job page yields. JSON-LD first, then the CSB microdata /
    label-token fallbacks — per field, because tenants mix the shapes."""
    fields = _jsonld_fields(page) or {}
    if not fields.get("title"):
        fields["title"] = _csb_title(page)
    if not fields.get("description"):
        fields["description"] = _csb_description(page)
    if not fields.get("location"):
        fields["location"] = _csb_location(page)
    if not fields.get("posted_at"):
        fields["posted_at"] = _csb_posted_at(page)
    return fields


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
    token = re.compile(rf"<{tag}\b|</{tag}\s*>", re.I)
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
        rf'data-careersite-propertyid="{prop}"[^>]*>(.*?)</span>', page, re.S
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
                return datetime.strptime(value, fmt).date().isoformat()
            except (TypeError, ValueError):
                continue
    return None
