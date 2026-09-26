"""Amazon (amazon.jobs) scraper.

Amazon runs its own in-house careers system — no other tenant sits on it — so per ADR-0139
("a Single source scraper is its own ats") this is ``ats="amazon"`` with one fixed,
undiscovered ``slug``: ``www.amazon.jobs``, the company's own primary careers host. Reuses
``BaseScraper``/the registry exactly as every multi-tenant ATS does.

All of the below is live-measured 2026-09-11 against the real public endpoint — full protocol,
sample sizes and the exact requests in ``docs/amazon/2026-09-11_api-measurement.md``.

**The search API is public JSON, no auth, no detail pass needed.** ``GET
https://www.amazon.jobs/en/search.json?offset={n}&result_limit=100&sort=recent`` returns up to
100 postings per page, and — unlike Oracle/Eightfold/icims — the listing itself carries the
**full** ``description`` (not a truncated teaser), plus ``basic_qualifications`` and
``preferred_qualifications`` as separate HTML blobs. ``has_detail_pass`` is therefore False:
there is nothing a second fetch would add. ``result_limit`` above 100 is rejected outright
(``"Result limit cannot be greater than 100"``), so 100 is the page size, not a choice.

**The board is far bigger than any single query can reach, and has to be subdivided.** The
API caps ``offset + result_limit`` at 10,000 — a request past it answers 200 with
``{"error": "Cannot return more than 10000 results at once", "jobs": null}`` — and the
top-level ``hits`` field is pinned at exactly ``10000`` on *every* query, filtered or not,
which makes it useless as a real count (confirmed: an unfiltered query and one scoped to a
181-job category both report ``hits: 10000`` until the category filter is actually applied,
at which point ``hits`` reports the true, much smaller number). The real total is well past
that ceiling — the ``business_category`` facet's own 60 buckets summed to 22,539 postings in
one snapshot — so a flat offset walk silently stops at 10,000 and calls it done. The fix
mirrors Workday's ``jobFamilyGroup`` (ADR-0017): subdivide on ``business_category[]``, whose
facet values are AWS's own business-unit buckets (``aws``, ``fulfillment-ops``,
``no-business-category``, ...), fetched fresh each run via ``facets[]=business_category``. The
largest bucket measured (``aws``, ~8,238) sits comfortably under the 10,000 ceiling, so today
no single slice needs a second level of subdivision — but a slice that ever grows past it hits
the same ``mark_truncated`` hard-cap Oracle's offset ceiling does, unconditionally, because no
share of an unreachable remainder is negligible (ADR-0121). Every job carries exactly one
``business_category`` value (verified: a full paginated walk of the 181-job ``ecp`` bucket
returned exactly 181 distinct ids, matching its own facet count with zero drift), so the
slices partition the board rather than overlapping it — but results are deduped by
``id_icims`` anyway, defensively, the way every subdivided scraper here is.

**There IS a rate limit — it just doesn't show up as a bad status code.** A light burst (60
requests across two bursts, 20 at 20 concurrent threads, 40 at 16) all returned plain 200 JSON —
the finding this module first shipped with. A full-board live re-scrape the next day (~530
requests in under 2 minutes, two consecutive full walks) instead got HTTP **200** with an HTML
body: Amazon's own CAPTCHA interstitial (``<title>Server Busy</title>``, a "Continue shopping"
button posting to ``/errors_page/validateCaptcha``) in place of the JSON envelope on a large
share of pages — silently starving that run to 9,181 of 22,577 real postings. ``_page``/
``_page_async`` don't retry this: the page is counted lost, labelled ``CAPTCHA HTML on a 200``
in the truncation reason beside any transport failure's status — which is why :meth:`~BaseScraper.mark_truncated_unless_negligible`
correctly flagged that run truncated (``read 9181 of 22577 postings``) rather than serving a
silent partial board as complete. The wall cleared on its own within ~20 seconds with no code
change — a lone, unhurried request right after the walk got a clean 200 JSON, and an isolated
re-run of ``fetch_raw()`` immediately after (no concurrent bursts preceding it) returned 22,576
of a fresh 22,576-job facet-sum, exactly. So: transient and load-triggered, not a hard per-IP
ban, and the existing truncation guard is what actually protects a real pipeline run from ever
reading this as delistings — nothing further was added here on the strength of one incident, but
a run landing short should be read against this before being called a scraper bug.

**Field mapping notes, all measured on live data:**

- ``id_icims`` (not the UUID-shaped ``id`` field) is the durable native id — it is what
  ``job_path`` and ``url_next_step`` are built from, and it was present and non-null on every
  one of 1,981 postings sampled across 7 categories.
- ``job_category`` (e.g. "Software Development", "Operations, IT, & Support Engineering") is
  the closest analogue to every other scraper's ``department`` — ``business_category`` is a
  business-unit label (aws/retail/advertising/...), not a role family, and is spent entirely
  on subdividing the listing walk.
- A posting's ``locations`` array carries a per-location ``type``: ``ONSITE`` (99.7% of 1,280
  location entries sampled) or ``VIRTUAL``. Any ``VIRTUAL`` entry marks the Job remote; the
  location-string heuristic (:func:`headstart.jobs.job.is_remote`) is the fallback for the rare
  posting with no parseable ``locations`` entry at all.
- ``posted_date`` is a human string ("September 11, 2026") — and on a single-digit day the API
  emits a **double space** ("September  9, 2026"), confirmed live across a 500-posting sample.
  Whitespace is collapsed before ``strptime``.
- ``title`` routinely carries trailing whitespace on the wire ("Data Center Engineering
  Operations Technician ") and is stripped.
- ``company_name`` is the posting's *legal entity* (Amazon Data Services, Inc.; Amazon.com
  Services LLC; Amazon UK Services Ltd.; ...), not a display name — there were 29+ distinct
  values in one 100-posting sample. ``Job.company`` stays the fixed ``self.company`` ("Amazon"),
  the same way every other single-tenant field on this ATS collapses to one board identity.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections import Counter
from datetime import datetime
from typing import Any

from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import BaseScraper, classify_exception, loss_breakdown

#: The API's own hard maximum for `result_limit`. 200 and above answer 200 with
#: `{"error": "Result limit cannot be greater than 100"}` and no jobs.
_PAGE_SIZE = 100
#: `offset + result_limit` beyond this errors: `{"error": "Cannot return more than 10000
#: results at once", "jobs": null}`. Measured exactly at offset=10000 (blank) and
#: offset=10050 (the error), both against an unfiltered query.
_OFFSET_CEILING = 10_000
#: Concurrent page fetches. A light 60-request burst at up to 16 concurrent stayed clean, but a
#: ~530-request, two-walk-in-a-row live re-scrape did trigger Amazon's CAPTCHA wall (module
#: docstring) — the load that matters is sustained volume, not this width specifically, and
#: `mark_truncated_unless_negligible` is what actually protects a run if it recurs. Left at 16,
#: the same value icims/zwayam settled on, rather than guessing at a lower one on one incident.
_PAGE_WORKERS = 16
#: How long the CAPTCHA'd listing pages wait before their one re-fetch. Measured live: a retry
#: right away recovered little, while one ~2–3 minutes later recovered 8 of 8. The wall lost
#: 0–72 of 266 pages a run and truncated the Board on 4 of runs 36200233818..36218633315.
#: 120 is the low end of that window: the least wait that still recovered every page measured.
_CAPTCHA_WAIT_SECONDS = 120
_CAPTCHA = "CAPTCHA HTML on a 200"

_WS = re.compile(r"\s+")


class AmazonScraper(BaseScraper):
    """Amazon's own careers system — the one board on this ATS (ADR-0139). ``slug`` is fixed:
    ``www.amazon.jobs``, Amazon's own primary careers host.

    Does not override :meth:`~BaseScraper.alias_key` — a deliberate per-scraper call, not an
    unexamined default, per ADR-0139's own note that a Single source scraper has no sibling
    board to alias against. Live-checked 2026-09-11: neither the careers page nor the search
    endpoint redirects anywhere (``curl -L`` on both lands back on the request URL), so the
    inherited "follow redirects, compare hosts" default resolves ``www.amazon.jobs`` to itself —
    exactly the meaningful case the default is for, not the empty one.
    """

    COMPANY = "Amazon"

    ats = "amazon"
    # scraper: f"https://{slug}{job_path}" where the SLUG IS THE BOARD HOST (ADR-0139, one
    # tenant) and job_path is the API's own field, e.g. "/en/jobs/10537803/data-center-...".
    # Live-verified 2026-09-11: that exact URL 200s.
    url_shape = r"https://www\.amazon\.jobs/en/jobs/\d+/[\w-]+"
    # The listing carries the full description; no second fetch needed.
    has_detail_pass = False

    def url(self) -> str:
        return self._search_url(offset=0)

    def job_url(self, job_path: str) -> str:
        return f"https://{self.slug}{job_path}"

    def _search_url(
        self, *, offset: int, category: str | None = None, want_facets: bool = False
    ) -> str:
        params: list[tuple[str, str]] = [
            ("offset", str(offset)),
            ("result_limit", str(_PAGE_SIZE)),
            ("sort", "recent"),
        ]
        if category is not None:
            params.append(("business_category[]", category))
        if want_facets:
            params.append(("facets[]", "business_category"))
        return f"https://{self.slug}/en/search.json?{urllib.parse.urlencode(params)}"

    def _categories(self) -> dict[str, int]:
        """The current ``business_category`` facet, ``value -> count`` — the subdivision this
        board is walked by (module docstring). Fetched fresh every run: these are Amazon's own
        live business-unit buckets, not a list worth hardcoding."""
        data = json.loads(self._get(self._search_url(offset=0, want_facets=True)))
        facet = (data.get("facets") or {}).get("business_category_facet") or []
        out: dict[str, int] = {}
        for entry in facet:
            if isinstance(entry, dict):
                for name, count in entry.items():
                    out[name] = int(count or 0)
        if not out:
            # No facet means no tasks and no stated total, so the walk would return [] with no
            # truncation check to catch it — ~22k postings read as an empty Board.
            self.note_unreadable_board(
                "a business_category facet",
                f"none (keys: {sorted(data.get('facets') or {})})",
            )
        return out

    @staticmethod
    def _offsets_for(count: int) -> list[int]:
        """Page offsets covering ``count`` postings, capped so `offset + result_limit` never
        crosses :data:`_OFFSET_CEILING` (the caller reports the cap separately — this just
        never asks for a page past it)."""
        if count <= 0:
            return []
        last_offset = min(count - 1, _OFFSET_CEILING - _PAGE_SIZE)
        return list(range(0, last_offset + 1, _PAGE_SIZE))

    def _page(self, task: tuple[str, int]) -> list[dict]:
        category, offset = task
        try:
            body = self._get(self._search_url(offset=offset, category=category))
        except http.RequestsError as exc:
            self._page_losses[classify_exception(exc)] += 1
            return []
        return self._jobs_of(body, task)

    async def _page_async(self, session: Any, task: tuple[str, int]) -> list[dict]:
        category, offset = task
        try:
            body = await self._get_async(
                session, self._search_url(offset=offset, category=category)
            )
        except http.RequestsError as exc:
            self._page_losses[classify_exception(exc)] += 1
            return []
        return self._jobs_of(body, task)

    def _jobs_of(self, body: str, task: tuple[str, int]) -> list[dict]:
        """One listing page's postings, or [] with the page's loss tallied for the truncation
        reason — the CAPTCHA interstitial answers 200 with HTML (module docstring), and its
        task is kept for the one delayed re-fetch in :meth:`fetch_raw`."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._page_losses[_CAPTCHA] += 1
            self._captcha_tasks.append(task)
            return []
        if data.get("error"):
            self._page_losses["error body"] += 1
            return []
        return data.get("jobs") or []

    def _fetch_pages(self, tasks: list[tuple[str, int]]) -> list[list[dict] | None]:
        """Each task's page, over whichever fan-out this run uses."""
        if self.async_fanout_enabled():
            return self.fan_out_async(
                tasks, self._page_async, concurrency=_PAGE_WORKERS
            )
        return self.fan_out(
            tasks, self._page, workers=_PAGE_WORKERS, what=self.board_key()
        )

    def fetch_raw(self) -> Any:
        categories = self._categories()
        tasks: list[tuple[str, int]] = []
        for category, count in categories.items():
            if count > _OFFSET_CEILING:
                # Unconditional, like Oracle's offset ceiling: the remainder is unreachable on
                # every run, not merely absent this run, so no share of it is negligible
                # (ADR-0121). Unreached today — the largest measured bucket is ~8,238 — but a
                # single business unit growing past it is exactly the shape this guards.
                self.mark_truncated(
                    f"business_category={category} states {count} jobs — the API serves no "
                    f"offset past {_OFFSET_CEILING:,} per query, so the rest is unreachable, "
                    "not absent"
                )
            tasks.extend((category, offset) for offset in self._offsets_for(count))
        # Why each lost listing page was lost, for the truncation reason below. A page that
        # raised anything else comes back None and is counted `unlabelled`.
        self._page_losses: Counter[str] = Counter()
        self._captcha_tasks: list[tuple[str, int]] = []
        pages = self._fetch_pages(tasks)
        if self._captcha_tasks:
            # One delayed pass over the walled pages; whatever is still walled stays lost, and
            # the tally is re-counted from this pass.
            retry, self._captcha_tasks = self._captcha_tasks, []
            self._page_losses -= Counter({_CAPTCHA: len(retry)})
            self._log.info(
                f"{self.board_key()}: {len(retry)} of {len(tasks)} listing pages came back "
                f"as CAPTCHA HTML — re-fetching them once in {_CAPTCHA_WAIT_SECONDS}s"
            )
            # Blocks this Board's worker, and only this Board's: the one Amazon Board is read
            # in a shard's first minutes (run 36218633315 finished it ~2 minutes in), so the
            # wait fits the shard's budget with the rest of its Boards still running beside it.
            time.sleep(_CAPTCHA_WAIT_SECONDS)
            pages += self._fetch_pages(retry)
            self._captcha_tasks = []  # still-walled pages are tallied; nothing re-fetches them
        # Deduped defensively by native id, not because a job is known to carry more than one
        # business_category (measured: it doesn't — a full walk of one category matched its own
        # facet count exactly, with no drift) but because every other subdivided scraper here
        # dedupes rather than trusting the partition to be perfect on a live, moving board.
        seen: dict[str, dict] = {}
        for page in pages:
            for job in page or []:
                native_id = job.get("id_icims")
                if native_id:
                    seen.setdefault(str(native_id), job)
        expected = sum(categories.values())
        if expected:
            lost = sum(self._page_losses.values()) + pages.count(None)
            if lost and self.truncated is not None:
                # The offset ceiling already spoke and `mark_truncated` keeps only the first
                # reason, so the call below would drop this breakdown unlogged.
                self._log.info(
                    f"{self.board_key()}: {lost} of {len(tasks)} listing pages lost"
                    + loss_breakdown(self._page_losses, lost)
                )
            self.mark_truncated_unless_negligible(
                len(seen),
                expected,
                f"read {len(seen)} of {expected} postings (summed over the "
                "business_category facet) — the rest is unread, not absent"
                + (
                    f"; {lost} of {len(tasks)} listing pages lost"
                    + loss_breakdown(self._page_losses, lost)
                    if lost
                    else ""
                ),
            )
        return list(seen.values())

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        untitled = 0
        for r in raw:
            native_id = str(r.get("id_icims") or "").strip()
            title = (r.get("title") or "").strip()
            if not native_id or not title:
                untitled += 1
                continue
            location = _location(r)
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=_remote(r, location),
                    department=r.get("job_category"),
                    url=self.job_url(r.get("job_path") or ""),
                    posted_at=_posted_at(r.get("posted_date")),
                    scraped_at=scraped_at,
                    description=html_to_text(_full_description(r)),
                    employment_type=r.get("job_schedule_type"),
                )
            )
        self.note_unread_rows(untitled, len(raw), "carried no id or title")
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for in this
        # scraper's raw record shape. Needs its own measurement pass before this can claim more.
        return None


def _parsed_locations(r: dict) -> list[dict]:
    """Each ``locations[]`` entry decoded from its own JSON-encoded string, skipping the rare
    unparseable one rather than failing the whole posting on it."""
    parsed: list[dict] = []
    for raw_loc in r.get("locations") or []:
        try:
            entry = json.loads(raw_loc)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            parsed.append(entry)
    return parsed


def _location(r: dict) -> str | None:
    """Every ``locations[]`` entry's own ``normalizedLocation``, joined — not just the
    posting-level ``normalized_location``, which names only one of them.

    Measured live 2026-09-22 on page 1 (100 postings): 40 have 2+ ``locations`` entries, and
    ``normalized_location`` names only one — e.g. id 10555899 normalizes to "Tel Aviv-Yafo…"
    while ``locations`` holds Haifa *and* Tel Aviv. Falls back to the posting-level fields for a
    posting whose array is empty or unparseable."""
    names: list[str] = []
    for entry in _parsed_locations(r):
        name = entry.get("normalizedLocation") or entry.get("location")
        if name and name not in names:
            names.append(name)
    if names:
        return "; ".join(names)
    return r.get("normalized_location") or r.get("location")


def _remote(r: dict, location: str | None) -> bool | None:
    """Whether this posting is remote: any ``VIRTUAL``-typed entry in its own ``locations``
    array, else the location-string fallback for a posting with none parseable."""
    if any(entry.get("type") == "VIRTUAL" for entry in _parsed_locations(r)):
        return True
    return is_remote(location)


def _posted_at(value: str | None) -> str | None:
    """``"September 11, 2026"`` (or, on a single-digit day, ``"September  9, 2026"`` — a
    measured double space) -> ISO date. None if it does not parse."""
    if not value:
        return None
    try:
        cleaned = _WS.sub(" ", value).strip()
        return datetime.strptime(cleaned, "%B %d, %Y").date().isoformat()  # noqa: DTZ007
    except ValueError:
        return None


def _full_description(r: dict) -> str | None:
    """The listing's ``description`` plus its ``basic_qualifications``/
    ``preferred_qualifications`` blobs, in the order the job page itself renders them — the
    qualifications carry phrasing (years of experience, salary ranges) that
    ``experience.extract()``/``salary.extract()`` read from ``description``, so folding them in
    here is what makes those fields reachable at all rather than only from prose in the summary.
    """
    parts = [r.get("description")]
    if r.get("basic_qualifications"):
        parts.append(
            "<br/><br/><b>Basic Qualifications</b><br/>" + r["basic_qualifications"]
        )
    if r.get("preferred_qualifications"):
        parts.append(
            "<br/><br/><b>Preferred Qualifications</b><br/>"
            + r["preferred_qualifications"]
        )
    joined = "".join(p for p in parts if p)
    return joined or None
