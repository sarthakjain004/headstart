"""jobs.apple.com — Apple's own in-house careers system, a Single source scraper (ADR-0139): one
company, one host, never discovered. ``slug`` is fixed to ``jobs.apple.com`` and never varies.

Everything below is measured live 2026-09-11 (full protocol in
``docs/apple/2026-09-11_api-measurement.md``); no code or research was carried over from a third
party for this one.

**The public listing endpoint is a plain, unauthenticated JSON POST — no cookies, no CSRF, no
Referer.** The SPA calls ``POST https://jobs.apple.com/api/v1/search`` with a body of
``{query, filters, page, locale, sort, format}``; an empty ``query``/``filters`` returns the whole
board. Verified with zero cookies and no ``Referer`` header at all: identical ``totalRecords`` and
result set to a request carrying a fresh session cookie. The bundle also wires a same-shaped
``/api/v1/ref/teams`` used by the signed-in profile UI, but that one 401s anonymously — it is not
needed here since an empty ``filters`` object already returns every posting, unfiltered.

**Pagination is fixed at 20 rows/page, 1-indexed, and ends on a short page.** ``totalRecords`` is
accurate on every in-range page (measured stable at 6,083 across 7 calls spanning pages 1-400,
several seconds apart) but resets to **0** on a page past the end — page 305 (the true last page,
6,083 = 304*20+3) returns 3 rows and ``totalRecords: 6083``; page 306 returns 0 rows and
``totalRecords: 0``. So the walk terminates on ``len(batch) < 20``, never on the total going to
zero, and the total from an earlier page is what the truncation check below measures against.

**Every listing row is one of two types, and both are real postings a visitor can apply to** —
this is not a filter to narrow, unlike Oracle's ``siteNumber``. ``REQ`` is a specific requisition
(``id`` already shaped ``{positionId}-{reqSuffix}``, e.g. ``200681917-3715``); ``PIPE`` is an
evergreen "pipeline" role — mostly Retail — that keeps taking applications with no single fixed
opening (``id`` shaped ``PIPE-{positionId}``). Measured over 15 pages (300 rows): 41% REQ, 59%
PIPE. Both render identically on jobs.apple.com's own search page, so both are scraped.

**The listing's own text is not the description.** Every row carries a ``jobSummary`` — a
team-level overview paragraph ("Apple Retail is where the best of Apple comes together...",
"Apple is a place where extraordinary people gather...") that recurs, near-identically opened,
across dozens of postings on the same team (measured: 46 Apple Retail rows across 3 pages all open
with the same 50-character prefix). The posting-specific content — ``description``,
``minimumQualifications``, ``preferredQualifications`` — exists only on the per-job detail
endpoint, ``GET /api/v1/jobDetails/{jobNumber}`` (also unauthenticated, also no Referer needed),
hence the detail pass. ``jobNumber`` is the listing's own ``id`` with a ``PIPE-`` prefix stripped
if present — REQ's ``id`` already has no prefix. An unknown id answers a clean ``HTTP 404``
(``{"error":"jobsite.general.serviceError"}``), unlike Oracle's silent-200-empty-items shape.
``employmentType`` is likewise detail-only (absent from every listing row sampled) and not
guaranteed even there — one of two fixture postings carries it, the other doesn't.

**``homeOffice`` is Apple's own explicit remote flag**, present on both listing and detail and
read directly rather than guessed from the location string — the same precedent Oracle's
``WorkplaceTypeCode`` set.

**No rate limit found.** 8 concurrent detail fetches, zero non-200s (a deliberately small sample —
this is one host, not a multi-tenant ATS, so there is no population of boards to protect).

**Job URL**: ``https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}`` —
verified live: the page 200s and its ``<title>`` carries the posting title.
"""

from __future__ import annotations

import json
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import USER_AGENT, BaseScraper

_SEARCH_URL = "https://jobs.apple.com/api/v1/search"
_LOCALE = "en-us"
#: The API's own fixed page size — every measured page (including the last, short one) confirms
#: it; there is no larger-limit parameter to ask for.
_PAGE_SIZE = 20
#: Safety backstop, not a real ceiling: no cap was found in the API itself (module docstring), and
#: 1,000 pages is 20,000 postings against today's board of 6,083 — headroom for the board to grow,
#: not a bound expected to be reached.
_MAX_PAGES = 1000
_DETAIL_WORKERS = (
    16  # measured clean at concurrency 8; this repo's usual detail-pass width
)
_SEARCH_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}


class AppleScraper(BaseScraper):
    """jobs.apple.com — a Single source scraper (ADR-0139). ``slug`` is fixed to the host,
    never discovered."""

    ats = "apple"
    has_detail_pass = (
        True  # per-Job fetch fills description + employment_type (ADR-0050)
    )
    detail_workers = _DETAIL_WORKERS

    def __init__(self, slug: str, company: str | None = None) -> None:
        # A Single source scraper has exactly one company; hardcoding it is simpler and more
        # reliable than resolve_company()'s title-scrape, which exists for slugs that stand in
        # for an unknown display name (ADR-0114) — a problem this ATS does not have.
        super().__init__(slug, company or "Apple")

    def url(self) -> str:
        return f"https://{self.slug}/{_LOCALE}/search"

    def alias_key(self) -> str | None:
        """ADR-0139: a Single source Board has no sibling tenant to alias against, so the base
        redirect-following default (built for vanity-hostname platforms) has nothing to find
        here — this Board resolves to its own slug."""
        return self.slug

    def _search_page(self, page: int) -> dict[str, Any]:
        response = http.fetch(
            "POST",
            _SEARCH_URL,
            json={
                "query": "",
                "filters": {},
                "page": page,
                "locale": _LOCALE,
                "sort": "newest",
                "format": _SEARCH_FORMAT,
            },
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
            **self._egress(),
        )
        response.raise_for_status()
        return response.json().get("res") or {}

    def _listing(self) -> list[dict]:
        """Page through the search endpoint until a short page ends the walk (module docstring:
        an out-of-range page's ``totalRecords`` resets to 0, so the total cannot be the
        terminator)."""
        items: list[dict] = []
        total = 0
        for page in range(1, _MAX_PAGES + 1):
            res = self._search_page(page)
            batch = res.get("searchResults") or []
            total = res.get("totalRecords") or total
            items.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(items)} postings — the rest unread"
            )
        if total and len(items) < total:
            self.mark_truncated_unless_negligible(
                len(items),
                total,
                f"read {len(items)} of {total} postings — the rest is unread, not absent",
            )
        return items

    def fetch_raw(self) -> Any:
        items = self._listing()
        ids = [i["id"] for i in items if i.get("id") and self.needs_detail(i["id"])]
        if self.async_fanout_enabled():
            fetched = self.fan_out_async(ids, self._detail_async)
        else:
            fetched = self.fan_out(ids, self._detail, workers=self.detail_workers)
        self.report_detail_gaps(fetched, "detail payloads")
        details = {i: d for i, d in zip(ids, fetched) if d}
        return {"searchResults": items, "details": details}

    def _job_number(self, native_id: str) -> str:
        # PIPE's id carries the vendor-type prefix the detail endpoint does not want; REQ's id
        # (already "{positionId}-{reqSuffix}") is the jobNumber verbatim.
        return native_id.removeprefix("PIPE-")

    def _detail_url(self, native_id: str) -> str:
        return f"https://{self.slug}/api/v1/jobDetails/{self._job_number(native_id)}"

    def _detail(self, native_id: str) -> dict | None:
        try:
            body = self._get(self._detail_url(native_id))
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return json.loads(body).get("res")

    async def _detail_async(self, session: Any, native_id: str) -> dict | None:
        try:
            body = await self._get_async(session, self._detail_url(native_id))
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return json.loads(body).get("res")

    def job_url(self, item: dict) -> str:
        return (
            f"https://{self.slug}/{_LOCALE}/details/"
            f"{item.get('positionId')}/{item.get('transformedPostingTitle') or ''}"
        )

    def _location(self, item: dict) -> str | None:
        names = [
            loc.get("name") or loc.get("countryName")
            for loc in item.get("locations") or []
            if loc.get("name") or loc.get("countryName")
        ]
        return "; ".join(names) or None

    def _description(self, detail: dict) -> str | None:
        # jobSummary is the team-level boilerplate (module docstring) — not used here, so the
        # description is only what the detail endpoint states about this specific posting.
        parts = [
            detail.get("description"),
            detail.get("minimumQualifications"),
            detail.get("preferredQualifications"),
        ]
        joined = "\n\n".join(p for p in parts if p)
        return html_to_text(joined) if joined else None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        items = raw.get("searchResults") or []
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for item in items:
            native_id = item.get("id")
            title = (item.get("postingTitle") or "").strip()
            if not native_id or not title:
                continue
            detail = details.get(native_id) or {}
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{native_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=self._location(item),
                    remote=item.get("homeOffice"),
                    department=(item.get("team") or {}).get("teamName"),
                    url=self.job_url(item),
                    posted_at=item.get("postDateInGMT"),
                    scraped_at=scraped_at,
                    description=self._description(detail),
                    employment_type=detail.get("employmentType"),
                )
            )
        return jobs
