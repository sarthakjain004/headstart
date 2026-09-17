"""Gem career-site scraper.

Adapted from jobhive's Gem scraper (kalil0321/ats-scrapers, MIT). Its pagination/batch-size claims
and its posted-date fallback are measured false or dead below — the attribution stays, since it is
the licence term, not a description of how much code is left (the same convention `phenom.py`
follows).

The ``slug`` is the board path on one fixed host (``jobs.gem.com/{slug}``), the same shape
`ashby.py` and `rippling.py` already use. Every tenant is served by one GraphQL batch endpoint,
``POST https://jobs.gem.com/api/public/graphql/batch``, taking an array of named operations:
``JobBoardList`` lists a board, ``ExternalJobPostingQuery`` reads one posting's detail.

Everything below was measured live on 2026-09-16 against the real API, using the 496-company seed
list published alongside the upstream scraper (`data/ats-tenants-merged/gem.csv`) —
`docs/gem/2026-09-16_graphql-api-measurement.md` has the full numbers.

**The listing has no pagination, and none was found to need one.** The GraphQL schema takes no
offset/limit argument at all, so unlike Phenom's ``size``-clamped search there is nothing to page
through — a request either gets the whole board or it doesn't. Measured against every live board in
the seed (496 slugs, 377 with jobs): the largest, ``coupa-software-inc-ats-1`` at 300 postings,
returned exactly 300 both from the listing and in a third-party pre-scraped sample — no truncation
observed. There is also no separate "total" field to compare a read against (unlike Phenom's
``totalHits``), so a cap above what has been observed here would be undetectable from this API
alone; nothing found up to 300 justifies building for one.

**A live board and a nonexistent one are indistinguishable from the GraphQL listing alone** — both
answer HTTP 200 with an empty ``jobPostings`` list. Measured directly: a deliberately-fabricated
slug (`this-slug-definitely-does-not-exist-xyz123`) returns the identical empty-list shape a real,
currently-zero-openings board does. The board page (``jobs.gem.com/{slug}``) is what actually
distinguishes them — 200 for real, 404 for fake — which is why the liveness probe (below) checks the
page before trusting the API's job count. Of the 119 seed slugs the API reported zero jobs for, 104
(87%) have a real 200 board page (a live company with nothing open right now — the same "many live
Boards read 0 jobs" shape this repo already accepts for freshteam/keka) and 15 (13%) 404 outright.

**The detail batch size is comfortably larger than upstream's DETAIL_BATCH_SIZE = 20**, which its own
comment calls "conservative" rather than a measured limit. Packing 20/25/50/100/200/300/500/1,000
``ExternalJobPostingQuery`` operations into one POST all succeeded (1,000 ops in 8.0s, zero errors);
2,000 failed outright with HTTP 500. `_DETAIL_BATCH_SIZE` is set well under that wall, at 100 — five
times upstream's batch size, and enough that even the largest observed board needs three detail
requests total, not per-job ones.

**Batch response order was checked, not assumed** — `_apply_detail_results` matches each result
back to its request by position (``zip(batch, results)``), which only holds if the endpoint
preserves request order. Verified live: natural, reversed, and two independently-shuffled id
orders (30/50/200 ops), a repeat of the same shuffle, and a batch with the same id repeated three
times interspersed with others (the sharpest test — a reordering or internal dedup would show up
immediately) all came back with each result's own ``extId`` matching the requested id at that same
position, 7/7 runs.

**No rate limit found.** 500+ listing requests and 2,500+ detail requests across this measurement
pass, at concurrency 8/16/32/64/128, drew zero non-200s and zero ``Retry-After`` headers; p50 latency
stayed 0.3-0.5s across the whole range. `_DETAIL_WORKERS` is set to a modest 16 anyway: batching
already cuts request volume ~100x versus a per-job scraper, so there is nothing to gain by pushing
concurrency toward the (unfound) wall.

**No CSRF, session cookie, or Referer needed** — every probe in this measurement pass, including the
first request of a fresh process, used a bare POST with no prior GET and no auth headers and got a
full 200 response.

**Two candidate posted-date fields are not equally real.** ``firstPublishedTsSec`` was populated on
159/159 sampled detail records (every board's listed postings, by construction, are already public).
``startDateTs`` was populated on **0/159** — it is documented upstream as a *future* go-live
timestamp, and a job scheduled for the future is exactly a job this scraper's public listing cannot
see yet, so the field is structurally unreachable here rather than merely rare. `posted_at` reads
``firstPublishedTsSec`` alone; ``startDateTs`` is not requested.

**``job.locationType`` (``IN_OFFICE``/``REMOTE``/``HYBRID``), not the per-location ``isRemote``
flag, is the authoritative remote signal.** `locationType` was populated on all 3,533 postings
sampled across every live board; `isRemote` disagreed with it on 318 (9.0%) — nearly always a
`locationType: REMOTE` posting whose location still carries `isRemote: false` (titles like "Agora -
Full Stack Engineer (Remote)" confirm which field is telling the truth). `_remote` reads
`locationType` first and falls back to `isRemote` only if `locationType` is ever absent, which it
never was in this sample.

**Every job's detail is fetched, with no ADR-0048 skip.** Phenom can skip a Job whose description is
already stored because every other field it emits also comes from its listing; Gem cannot make that
same claim, because `posted_at` and `compensationHtml` are detail-only (never on `JobBoardList`) —
skipping the fetch for an already-seen Job would silently null those fields on every run after the
first. Batching keeps the cost of always fetching low: a 300-posting board costs three detail
requests regardless.

**Company name: the board page renders server-side, and its title wrapper is one this repo already
knows.** Sampled 60 live board pages — no JS wall, real HTML on a bare GET. ~95% follow "{Name}
Careers" (`a16z speedrun careers`, `Accel Careers`, …), the exact wrapper `company_name.py` already
uses for eightfold/jobvite/keka, so `PATTERNS["gem"]` reuses `_CAREERS_WRAPPER` rather than a new
pattern. Many single-word-brand tenants (`agenta.ai Careers`, `11x.ai Careers`) still fall through to
their slug: `from_title`'s existing hostname guard correctly declines a name that is itself a bare
domain.

**Salary: `compensationHtml` is machine-templated on the large majority of tenants that state one at
all**, unlike Phenom's own tenant-private free text. Sampled 899 postings across all 377 live boards:
136 (15.1%) carry a `compensationHtml`, and 132 of those (97%) fit one dedicated Tier-1 parser
(`salary._field_gem`) even when the template is wrapped in a longer prose paragraph — verified
directly against the real text, not just counted. The 4 declines are tenants whose own figure reads
as implausible if annualized (e.g. "$100 – $200 per year", clearly a mislabeled hourly rate) and are
correctly rejected by the shared plausibility bounds rather than silently mis-annualized. Currency
symbols observed: `$`, `CA$`/`C$`, `A$`, `€`, `£`, `₹` — all mapped explicitly rather than guessed,
since a bare `$`-ending multi-char symbol cannot be assumed to be CAD the way the shared Tier-2
`_guess_currency` does (that heuristic is right for Tier 2's narrower observed evidence, wrong here
once `A$` is real).

``alias_key`` is not overridden: Gem's slug is a path segment on one shared host, the identical shape
`ashby.py` and `rippling.py` already leave on the base class default. That default safely degrades to
an uninformative (not harmful) "migrated" result for this shape of ATS — see its own docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import USER_AGENT, BaseScraper

GRAPHQL_URL = "https://jobs.gem.com/api/public/graphql/batch"

#: Ops per detail POST. Measured: 20 through 1,000 all succeed (1,000 in 8.0s), 2,000 fails with
#: HTTP 500 — this sits comfortably under that wall. See the module docstring.
_DETAIL_BATCH_SIZE = 100

#: See the module docstring — no rate limit found up to concurrency 128; kept modest because
#: batching already does the work a wider fan-out would.
_DETAIL_WORKERS = 16

_LIST_QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      id
      extId
      title
      locations { id name city isoCountry isRemote extId __typename }
      job {
        id
        department { id name extId __typename }
        locationType
        employmentType
        __typename
      }
      __typename
    }
    __typename
  }
}"""

# startDateTs is deliberately not requested — measured 0/159 populated, and structurally
# unreachable here (see the module docstring). companyUrl/requisitionId/teamDisplayName are
# likewise omitted: nothing in Job consumes them.
_DETAIL_QUERY = """query ExternalJobPostingQuery($boardId: String!, $extId: String!) {
  oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
    id
    title
    descriptionHtml
    extId
    firstPublishedTsSec
    locations { id extId name city isoCountry isRemote __typename }
    job {
      id
      locationType
      employmentType
      department { id extId name __typename }
      __typename
    }
    compensationHtml
    __typename
  }
}"""


def _listing_title(row: dict[str, Any]) -> str | None:
    """A listing posting's title, for the tech gate. Named rather than an inline lambda because
    this gate is a *measured* tolerance and not an exact one — ``parse`` falls back to the
    detail's own ``title``, so an accessor that quietly started reading a different key would
    classify on the wrong string and nothing would raise."""
    return row.get("title")


def _listing_department(row: dict[str, Any]) -> str | None:
    """A listing posting's department, for the tech gate. See :func:`_listing_title`."""
    return ((row.get("job") or {}).get("department") or {}).get("name")


class GemScraper(BaseScraper):
    """Gem scraper — ``slug`` is the board's path segment on ``jobs.gem.com``."""

    ats = "gem"
    # scraper: f"https://jobs.gem.com/{slug}/{extId}" — one fixed host, slug and a per-posting
    # extId (base64-shaped or a bare UUID; both are URL-safe, verified live). No title slug is
    # appended: the page renders from the id alone.
    url_shape = r"https://jobs\.gem\.com/[^/]+/[^/?#]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = (
        True  # per-Job fetch fills `description`, `posted_at`, `salary` (ADR-0050)
    )

    def url(self) -> str:
        return f"https://jobs.gem.com/{self.slug}"

    def board_page(self) -> str:
        return self.url()

    def job_url(self, native_id: str) -> str:
        return f"https://jobs.gem.com/{self.slug}/{native_id}"

    # --- listing ------------------------------------------------------------------------------

    def _graphql(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = self._fetch(
            "POST",
            GRAPHQL_URL,
            json=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Content-Type": "application/json",
            },
            timeout=45,
        )
        response.raise_for_status()
        return response.json() or []

    def _listing(self) -> list[dict[str, Any]]:
        body = self._graphql(
            [
                {
                    "operationName": "JobBoardList",
                    "variables": {"boardId": self.slug},
                    "query": _LIST_QUERY,
                }
            ]
        )
        result = body[0] if body else {}
        data = (result.get("data") or {}).get("oatsExternalJobPostings")
        if data is None:
            # A real API error (bad envelope), not an empty board — those answer `data` present,
            # `jobPostings: []`, which is the ordinary "nothing open right now" shape and needs
            # no note (see the module docstring on live-vs-empty).
            self.note_unreadable_board(
                "a jobPostings list", "no oatsExternalJobPostings in the response"
            )
            return []
        return [p for p in (data.get("jobPostings") or []) if isinstance(p, dict)]

    # --- detail -------------------------------------------------------------------------------

    def _detail_payload(self, batch: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "operationName": "ExternalJobPostingQuery",
                "variables": {"boardId": self.slug, "extId": ext_id},
                "query": _DETAIL_QUERY,
            }
            for ext_id in batch
        ]

    def _apply_detail_results(
        self, batch: list[str], results: list[Any]
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for native_id, result in zip(batch, results, strict=False):
            detail = ((result or {}).get("data") or {}).get("oatsExternalJobPosting")
            if detail:
                out[native_id] = detail
            else:
                self.note_detail_loss("no job on a 200")
        return out

    def _detail_batch(self, batch: list[str]) -> dict[str, dict[str, Any]]:
        try:
            results = self._graphql(self._detail_payload(batch))
        except http.RequestsError as exc:
            for _ in batch:
                self.note_detail_exception(exc)
            return {}
        return self._apply_detail_results(batch, results)

    async def _detail_batch_async(
        self, session: Any, batch: list[str]
    ) -> dict[str, dict[str, Any]]:
        try:
            response = await self._fetch_async(
                session,
                "POST",
                GRAPHQL_URL,
                json=self._detail_payload(batch),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                },
                timeout=45,
            )
            response.raise_for_status()
            results = response.json() or []
        except http.RequestsError as exc:
            for _ in batch:
                self.note_detail_exception(exc)
            return {}
        return self._apply_detail_results(batch, results)

    def fetch_raw(self) -> Any:
        listed = self._listing()
        # No ADR-0048 skip here — see the module docstring: posted_at and compensationHtml are
        # detail-only, so skipping a previously-seen Job would silently null them on every later
        # run rather than merely re-fetch a description we already store.
        #
        # The ADR-0166 tech gate does apply: a posting `filter_tech` drops is never embedded,
        # indexed or shown, so its detail buys nothing. A *measured* tolerance rather than
        # exactness — `parse` reads `title` and `job.department.name` off the listing row but
        # falls back to the detail's own for both, so a posting whose listing omits what its
        # detail states could disagree. Measured live 2026-09-17 over the 40 Boards the
        # 2026-09-17 pre-filter corpus says lean hardest on `department` (56 of its 620 tech
        # postings are ones a department-blind gate would drop), 1,304 postings: 606 kept by the
        # gate, 606 by the filter, zero disagreements. Re-check it if this query's shape moves.
        tech = self.tech_detail_wanted(listed, _listing_title, _listing_department)
        wanted = [str(j["extId"]) for j in tech if j.get("extId")]
        batches = [
            wanted[i : i + _DETAIL_BATCH_SIZE]
            for i in range(0, len(wanted), _DETAIL_BATCH_SIZE)
        ]
        details: dict[str, dict[str, Any]] = {}
        if batches:
            if self.async_fanout_enabled():
                batch_results = self.fan_out_async(
                    batches, self._detail_batch_async, default={}
                )
            else:
                batch_results = self.fan_out(
                    batches, self._detail_batch, workers=self.detail_workers, default={}
                )
            for d in batch_results:
                details.update(d or {})
            fetched = [details.get(native_id) for native_id in wanted]
            self.report_detail_gaps(fetched, "descriptions")
        return {"jobs": listed, "details": details}

    # --- parse --------------------------------------------------------------------------------

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        listed = raw.get("jobs") or []
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in listed:
            native_id = str(row.get("extId") or "")
            if not native_id:
                continue
            detail = details.get(native_id) or {}
            job_obj = row.get("job") or {}
            detail_job = detail.get("job") or {}
            locations = row.get("locations") or detail.get("locations") or []
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("title") or detail.get("title") or "").strip(),
                    location=_location(locations),
                    remote=_remote(
                        job_obj.get("locationType") or detail_job.get("locationType"),
                        locations,
                    ),
                    department=(
                        (job_obj.get("department") or {}).get("name")
                        or (detail_job.get("department") or {}).get("name")
                    ),
                    url=self.job_url(native_id),
                    posted_at=_posted_at(detail.get("firstPublishedTsSec")),
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("descriptionHtml")),
                    experience=None,  # no native field; headstart.experience.extract() covers it
                    employment_type=(
                        job_obj.get("employmentType")
                        or detail_job.get("employmentType")
                    ),
                    salary=self._salary_field(detail),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``compensationHtml`` is HTML, not the plain string every other ATS's ``_salary_field``
        returns — strip it the same way ``description`` is stripped, so ``salary._field_gem``
        (Tier 1) reads clean text. ``None`` when this Job carries no detail (never fetched, or the
        detail pass lost it) or the detail states no compensation field at all."""
        return html_to_text((raw or {}).get("compensationHtml"))


def _location(locations: list[dict[str, Any]]) -> str | None:
    """The first location's own rendered name, falling back to a city/country join.

    Only the first of possibly several locations is used, matching the upstream scraper's own
    choice — real multi-location postings were a small minority of those sampled. ``name`` is
    usually the richer field (Gem's own normalisation already renders "Remote - USA", "San
    Francisco (On-Site)"); ``city``/``isoCountry`` is the fallback for the postings where it isn't
    set.
    """
    if not locations or not isinstance(locations[0], dict):
        return None
    first = locations[0]
    name = (first.get("name") or "").strip()
    if name:
        return name
    parts = [first.get("city"), first.get("isoCountry")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _remote(location_type: str | None, locations: list[dict[str, Any]]) -> bool | None:
    """``job.locationType`` first — see the module docstring for why it outranks the per-location
    ``isRemote`` flag. Falls back to ``isRemote`` only when ``locationType`` itself is absent,
    which never happened in the measured sample but isn't guaranteed by the schema."""
    if location_type == "REMOTE":
        return True
    if location_type == "HYBRID":
        return None
    if location_type in ("IN_OFFICE", "ONSITE", "ON_SITE"):
        return False
    if locations:
        return any(
            isinstance(loc, dict) and loc.get("isRemote") is True for loc in locations
        )
    return None


def _posted_at(first_published_ts_sec: Any) -> str | None:
    if (
        not isinstance(first_published_ts_sec, (int, float))
        or first_published_ts_sec <= 0
    ):
        return None
    try:
        return datetime.fromtimestamp(first_published_ts_sec, tz=UTC).isoformat()
    except (OSError, ValueError, OverflowError):
        return None
