"""Amazon (amazon.jobs) scraper.

Amazon runs its own in-house careers system — no other tenant sits on it — so per ADR-0139
("a single-company board is its own ats") this is ``ats="amazon"`` with one fixed,
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

**No rate limit found.** 60 requests across two bursts (20 at 20 concurrent threads, 40 at 16)
all returned 200 — 0 non-200s. Latency is real, not throttling: 1.9–6.2s per request, median
2.8s, so the fan-out width matters for wall-clock even though nothing here is refusing
traffic. No ``User-Agent`` is required either (a bare, UA-less request also returned 200), but
this scraper still sends the repo's own ``USER_AGENT`` like every other one.

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
  location-string heuristic (:func:`headstart.models.is_remote`) is the fallback for the rare
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
import urllib.parse
from datetime import datetime
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

#: The API's own hard maximum for `result_limit`. 200 and above answer 200 with
#: `{"error": "Result limit cannot be greater than 100"}` and no jobs.
_PAGE_SIZE = 100
#: `offset + result_limit` beyond this errors: `{"error": "Cannot return more than 10000
#: results at once", "jobs": null}`. Measured exactly at offset=10000 (blank) and
#: offset=10050 (the error), both against an unfiltered query.
_OFFSET_CEILING = 10_000
#: Concurrent page fetches. No rate limit found in 60 requests at up to 16 concurrent (module
#: docstring); this stays a little short of that measured-clean width for the same reason
#: icims/zwayam settled on 16 rather than pushing further on an unmeasured margin.
_PAGE_WORKERS = 16

_WS = re.compile(r"\s+")


class AmazonScraper(BaseScraper):
    """Amazon's own careers system — the one board on this ATS (ADR-0139). ``slug`` is fixed:
    ``www.amazon.jobs``, Amazon's own primary careers host."""

    ats = "amazon"
    # The listing carries the full description; no second fetch needed.
    has_detail_pass = False

    def url(self) -> str:
        return self._search_url(offset=0)

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
            data = json.loads(
                self._get(self._search_url(offset=offset, category=category))
            )
        except http.RequestsError:
            return []
        if data.get("error"):
            return []
        return data.get("jobs") or []

    async def _page_async(self, session: Any, task: tuple[str, int]) -> list[dict]:
        category, offset = task
        try:
            body = await self._get_async(
                session, self._search_url(offset=offset, category=category)
            )
        except http.RequestsError:
            return []
        data = json.loads(body)
        if data.get("error"):
            return []
        return data.get("jobs") or []

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
        if self.async_fanout_enabled():
            pages = self.fan_out_async(
                tasks, self._page_async, concurrency=_PAGE_WORKERS, default=[]
            )
        else:
            pages = self.fan_out(tasks, self._page, workers=_PAGE_WORKERS, default=[])
        # Deduped defensively by native id, not because a job is known to carry more than one
        # business_category (measured: it doesn't — a full walk of one category matched its own
        # facet count exactly, with no drift) but because every other subdivided scraper here
        # dedupes rather than trusting the partition to be perfect on a live, moving board.
        seen: dict[str, dict] = {}
        for page in pages:
            for job in page:
                native_id = job.get("id_icims")
                if native_id:
                    seen.setdefault(str(native_id), job)
        expected = sum(categories.values())
        if expected:
            self.mark_truncated_unless_negligible(
                len(seen),
                expected,
                f"read {len(seen)} of {expected} postings (summed over the "
                "business_category facet) — the rest is unread, not absent",
            )
        return list(seen.values())

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for r in raw:
            native_id = str(r.get("id_icims") or "").strip()
            title = (r.get("title") or "").strip()
            if not native_id or not title:
                continue
            location = r.get("normalized_location") or r.get("location")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{native_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=_remote(r, location),
                    department=r.get("job_category"),
                    url=f"https://{self.slug}{r.get('job_path') or ''}",
                    posted_at=_posted_at(r.get("posted_date")),
                    scraped_at=scraped_at,
                    description=html_to_text(_full_description(r)),
                    employment_type=r.get("job_schedule_type"),
                )
            )
        return jobs


def _remote(r: dict, location: str | None) -> bool | None:
    """Whether this posting is remote: any ``VIRTUAL``-typed entry in its own ``locations``
    array, else the location-string fallback for a posting with none parseable."""
    for raw_loc in r.get("locations") or []:
        try:
            parsed = json.loads(raw_loc)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "VIRTUAL":
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
