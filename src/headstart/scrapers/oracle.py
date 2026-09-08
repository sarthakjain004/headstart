"""Oracle Recruiting Cloud (HCM) scraper.

Adapted from jobhive's Oracle scraper (kalil0321/ats-scrapers, MIT). Little of that original
survives this rewrite, but the attribution does: it is the licence term, not a description of how
much code is left.

Each Oracle tenant sits on its own pod host, so the ``slug`` is that host
(``fa-etvl-saasfaprod1.fa.ocs.oraclecloud.com``). The public CandidateExperience REST API returns
requisitions under ``items[0].requisitionList``, and the pagination params live *inside* the
``finder`` string rather than as separate query params.

Four measured facts shape everything below. All are from a 2026-09-08 sweep of the 670 pool
hosts — 15,189 listing requisitions, 1,030 detail payloads, 6,351 rate-limit requests — written up
in ``docs/oracle/2026-09-08_api-measurement.md``.

**``siteNumber`` is a filter, and omitting it is how you read the whole Board.** A tenant may run
many "sites" (brand, language, and internal variants); a site number narrows the listing to one of
them, an *invalid* one is ignored rather than rejected, and no site number at all returns the
host's entire set. That last form is the exact union of every site: verified across all 596 hosts
with a hiring board, zero counter-examples, and the 35 apparent misses all explained by the 200-row
page cap rather than a missed posting. This is why the finder here carries no ``siteNumber`` and
why the old ``host/CX_2`` slug override is gone — it could only ever shrink a Board. The old
hardcoded ``CX_1`` default was wrong for **929 of 1,331** hiring boards, and wrong *silently*:
a bad site number still answers 200 with a well-formed envelope.

**The listing carries almost nothing, and the description it does carry is truncated.** Across
15,189 requisitions, ``LegalEmployer``, ``Department``, ``JobFunction`` and ``JobType`` are 0.0%
non-null and ``JobSchedule`` is 1.1% — so every field but title, location, id and date has to come
from somewhere else. ``ShortDescriptionStr`` is present on 44.4% and **hard-capped at exactly
1,000 characters** (p100 = 1,000; 17 of 6,745 samples sit precisely on it). It is a truncated
summary by construction, never a description.

**The full description exists only on the detail endpoint.** ``ExternalDescriptionStr`` is present
on 93.1% of detail payloads at p50 4,138 characters with *no* cap (the length distribution is
smooth, and the most common exact length occurs 3 times in 536 samples), against the listing
teaser's p50 of 234. It cannot be reached from the listing at any ``expand`` — ``expand=all`` does
not include the key. Hence the per-Job detail pass and ``has_detail_pass``.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

#: The API's own maximum `limit`. Requesting more is silently clamped to it — 300, 500 and 1000
#: all return 200 rows and echo `"limit": 200`.
_PAGE_SIZE = 200
#: Our ceiling. Reaching it means the board did not end, we stopped reading it.
_MAX_PAGES = 100
#: Rows a completed walk may fall short of `TotalJobsCount`, **per page fetched**, before it is
#: called truncated. Per-page rather than a flat figure because the shortfall scales with the
#: walk: measured on 55 multi-page Boards, 9 landed under their total and the three worst were
#: short by 7 over 15 pages, 5 over 20 and 4 over 8 — ratios of 0.5, 0.25 and 0.47 rows per page.
#: A flat slack of 2 was the first attempt and a review found it wrong in exactly the place that
#: matters: it fits small Boards and falsely truncates large ones, every run, permanently, into
#: ADR-0053's exclusion scope — which has no drain, so the accretion is invisible.
#:
#: 1 row per page is double the worst ratio observed, and still separates a real loss by more
#: than an order of magnitude: `egud` reads 10,000 of 11,056 (20.7 rows/page), `ejwl` 9,926 of
#: 13,430 (~70) and `etud` 89 of 114 (25) — all still reported, as they must be.
_SLACK_PER_PAGE = 1
#: Concurrent detail fetches. Measured clean at 32 across five regional pods (us2, ocs, em3, em2,
#: us6) — 454 calls, 46-65 req/s, zero non-200s, and no rate limit found anywhere in 6,351
#: requests. Half that here because `harvest` scrapes Boards concurrently *and* each Board fans
#: out, so peak in-flight is the product (see `base.py`'s note), and because 16 is what icims and
#: zwayam already use — no special justification needed for a value the repo already runs.
_DETAIL_WORKERS = 16


#: The workplace-type code meaning remote. Oracle states the workplace twice — a stable
#: ``WorkplaceTypeCode`` and a *tenant-customised display label* — and only the code can be
#: matched safely. Measured over 7,756 listing rows: ``ORA_REMOTE`` appears 116 times, and the
#: labels sitting on it are "Remote" (107) **and "Work From Home" (9)**; likewise ``ORA_ON_SITE``
#: (2,171) carries both "On-site" (2,116) and "Work From Office" (55). So a substring test against
#: the label misses a spelling a tenant is free to invent, while the code is exact. The two are
#: always present together (2,716 rows each), which is why the label is not consulted at all.
_REMOTE_CODE = "ORA_REMOTE"


def _remote(listed: dict, detail: dict, location: str | None) -> bool | None:
    """Whether this posting is remote: the tenant's own answer, else the location guess.

    Falling back to the location is safe rather than merely convenient. Of 2,716 rows stating a
    workplace type, the two signals disagreed 109 times and **every one** was the tenant saying
    remote where the location string did not — there is no measured case of a location guess
    overriding an explicit on-site, which is the failure this ordering would otherwise risk.
    """
    code = listed.get("WorkplaceTypeCode") or detail.get("WorkplaceTypeCode")
    if code:
        return code == _REMOTE_CODE
    return is_remote(location)


class OracleScraper(BaseScraper):
    """Oracle Recruiting Cloud scraper — ``slug`` is the tenant's pod host."""

    ats = "oracle"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self._offset = (
            0  # advanced by `fetch_raw`; `url()` renders whatever page it is on
        )

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The pod host, which the pool's ``tenant`` column does not always hold.

        Discovery writes two shapes into ``data/ats-tenants-merged/oracle.csv``: the host itself
        (from the Common-Crawl miner) and a bare company label like ``akamai`` (from the harvest
        feeder), the latter carrying the host only in ``url``. The base implementation returns the
        tenant, which for the second shape is not a host and cannot be fetched — so prefer the
        URL's host and fall back to the tenant.
        """
        # `models.host_of`, not a local split: that function exists because this rule has to
        # hold in the scraper, the liveness prober and the ledger repair at once, and the one
        # time they disagreed it cost 312 boards recorded live with zero jobs. A hand-rolled
        # version here dropped its `?query` split and the lowercasing the casing-duplicate rule
        # depends on. Same shape icims and zwayam use.
        #
        # A pool row that is a bare label with no URL either (23 of them: `akamai`, `chubb`,
        # `cummins`) has no host to recover, and falls through to the tenant — which will not
        # resolve. That is deliberate and currently unreachable: those rows are unprobeable, so
        # the ledger has no such entry and `load_active_companies` can never build one. Guarding
        # it here would be error handling for a case that cannot arrive.
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        # No `siteNumber`: it filters the Board down to one site, and omitting it returns the
        # union of every site (module docstring). `findReqs` still needs its other params inside
        # the finder string, comma-separated — the careers UI's own calls use literal commas.
        return (
            f"https://{self.slug}/hcmRestApi/resources/latest/"
            f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
            f"&finder=findReqs;limit={_PAGE_SIZE},offset={self._offset}"
        )

    def _listing(self) -> list[dict]:
        """Page through the requisition list until the board runs out.

        **A short page is not the end of the Board.** This is the correction that matters here:
        Oracle serves under-full pages mid-walk — `ebxr.fa.us2` answers offset 0 with 199 rows
        against a stated total of 420, reproducibly (3 of 3 attempts), then offset 200 with a
        full 200 and offset 400 with the remaining 20. Treating the 199 as the end read 199 of
        420. Two samples measured the cost: 5 of 40 multi-page Boards (12.5%) losing 3,421 of
        28,715 postings (11.9%), and an independent 60-Board sample finding 12 (20%) losing
        18,974 of 63,397 (29.9%). The loss concentrates in large Boards, so the second is the
        more representative; both say the same thing about the fix. The end is an **empty** page.

        **An empty page is not always the end of the Board, though.** The API refuses to read
        past row 10,000: on `ejwl.fa.us2` (13,430 postings) `offset=9800` returns a full 200
        while `offset=9900` returns zero rows *and* a `TotalJobsCount` of 0 — the envelope goes
        blank rather than erroring. So a Board over ~10,000 postings ends its walk at the
        ceiling, not at its true end, and the shortfall check below is what reports it. That
        ceiling binds long before `_MAX_PAGES`, which is why no real Board reaches the page cap.

        `TotalJobsCount` stops the walk early when it is satisfied, and it is trustworthy for
        that: across 55 multi-page Boards no Board repeated an id, and 46 of 55 landed exactly on
        their total. `hasMore` remains useless — it came back ``false`` on a 248-posting Board
        whose first page held 200.
        """
        reqs: list[dict] = []
        total = 0
        self._offset = 0
        for _ in range(_MAX_PAGES):
            items = json.loads(self._get()).get("items") or []
            first = items[0] if items else {}
            batch = first.get("requisitionList") or []
            # Keep the first non-zero total: later pages echo it, but a missing one must not
            # reset what an earlier page already told us.
            total = first.get("TotalJobsCount") or total
            reqs.extend(batch)
            self._offset += _PAGE_SIZE
            # An empty page is the definitive end; a short one only means this page was short.
            # `total and ...` is load-bearing on the second clause: without it a missing
            # TotalJobsCount makes `len(reqs) >= 0` true and stops after one page.
            if not batch or (total and len(reqs) >= total):
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(reqs)} of {total or 'unknown'} "
                "requisitions — the rest unread"
            )
        # Counts the fetches made, so it includes the final empty one — the allowance is
        # therefore data-pages + 1, a row more generous than the 0.5 rows/page the sample
        # measured. Deliberate: the margin sits on the safe side of a false truncation, and a
        # real loss clears it by more than an order of magnitude either way.
        pages = self._offset // _PAGE_SIZE
        if total and len(reqs) < total - pages * _SLACK_PER_PAGE:
            self.mark_truncated(
                f"read {len(reqs)} of {total} requisitions — the rest is unread, not absent"
            )
        return reqs

    def fetch_raw(self) -> Any:
        # Every listed posting gets its detail payload, with no ADR-0048 `needs_detail` skip. That
        # optimisation is only safe where the detail fetch supplies the description and nothing
        # else — true for eightfold, false here: this payload is also the only source of
        # `employment_type` (JobSchedule, 80.6% on detail against 1.1% on the listing),
        # `department` (Category 76.2% / JobFunction 45.0%, both 0.0% on the listing) and most of
        # `remote`. Skipping it for an already-described Job would blank three fields that had
        # values. jazzhr and zoho hit the same fork and made the same call.
        reqs = self._listing()
        ids = [str(r["Id"]) for r in reqs if r.get("Id")]
        details: dict[str, dict] = {}
        if ids:
            # Multiplexed by default (ADR-0016); HEADSTART_ASYNC_FANOUT=0 falls back to threads.
            if self.async_fanout_enabled():
                fetched = self.fan_out_async(ids, self._detail_async)
            else:
                fetched = self.fan_out(ids, self._detail, workers=self.detail_workers)
            # Reported, not marked truncated: a missing detail payload costs this Job its
            # description and derived fields, but the Job itself is still listed and still
            # emitted, so the Board's list is whole (ADR-0053 is about the list, not the fields).
            self.report_detail_gaps(fetched, "detail payloads")
            details = {i: d for i, d in zip(ids, fetched) if d}
        return {"requisitionList": reqs, "details": details}

    def _detail_url(self, job_id: str) -> str:
        # `ById` with a *quoted* id, taken from the careers UI's own network calls — the
        # plausible-looking `findReqDetailById` returns HTTP 400. No `siteNumber`: it is ignored
        # here, and 454 cross-pod calls omitting it all returned the requisition.
        return (
            f"https://{self.slug}/hcmRestApi/resources/latest/"
            f'recruitingCEJobRequisitionDetails?onlyData=true&expand=all&finder=ById;Id="{job_id}"'
        )

    @staticmethod
    def _first_item(body: str) -> dict | None:
        """The one requisition in a detail response, or None if it carried none.

        An unknown id is **not** a 404 — it answers 200 with ``items: []`` — so an empty list is a
        real outcome to fold into the detail-gap count, not an error to raise on.
        """
        items = json.loads(body).get("items") or []
        return items[0] if items else None

    def _detail(self, job_id: str) -> dict | None:
        return self._first_item(self._get(self._detail_url(job_id)))

    async def _detail_async(self, session: Any, job_id: str) -> dict | None:
        return self._first_item(
            await self._get_async(session, self._detail_url(job_id))
        )

    def job_url(self, job_id: str) -> str:
        """The careers-UI page for one posting.

        The site segment is cosmetic: browser-verified 2026-09-08, this path renders the posting
        with its Apply controls for the correct site, a wrong site and a nonexistent ``CX_9999``
        alike — all three redirect to ``CX_1`` and resolve the job by id. ``CX_1`` is written here
        because that is where the app lands anyway, not because the Board is known to use it.
        """
        return (
            f"https://{self.slug}/hcmUI/CandidateExperience/en/sites/CX_1/job/{job_id}"
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # A bare `{"items": ...}` is the API's own listing envelope — what a fixture captured
        # straight from the endpoint looks like, and `tests/fixtures/oracle_fa-etqo_cx2.json`
        # is exactly that. `fetch_raw` never returns it, so this branch is unreachable in
        # production; it exists so a captured fixture can stay as captured rather than being
        # doctored into the internal shape. jazzhr and zoho carry a similar fork but a *weaker*
        # precedent than that sounds: theirs keys on the base class's own `str` raw, a shape
        # generic callers really do produce. This one keys on a shape nothing now produces, so
        # a reviewer reading it as dead code is right on the mechanics — it is kept for the
        # fixture alone, and should go when the fixture does.
        if "requisitionList" in raw:
            reqs, details = raw["requisitionList"], raw.get("details") or {}
        else:
            items = raw.get("items") or []
            reqs = items[0].get("requisitionList", []) if items else []
            details = {}
        jobs: list[Job] = []
        for r in reqs:
            job_id = str(r["Id"])
            d = details.get(job_id) or {}
            location = r.get("PrimaryLocation") or r.get("PrimaryLocationCountry")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{job_id}",
                    ats=self.ats,
                    company=d.get("LegalEmployer")
                    or r.get("LegalEmployer")
                    or self.company,
                    title=(r.get("Title") or "").strip(),
                    location=location,
                    remote=_remote(r, d, location),
                    # `Category` before `JobFunction`: measured on the detail payload at 76.2%
                    # against 45.0%, and the listing states neither (both 0.0%).
                    department=(
                        d.get("Category")
                        or d.get("JobFunction")
                        or r.get("Department")
                        or r.get("JobFunction")
                    ),
                    url=self.job_url(job_id),
                    posted_at=r.get("PostedDate"),
                    scraped_at=scraped_at,
                    # The detail body, not the listing's `ShortDescriptionStr` — that field is
                    # capped at 1,000 characters and present on well under half the rows.
                    description=html_to_text(
                        d.get("ExternalDescriptionStr") or r.get("ShortDescriptionStr")
                    ),
                    # `JobSchedule` (80.6% on detail) is the populated one; `JobType` is 0.7%.
                    employment_type=(
                        d.get("JobSchedule")
                        or d.get("JobType")
                        or r.get("JobType")
                        or r.get("JobSchedule")
                    ),
                )
            )
        return jobs
