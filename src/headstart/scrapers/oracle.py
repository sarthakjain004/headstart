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

from headstart.fetcher import Fetcher
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper, DetailLost, DetailRequest

#: The API's own maximum `limit`. Requesting more is silently clamped to it — 300, 500 and 1000
#: all return 200 rows and echo `"limit": 200`.
_PAGE_SIZE = 200
#: Our ceiling. Reaching it means the board did not end, we stopped reading it.
_MAX_PAGES = 100
#: The offset the API refuses to read past: it serves `offset + limit <= 10000` and answers
#: anything beyond with zero rows *and* a `TotalJobsCount` of 0, the envelope going blank rather
#: than erroring. Measured exactly on `ejwl.fa.us2` — offset 9999 limit 1 returns a row, limit 2
#: returns none; offset 9900 limit 100 returns 100, limit 101 returns none — and confirmed on
#: `eluq.fa.us2`. A Board above it cannot be read whole by offset paging at all.
_OFFSET_CEILING = 10_000

#: Concurrent detail fetches. Measured clean at 32 across five regional pods (us2, ocs, em3, em2,
#: us6) — 454 calls, 46-65 req/s, zero non-200s, and no rate limit found anywhere in 6,351
#: requests. Half that here because `harvest` scrapes Boards concurrently *and* each Board fans
#: out, so peak in-flight is the product (see `base.py`'s note), and because 16 is what icims and
#: zwayam already use — no special justification needed for a value the repo already runs.
_DETAIL_WORKERS = 16


#: The two workplace-type codes this repo has actually observed meaning something unambiguous.
#: Oracle states the workplace twice — a stable ``WorkplaceTypeCode`` and a *tenant-customised
#: display label* — and only the code can be matched safely. Measured over 7,756 listing rows:
#: ``ORA_REMOTE`` appears 116 times, and the labels sitting on it are "Remote" (107) **and "Work
#: From Home" (9)**; likewise ``ORA_ON_SITE`` (2,171) carries both "On-site" (2,116) and "Work
#: From Office" (55). So a substring test against the label misses a spelling a tenant is free to
#: invent, while the code is exact. The two are always present together (2,716 rows each), which
#: is why the label is not consulted at all.
_REMOTE_CODE = "ORA_REMOTE"
_ON_SITE_CODE = "ORA_ON_SITE"


def _remote(listed: dict, detail: dict, location: str | None) -> bool | None:
    """Whether this posting is remote: the tenant's own answer where unambiguous, else the
    location guess.

    Falling back to the location is safe rather than merely convenient when the tenant states
    *no* code at all. Of 2,716 rows stating a workplace type, the two signals disagreed 109
    times and **every one** was the tenant saying remote where the location string did not —
    there is no measured case of a location guess overriding an explicit on-site, which is the
    failure this ordering would otherwise risk.

    A third case — a code that is neither ``ORA_REMOTE`` nor ``ORA_ON_SITE`` (``ORA_HYBRID``, or
    any other value Oracle states) — used to fall through to a bare equality check and read as
    ``False``, conflating "explicitly hybrid" with "explicitly on-site". That's wrong the same
    way it would be wrong on workday/phenom/taleo_be, whose own ``_remote_from``/docstrings map
    hybrid to ``None`` rather than ``False`` for exactly this reason: calling a hybrid posting
    non-remote overstates what the Board said. Neither ``ORA_HYBRID`` nor a plausible
    ``ORA_FULL_TIME_REMOTE`` variant was observed in a live sample of 289 detail payloads across
    2 tenants (aqa.fa.us1, ejwl.fa.us2, 2026-09-22), so this maps *any* such stated-but-unknown
    code to ``None`` rather than guessing what a specific one means — and, since the tenant DID
    state something, this does not fall back to a location guess either: that guess is only
    safe for a posting that stated nothing at all.
    """
    code = listed.get("WorkplaceTypeCode") or detail.get("WorkplaceTypeCode")
    if code == _REMOTE_CODE:
        return True
    if code == _ON_SITE_CODE:
        return False
    if code:
        return None
    return is_remote(location)


class OracleScraper(BaseScraper):
    """Oracle Recruiting Cloud scraper — ``slug`` is the tenant's pod host."""

    ats = "oracle"
    # scraper: f"https://{slug}/hcmUI/CandidateExperience/en/sites/CX_1/job/{id}" where the
    # slug is the tenant's own pod host (fa-etvl-saasfaprod1.fa.ocs.oraclecloud.com,
    # chevron.fa.us2.oraclecloud.com — ten regional pods, so nothing narrower to anchor on).
    # The site segment stays loose because it is cosmetic: browser-verified 2026-09-08, the app
    # redirects any site — including a nonexistent one — to CX_1 and resolves the job by id.
    # The id is NOT numeric: of 10,115 sampled live 2026-09-08, 47 carry underscores
    # (MY_SCA_173_2411) and many are letter-prefixed (N122008), so `\d+` would flag real rows.
    url_shape = r"https://[^/]+/hcmUI/CandidateExperience/[a-z]{2}/sites/[^/]+/job/[A-Za-z0-9_]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    egress_fallback_on = frozenset({429})

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
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
        # the ledger has no such entry and `scrapable_boards.load` can never build one. Guarding
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
        if total and len(reqs) < total:
            if self._offset >= _OFFSET_CEILING:
                # The one genuine incompleteness this walk can suffer below `_MAX_PAGES`: the API
                # serves no offset past 10,000, so the remainder is unreachable on every run
                # rather than a transient miss, and no share of it is negligible however close to
                # `total` the read landed. A Board stating 10,050 and reading 10,000 is 99.5% and
                # still must not be declared authoritative — the class ADR-0121 keeps outside the
                # tolerance. Measured live on `eubt.fa.us6`: 10,000 read of a stated 78,431.
                self.mark_truncated(
                    f"read {len(reqs)} of {total} requisitions — the API serves no offset past "
                    f"{_OFFSET_CEILING:,}, so the rest is unreachable, not absent"
                )
            else:
                # Below the ceiling the walk stopped because a page came back empty, and an empty
                # page is the end of the Board — so this list is whole and a shortfall against
                # `TotalJobsCount` says nothing about it (ADR-0169). The counter is not a count of
                # servable requisitions: measured live 2026-09-21 across 17 Boards, an exhaustive
                # sweep of every offset window up to the stated total found **zero** ids the
                # ordinary walk had missed. The claim rests on the **16** stating under 10,000,
                # where the sweep can look past where the walk stopped; the 17th sits at the
                # ceiling, where it stops at the same wall, so it proves nothing and is excluded.
                # Nine of the 16 were being marked truncated here, at ratios from 27-of-600 to
                # 98-of-123. Truncating on that gap parked complete reads in ADR-0053's exclusion
                # scope, which has no drain, so their closed postings were served indefinitely.
                #
                # Logged rather than silent: the inflation is worth watching, and this is the only
                # place that can see it.
                self._log.info(
                    f"{self.board_key()}: served {len(reqs)} of a stated {total} requisitions — "
                    "the Board ended on an empty page, so the counter over-states it (ADR-0169)"
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
        # Reported, not marked truncated: a missing detail payload costs this Job its
        # description and derived fields, but the Job itself is still listed and still
        # emitted, so the Board's list is whole (ADR-0053 is about the list, not the fields).
        details = self.run_detail_pass(
            reqs,
            key_of=lambda req: str(req["Id"]) if req.get("Id") else None,
            what="detail payloads",
        )
        return {"requisitionList": reqs, "details": details}

    def detail_request(self, req: dict) -> DetailRequest:
        # `ById` with a *quoted* id, taken from the careers UI's own network calls — the
        # plausible-looking `findReqDetailById` returns HTTP 400. No `siteNumber`: it is ignored
        # here, and 454 cross-pod calls omitting it all returned the requisition.
        if not req.get("Id"):
            raise DetailLost("no requisition id")
        return DetailRequest(
            f"https://{self.slug}/hcmRestApi/resources/latest/"
            f'recruitingCEJobRequisitionDetails?onlyData=true&expand=all&finder=ById;Id="{req["Id"]}"'
        )

    def read_detail(self, req: dict, response: Any) -> dict:
        """The one requisition in a detail response.

        An unknown id is **not** a 404 — it answers 200 with ``items: []`` — so an empty list is a
        real outcome to fold into the detail-gap count, not an error to raise on. It is labelled
        rather than merely counted, because a Board whose ids have all gone stale and a Board the
        pod is refusing produce the same number of gaps and call for opposite responses
        (:meth:`~BaseScraper.note_detail_loss`).
        """
        items = json.loads(response.text).get("items") or []
        if not items:
            raise DetailLost("no items on a 200")
        return items[0]

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
                    id=self.job_id(job_id),
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

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for on either the
        # listing or the detail payload. Needs its own measurement pass before this can claim
        # more.
        return None
