"""jobs.apple.com — Apple's own in-house careers system, a Single source scraper (ADR-0139): one
company, one host, never discovered. ``slug`` is fixed to ``jobs.apple.com`` and never varies.

Everything below is measured live 2026-09-11, with a follow-up full-board re-scrape and a
concurrency re-check on 2026-09-12 (full protocol in
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
opening (``id`` shaped ``PIPE-{positionId}``). Both render identically on jobs.apple.com's own
search page, so both are scraped. **The split is heavily skewed toward ``REQ``, not the near-even
41/59 an early 15-page sample suggested** — that sample was biased by ``sort: "newest"``:
evergreen ``PIPE`` rows appear to get their ``postDateInGMT`` touched often, so they cluster at the
front of a newest-first listing out of proportion to their share of the board. Two independent
full-board walks (2026-09-12, ~305 pages / 2-2.5 minutes each) read **98.7% REQ / 1.3% PIPE** across
6,088 postings both times.

**A full walk sees a handful of duplicate ids, from the board reshuffling mid-scrape** — measured
on those same two full walks: 2 and 5 duplicate ids respectively (of 6,088), all with the *same*
fields under both sightings. Four of the five in the second walk were evergreen ``PIPE`` rows whose
``postDateInGMT`` advanced by ~345ms between two reads, re-sorting them past the walk's current
page; the fifth was a ``REQ`` row seen twice with an identical timestamp. Same shape as Eightfold's
replica-ordering problem, at a much smaller scale — ``_listing`` dedupes by ``id`` as it reads
rather than needing Eightfold's multi-sweep reconciliation, since a duplicate here just overwrites
itself rather than costing a row.

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

**``employment_type`` comes from the listing, not the detail's ``employmentType``.** Measured live
2026-09-22 (60 listing rows, ~50 details sampled off pages 3/50/150/250): ``standardWeeklyHours``
is present and non-zero on 20/20 listing rows sampled then, and a wider 280-row sweep across pages
1-300 the same day found it on 280/280 with real spread (5, 16, 20, 35, 37.5, 38, 38.25, 38.5, 39,
40, 42, 45) — never a distinguishing constant. The detail's own ``employmentType`` is mostly the
opposite: absent on 10/10 details sampled off page 3, and on the other 30 (pages 50/150/250) it is
present but always the same string, ``"Standard"`` — including on six 20 h/week retail postings.
So :func:`_employment_type` maps ``standardWeeklyHours`` to ``"Full-time"``/``"Part-time"`` at the
same ``>= 30`` split upstream uses (matching ``employment_type_filter.RULES``' substring rules:
"full"/"part").

**One real exception found widening the sample past the issue's own 50, kept rather than
dropped.** A ``postingTitle`` containing the standalone word "Intern" is a genuine third value the
weekly-hours split can't produce and would otherwise erase: measured live 2026-09-22, 15/15
Intern-titled REQ postings pulled from a live search (`intern`-matched titles across pages 1-400)
carry ``standardWeeklyHours: 40`` — which the hours split alone would read as "Full-time", losing
the one value the ``internship`` filter (``employment_type_filter.py``'s ``is_internship`` rule) actually
matches on. 11/15 of those also state ``employmentType: "Intern"`` on their detail (the other 4
state nothing), so the *title* carries the signal at least as reliably as the detail field it is
replacing, and it comes from the listing — available whether or not this run fetches the detail, so
it survives the ADR-0048 skip re-enabled below. A 2,080-title sweep (pages 1-400, every third page)
found 20 ``\bintern\b`` matches and zero false positives ("International", "internal" don't match a
whole-word boundary), so :func:`_employment_type` checks the title first.

**``homeOffice`` is Apple's own explicit remote flag**, present on both listing and detail and
read directly rather than guessed from the location string — the same precedent Oracle's
``WorkplaceTypeCode`` set.

**No rate limit found.** 8, then 16 concurrent detail fetches (2026-09-11 and 2026-09-12), then 32
and 64 (2026-09-17, ~800 requests) — zero non-200s at every width. Deliberately small samples:
this is one host, not a multi-tenant ATS, so there is no population of boards to protect. The pass
runs at 32 (:data:`_DETAIL_WORKERS`) and over **connections rather than streams**, because this
origin meters per connection — see :attr:`AppleScraper.async_fanout` and
`experiment/apple-detail-transport/LOG.md`. 64 is not taken: the gain flattens and the width is
already past what one company's careers site should be asked for.

**Job URL**: ``https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}`` —
verified live: the page 200s and its ``<title>`` carries the posting title.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.models import Job, html_to_text
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailLost, DetailRequest

_SEARCH_URL = "https://jobs.apple.com/api/v1/search"
_LOCALE = "en-us"
#: The API's own fixed page size — every measured page (including the last, short one) confirms
#: it; there is no larger-limit parameter to ask for.
_PAGE_SIZE = 20
#: Safety backstop, not a real ceiling: no cap was found in the API itself (module docstring), and
#: 1,000 pages is 20,000 postings against today's board of 6,083 — headroom for the board to grow,
#: not a bound expected to be reached.
_MAX_PAGES = 1000
#: Detail-pass width, on the **sync** path — see `AppleScraper.async_fanout_enabled`. 32, not the
#: earlier 16, because the binding constraint is connections and this is how many are opened.
#: Measured live 2026-09-17, zero non-200s at 32 and at 64 over ~800 requests; 32 is taken rather
#: than 64 because the gain flattens (10.3 -> 15.7 req/s on the thread path) and this is one
#: company's careers origin, not a multi-tenant ATS with a population of Boards to spread over.
_DETAIL_WORKERS = 32
_SEARCH_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
#: Whole-word "Intern" in a listing title — module docstring: 15/15 Intern-titled REQ postings
#: sampled live carry `standardWeeklyHours: 40`, which the hours split alone reads as full-time.
#: `\b` excludes "International"/"internal" (measured: 20/2,080 titles matched, zero false
#: positives).
_INTERN_TITLE_RE = re.compile(r"\bintern\b", re.IGNORECASE)


class AppleScraper(BaseScraper):
    """jobs.apple.com — a Single source scraper (ADR-0139). ``slug`` is fixed to the host,
    never discovered."""

    COMPANY = "Apple"

    ats = "apple"
    # scraper: f"https://{slug}/en-us/details/{positionId}/{transformedPostingTitle}" (job_url
    # below) — slug is the fixed host jobs.apple.com (ADR-0139, a Single source scraper: one
    # company, never discovered). Verified live 2026-09-11: the page 200s and its <title>
    # carries the posting title. positionId is numeric on every sampled row; the title slug
    # can in theory be empty (job_url falls back to "" when transformedPostingTitle is
    # missing) so it is loose.
    url_shape = r"https://jobs\.apple\.com/en-us/details/\d+/[\w-]*"
    has_detail_pass = True  # per-Job fetch fills description only (ADR-0048/ADR-0050)
    detail_workers = _DETAIL_WORKERS

    #: This origin meters per **connection**, not per stream, so the multiplexed path is what
    #: limits it (ADR-0167). Live 2026-09-17, interleaved A/B on fresh ids, three rounds, zero
    #: non-200s either way: one ``AsyncSession`` holds ~4.2-4.4 req/s at 16, 32 *and* 64 streams
    #: alike, while 32 threads — 32 connections — reach 9.0-11.7. The server is not the one
    #: refusing: its own SETTINGS frame advertises ``MAX_CONCURRENT_STREAMS = 128``.
    #:
    #: This is why the number is not simply moved to :attr:`detail_streams`: that widens streams on
    #: the one connection, which is exactly what was measured to have no effect. Revisit if the
    #: base ever grows a multi-session async path — the finding is about connections, not async.
    async_fanout = False

    def url(self) -> str:
        return f"https://{self.slug}/{_LOCALE}/search"

    def alias_key(self) -> str | None:
        """ADR-0139: a Single source Board has no sibling tenant to alias against, so the base
        redirect-following default (built for vanity-hostname platforms) has nothing to find
        here — this Board resolves to its own slug."""
        return self.slug

    def _search_page(self, page: int) -> dict[str, Any]:
        response = self._fetch(
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
        )
        response.raise_for_status()
        return response.json().get("res") or {}

    def _listing(self) -> list[dict]:
        """Page through the search endpoint until a short page ends the walk (module docstring:
        an out-of-range page's ``totalRecords`` resets to 0, so the total cannot be the
        terminator).

        Deduped by ``id`` as it's read, not after: measured live 2026-09-12, a `"sort": "newest"`
        walk over ~305 pages (~2-2.5 minutes) sees 2-5 duplicate ids per run out of ~6,088 — a few
        evergreen `PIPE` postings had their `postDateInGMT` bumped mid-walk (two reads ~345ms
        apart), which re-sorts them into a page the walk had already passed, and one `REQ` row
        repeated with an identical timestamp. Both sightings carry the same fields, so the later
        one simply overwrites the earlier — the same shape as Eightfold's replica-ordering fix,
        scaled down: nothing here needs Eightfold's multi-sweep reconciliation, since the
        shortfall this could cause is bounded by the dedup itself, not by the pages missed."""
        seen: dict[str, dict] = {}
        total = listed = unkeyed = 0
        for page in range(1, _MAX_PAGES + 1):
            res = self._search_page(page)
            batch = res.get("searchResults") or []
            if page == 1 and not batch and not res.get("totalRecords"):
                # A moved envelope reads downstream exactly like an empty Board (google.py
                # guards the same shape on its page 1).
                self.note_unreadable_board(
                    "searchResults on page 1", f"keys {sorted(res)}"
                )
            total = res.get("totalRecords") or total
            listed += len(batch)
            for row in batch:
                native_id = row.get("id")
                if native_id:
                    seen[native_id] = row
                else:
                    unkeyed += 1
            if len(batch) < _PAGE_SIZE:
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(seen)} postings — the rest unread"
            )
        self.note_unread_rows(unkeyed, listed, "carried no id")
        if total and len(seen) < total:
            self.mark_truncated_unless_negligible(
                len(seen),
                total,
                f"read {len(seen)} of {total} postings — the rest is unread, not absent",
            )
        return list(seen.values())

    def fetch_raw(self) -> Any:
        items = self._listing()
        # ADR-0017 tech gate: `parse` reads `postingTitle` and `team.teamName` off this same
        # listing item and never off the detail, so the gate reaches the verdict `filter_tech`
        # will reach. Apple is ~70.8% tech, so this saves less than on any other Board — it is
        # wired for the same reason it is cheap.
        #
        # ADR-0048 `needs_detail` skip (`skip_held`): now safe to re-enable. `employment_type`
        # moved off the detail payload onto the listing's own `standardWeeklyHours` (module
        # docstring), so the detail fetch supplies only `description` — exactly the case the skip
        # exists for. An already-described Job (`have_details`) is left alone rather than
        # re-fetched.
        #
        # Every row `_listing` keeps has an `id`. The pass rides the thread transport because
        # `async_fanout` is False (ADR-0167), and records its `concurrency apple details @N` line
        # there at `detail_workers` (ADR-0201) — the line the transport decision was read from.
        details = self.run_detail_pass(
            items,
            key_of=lambda row: row["id"],
            what="detail payloads",
            title_of=lambda row: row.get("postingTitle"),
            department_of=lambda row: (row.get("team") or {}).get("teamName"),
            skip_held=True,
        )
        return {"searchResults": items, "details": details}

    def detail_request(self, row: dict) -> DetailRequest:
        # PIPE's id carries the vendor-type prefix the detail endpoint does not want; REQ's id
        # (already "{positionId}-{reqSuffix}") is the jobNumber verbatim.
        job_number = row["id"].removeprefix("PIPE-")
        return DetailRequest(f"https://{self.slug}/api/v1/jobDetails/{job_number}")

    def read_detail(self, row: dict, response: Any) -> dict:
        detail = json.loads(response.text).get("res")
        if detail is None:
            raise DetailLost("no res on a 200")
        return detail

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

    def _employment_type(self, item: dict) -> str | None:
        """ "Intern"/"Full-time"/"Part-time" from the listing alone — never the detail's
        ``employmentType``, which is absent or the constant ``"Standard"`` on almost every
        posting (module docstring). A title carrying the whole word "Intern" wins first: it is
        the one value the hours split can't produce and would otherwise erase, and unlike the
        detail field it doesn't disappear once the ADR-0048 skip stops fetching a Job's detail.
        Otherwise ``standardWeeklyHours >= 30`` matches upstream's own split; wording carries
        "full"/"part"/"intern" so ``employment_type_filter.RULES``' substring rules read it."""
        if _INTERN_TITLE_RE.search(item.get("postingTitle") or ""):
            return "Intern"
        hours = item.get("standardWeeklyHours")
        if hours is None:
            return None
        return "Full-time" if hours >= 30 else "Part-time"

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
        untitled = 0
        for item in items:
            native_id = item.get("id")
            title = (item.get("postingTitle") or "").strip()
            if not native_id or not title:
                untitled += 1
                continue
            detail = details.get(native_id) or {}
            jobs.append(
                Job(
                    id=self.job_id(native_id),
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
                    employment_type=self._employment_type(item),
                )
            )
        self.note_unread_rows(untitled, len(items), "carried no id or title")
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for in this
        # scraper's raw record shape. Needs its own measurement pass before this can claim more.
        return None
