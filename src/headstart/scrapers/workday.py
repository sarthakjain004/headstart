"""Workday scraper.

Workday career sites live at:
    https://{company}.{instance}.myworkdayjobs.com/{site}
with an undocumented but stable listing API:
    POST https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs

A company's `slug` here is the full careers URL (the data center / instance and
site vary per tenant, so the URL carries everything we need).

Beating the 2,000 cap (the reason this scraper is more than a paginator):

The API caps ``limit`` at 20 per page and caps the *reported total* at 2,000.
Past offset 2,000 it silently wraps to page 1 — so no amount of paging gets you
more than 2K jobs from a single query. For tenants above that (Accenture ~61K),
we subdivide by a facet ("Area of Work" / ``jobFamilyGroup`` first, then
``timeType``/``locations``/``workerSubType``). Each filtered query has its own
2K ceiling and the union covers the full set; ``facets`` in every response
carries each value's true ``count`` so we can plan the split without probing.

The list endpoint carries no description; a second pass fetches each posting's detail
(GET .../wday/cxs/{company}/{site}{externalPath} -> jobPostingInfo.jobDescription) in a
bounded thread pool to fill it in. A failed detail fetch leaves description None — the job
is still kept.

Adapted from jobhive (kalil0321/ats-scrapers) to this project's shared pooled ``http`` client,
mapped onto our leaner Job. The listing crawl (``_paginate``) and the per-job detail pass both fan
out over a bounded number of concurrent async streams against the same host — see ``_PAGE_STREAMS``
and ``_DETAIL_STREAMS``.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from headstart import http, log, spare_egress
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)


def _failure_class(exc: Exception) -> str:
    """A groupable label for one failed page — the status where the origin gave one, else the
    exception type. Deliberately coarse: the message carries per-request detail (offsets, hosts)
    that would never group, and what a short crawl needs is the *shape* of its failures, not 108
    distinct strings."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"HTTP {status}" if status else type(exc).__name__


_URL_PATTERN = re.compile(
    r"^https://(?P<company>[^.]+)\.(?P<instance>wd\d+)\.myworkdayjobs\.com/(?P<site>[^/?#]+)"
)

# The Workday data centers whose CXS API answers, found by probing which ``*.wdN.myworkdayjobs.com``
# wildcards resolve across wd1-wd1000 and respond (18 as of 2026-07). Tenants migrate between data
# centers; when one does, its old ``wdN`` host 500s and the CXS API 422s, so a board built from the
# stale URL reads as empty. ``_resolve_instance`` sweeps these to recover a migrated board (the
# liveness prober imports this list for the same sweep). Ordered by prevalence in our pool so the
# sweep hits likely instances first. ``wd104`` is deliberately excluded: it is boardless and its CXS
# never answers (a permanent multi-second hang), so it can host no readable board — keeping it only
# hung the sweep and blocked every verdict. Re-run the DNS sweep to extend this as Workday grows.
INSTANCES = (
    "wd1",
    "wd5",
    "wd3",
    "wd12",
    "wd103",
    "wd501",
    "wd503",
    "wd108",
    "wd10",
    "wd105",
    "wd502",
    "wd102",
    "wd115",
    "wd107",
    "wd504",
    "wd116",
    "wd109",
    "wd117",
)

_PAGE_LIMIT = 20  # Workday hard-caps `limit` at 20 (higher returns 400).
_QUERY_TOTAL_CAP = 2000  # total reported as exactly 2000 => capped => subdivide.
_MAX_DEPTH = 4  # recursion bound; Accenture needs depth 3, 4 is a paranoid ceiling.
_DETAIL_WORKERS = 6  # concurrent description fetches; bounded since they hit one host
# The async path's multiplexing width. It had been inheriting the shared 100-stream default while
# the sync path above deliberately held to 6 against the same host — measured cost of that
# divergence over 19 pipeline runs: 3,023,846 429-retries and 1,254,130 of 2,426,147 descriptions
# (51.7%) coming back empty. 25 is the width ADR-0047 measured as safe for Eightfold's comparable
# detail pass; Workday carries ~2.5x the per-shard volume, so this is a starting point to re-measure
# with scripts/bench/probe_eightfold_throttle.py's method, not a settled number.
_DETAIL_STREAMS = 25
# The listing-pagination fan-out's width (see `_paginate`). Reuses `_DETAIL_STREAMS`'s value
# rather than a fresh number: pagination and the detail pass are sequential phases of one board's
# fetch (`fetch_raw` runs `_exhaust` — which calls `_paginate` — to completion before the detail
# pass's `fan_out_async` starts), so this never *stacks* concurrent load on top of what
# `_DETAIL_STREAMS` was measured safe for against the same per-(source IP, instance) ceiling — it
# spends that same ceiling in a second phase, not a second one. Kept as its own name because the
# two passes differ (a POST with a JSON body vs. a bare GET) and may need to diverge once measured
# under real pagination load, the way `_DETAIL_STREAMS` diverged from `_DETAIL_WORKERS`. It is a
# ceiling rather than the width in use: once a 429 has walled this shard's `workday` group,
# `spare_egress.stream_width` narrows the fan-out below it for the rest of the run (#195).
_PAGE_STREAMS = _DETAIL_STREAMS
# How much of one query's pages may come back short before `_paginate` fails the crawl instead of
# reporting it truncated (ADR-0076). A judgement call, not a measurement — nothing records
# per-page failure rates, so there is no distribution to cut at yet; the warning `_paginate` logs
# either way carries the numbers to re-measure this with. Half is where the two ends land on the
# side they belong: one page of five lost to a 429 still ships the other four, while a query that
# loses most of its pages has kept too little to read as those postings — and marking *that*
# truncated would tell `index sync` to preserve rows for a query we barely read.
_MAX_LOST_PAGE_SHARE = 0.5
# Above what share of a Board's details may go missing before the gap is a WARNING rather than an
# INFO count. The same half as `_MAX_LOST_PAGE_SHARE` by analogy, *not* by derivation, and unlike
# it this only picks a log level — it never fails the crawl and never marks the Board truncated.
# ADR-0088 has the reasoning for both halves of that; re-set the number from real
# `failed mid-crawl` lines rather than defending 0.5 on principle.
_MAX_LOST_DETAIL_SHARE = 0.5

# ``remoteType`` is freeform; map the unambiguous values. "hybrid"/"flexible"
# stay None — neither purely remote nor onsite.
_REMOTE_TYPE_PATTERNS = {
    "remote": True,
    "fully remote": True,
    "100% remote": True,
    "work from home": True,
    "telecommute": True,
    "telework": True,
    "on-site": False,
    "onsite": False,
    "in office": False,
    "in-office": False,
    "office": False,
}

# Subdivision dimensions in priority order (see module docstring).
_SUBDIVISION_FACETS = ("jobFamilyGroup", "timeType", "locations", "workerSubType")

# Deliberate, per-board recall narrowing — NOT a subdivision. `_SUBDIVISION_FACETS` above splits
# a capped query into slices whose union is the whole board; this instead fixes facet values from
# the start, so postings outside them are never fetched at all. ADR-0017 is explicit that a
# category facet like `jobFamilyGroup` "cannot be the authoritative tech gate" because department
# taxonomies are inconsistent and a real tech job filed under an odd department would be dropped
# for good, not just deprioritized — the post-hoc filter never gets a chance to see what the scrape
# never fetched. Every board below is an explicit, human-approved exception to that recall
# guarantee, decided one board at a time, not a pattern to extend to a new board without the same
# tradeoff being re-examined against that board's own facet counts.
#
# All six were the single largest contributor to a shard's wall-clock somewhere in the 20 pipeline
# runs surveyed 2026-08-20 (docs/pipeline/2026-08-20_cadence-settle-in-and-critical-path.md §3, §6
# — a shard 90-98% one board), and share the same shape: a retail/ops-dominated board where the
# tech-labeled category is a sliver (0.2-4.5%) of the whole. The label is NOT uniform across
# companies — TJX calls it "Information Technology", not "Technology" — and two boards (Lowe's,
# Loblaw) split tech-adjacent roles across more than one small bucket rather than one; which
# buckets to include past the obvious "Technology" one is a human call, made explicitly per board
# below, not inferred. All counts and ids live-checked 2026-08-20 against each board's own API.
_FIXED_FACETS_BY_SLUG: dict[str, dict[str, list[str]]] = {
    # 19,272 -> 823. Business Services 6,970 and Business Operations 6,553 are the two biggest
    # categories this gives up, plus Part time (5,829 of 19,272 total across the board).
    "https://walmart.wd504.myworkdayjobs.com/WalmartExternal": {
        "jobFamilyGroup": ["e83ebdbd2a0a01e7e1477a8948e904c6"],  # "Technology"
        "timeType": ["b181d8271e36017533d4ca68eee44f00"],  # "Full time"
    },
    # 19,283 -> 187. Retail Store & Pharmacy 12,084 and "Internships and Devlpmt Pgms" 4,055 are
    # the two biggest categories this gives up, plus Part time (11,649).
    "https://cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers": {
        "jobFamilyGroup": ["e65dbadf6a50100168ed86fe4cf50001"],  # "Technology"
        "timeType": ["1aea6da227e21005504339b6b1770001"],  # "Full time"
    },
    # ~11,900 -> 118. "Stores" (11,681) is nearly the whole board. Every one of Technology's 118
    # postings is independently confirmed Full time (0 are the dominant "Variable" timeType), so
    # this filter combination costs nothing extra beyond the category narrowing itself.
    "https://target.wd5.myworkdayjobs.com/targetcareers": {
        "jobFamilyGroup": ["daccab9f1d25018677ebcc363457460e"],  # "Technology"
        "timeType": ["dbf9619e323f012ffc1de2db2a574a00"],  # "Full time"
    },
    # 10,349 -> 63. TJX's own taxonomy has no "Technology" label at all — "Information Technology"
    # is the one that holds its software roles. "Business & Store Operations" (9,928) is nearly
    # the whole board this gives up, plus Part time (6,726).
    "https://tjx.wd1.myworkdayjobs.com/TJX_EXTERNAL": {
        "jobFamilyGroup": [
            "7d770955258e1000a7fd6e81b64e0000"
        ],  # "Information Technology"
        "timeType": ["d5866b796ae0101de252d60f8cb10000"],  # "Full time"
    },
    # 12,438 -> 34. Loblaw's tenant is on the shared `myview.wd3` host under the `paradox_careers`
    # site. "Technology" (25) alone would miss real engineering roles filed under "Digital &
    # Ecommerce" (9) instead — included on purpose, not everything small was. "Retail" (11,670) is
    # the board this gives up, plus Part time (10,651).
    "https://myview.wd3.myworkdayjobs.com/paradox_careers": {
        "jobFamilyGroup": [
            "1e109731718b4ca4ada8d4d49b59afe0",  # "Technology"
            "e128c069551e1086c1a3b8c2616fbf98",  # "Digital & Ecommerce"
        ],
        "timeType": ["f9ccda084af243cebebf4538f57811ab"],  # "Full time"
    },
    # 12,029 -> 73. Split the same way as Loblaw: "Technology" (42) plus "Digital" (27) and
    # "IND_Digital" (4) — the other "IND-"-prefixed buckets (Finance, HR, Legal, Marketing,
    # Merchandising, Supply Chain) are left out on purpose, they are not tech-adjacent. Every other
    # "IND_"/"IND-" naming is a leftover of two merged taxonomies, not a typo to "fix" here.
    # "Store Operations" (11,542) is the board this gives up, plus Part time (9,230).
    "https://lowes.wd5.myworkdayjobs.com/LWS_External_CS": {
        "jobFamilyGroup": [
            "02b0c958653f01d9810ce8fafb055745",  # "Technology"
            "02b0c958653f01dc63f0e6fafb055345",  # "Digital"
            "afb941e45f461009d3a1165a2df20000",  # "IND_Digital"
        ],
        "timeType": ["db5c129996dd0110db0aab26c2038100"],  # "Full time"
    },
}


class WorkdayScraper(BaseScraper):
    """Workday scraper — `slug` must be the full careers URL."""

    ats = "workday"
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_STREAMS

    #: **Provisional experiment** (ADR-0063, amended). Unlike Eightfold's 403/405 — a hard wall
    #: with no signal — a 429 is the origin telling us politely to slow down, and ADR-0026 makes
    #: honouring that binding. It is here anyway because the metering was measured to be per
    #: (source IP x instance host): a shard's failure rate tracks *its own* load on that instance
    #: (wd1 17.9% at 10-19 Boards, 36.0% at 40-49), so a second egress is a second allocation
    #: rather than a way of ignoring the first. Retry and `Retry-After` are still honoured first;
    #: this is only what happens after the ladder is spent.
    #:
    #: Expect it to fire on nearly every shard — Workday 429s are pervasive, where Eightfold's
    #: wall touched 18-30 Boards a run. Watch the shard report's recovered rate; if it is low, the
    #: spare egress is saturated too and this should come back out.
    #:
    #: **That exit criterion is blind, and nothing has replaced it yet** (ADR-0063's 2026-08-26
    #: amendment, found while reverting personio's). `note_settled` buckets every request the
    #: spare egress carries once the group is walled, so the rate is pinned high by healthy Boards
    #: that were never refused — it cannot fall, whether or not the fallback is working. This
    #: opt-in **stands**: it rests on the per-(source IP x instance host) table above, not on that
    #: rate. But do not read a high recovered rate as confirmation; only a per-Board outcome is.
    egress_fallback_on = frozenset({429})
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        # The data center actually serving the tenant. None until resolved; overrides the URL's
        # ``wdN`` when the tenant has migrated (see :meth:`_resolve_instance`).
        self._instance: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return url.rstrip("/")  # the full https://{co}.{inst}.myworkdayjobs.com/{site}

    def board_key(self) -> str:
        # ids are ``workday:{company}/{site}:{ats_id}`` (see parse), not ``workday:{full-url}`` —
        # so the Board key derives {company}/{site} from the URL slug, matching ``board_of``.
        company, _instance, site = self._parts()
        return f"{self.ats}:{company}/{site}"

    def url(self) -> str:
        company, instance, site = self._parts()
        return (
            f"https://{company}.{instance}.myworkdayjobs.com"
            f"/wday/cxs/{company}/{site}/jobs"
        )

    def _parts(self) -> tuple[str, str, str]:
        match = _URL_PATTERN.match(self.slug.rstrip("/"))
        if not match:
            raise ValueError(
                "Workday slug must be a careers URL like "
                f"https://{{co}}.wdN.myworkdayjobs.com/{{site}} — got {self.slug!r}"
            )
        instance = self._instance or match.group("instance")
        return match.group("company"), instance, match.group("site")

    def _resolve_instance(self) -> None:
        """Point this scrape at the data center currently serving the tenant.

        Workday tenants migrate between data centers; when one does, the URL's ``wdN`` goes stale
        (its host 500s, the CXS API 422s) and the board would read as empty. Probe the URL's instance
        first, then sweep the known data centers, caching the first that answers so :meth:`url` and
        :meth:`_detail_url` follow it. Leaves the URL's instance untouched if none serves the board —
        the crawl then yields no jobs, as before."""
        company, hinted, site = (
            self._parts()
        )  # self._instance is None here -> the URL's instance

        def serves(instance: str) -> bool:
            probe_url = (
                f"https://{company}.{instance}.myworkdayjobs.com"
                f"/wday/cxs/{company}/{site}/jobs"
            )
            try:
                response = http.fetch(
                    "POST",
                    probe_url,
                    **self._egress(),
                    json={
                        "appliedFacets": {},
                        "limit": 1,
                        "offset": 0,
                        "searchText": "",
                    },
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
            except http.RequestsError:
                return False
            return response.status_code == 200

        if serves(hinted):
            return  # fast path: the URL's data center is current
        for instance in INSTANCES:
            if instance != hinted and serves(instance):
                self._instance = instance
                return

    def _post(
        self,
        applied_facets: dict[str, list[str]],
        offset: int,
        *,
        raise_gone: bool = False,
    ) -> dict[str, Any] | None:
        """POST one page of the jobs query (retry lives in fetch). Returns the JSON dict, or
        None on 404 — except with ``raise_gone``, where the 404 raises like any other error.

        The split is per call site: mid-crawl (later pages, subdivided slices) a 404 is one
        page of a live board and the caller degrades to a *reported* partial (``_paginate``'s
        truncation). On the very first page of the whole board it means the site is gone, and
        returning None there read as an empty board — hiding dead boards from the ADR-0058
        quarantine forever, because only a raised 404/410 counts as a gone-verdict.
        """
        body = {
            "appliedFacets": applied_facets,
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = http.fetch(
            "POST", self.url(), json=body, headers=headers, timeout=30, **self._egress()
        )
        if response.status_code == 404 and not raise_gone:
            return None  # one page of a live board — the caller reports the gap
        response.raise_for_status()
        return response.json()

    async def _post_async(
        self, session: Any, applied_facets: dict[str, list[str]], offset: int
    ) -> dict[str, Any] | None:
        """Async counterpart to :meth:`_post`, for the concurrent mid-crawl pages
        :meth:`_paginate` fans out. Only ever called for offset > 0 — the first page of every
        slice still goes through the sync :meth:`_post` inside :meth:`_exhaust`, which needs
        ``raise_gone`` — so this always takes :meth:`_post`'s ``raise_gone=False`` path."""
        body = {
            "appliedFacets": applied_facets,
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = await http.fetch_async(
            session,
            "POST",
            self.url(),
            json=body,
            headers=headers,
            timeout=30,
            **self._egress(),
        )
        if response.status_code == 404:
            return None  # one page of a live board — the caller reports the gap
        response.raise_for_status()
        return response.json()

    def fetch_raw(self) -> Any:
        """Crawl the tenant (paginate + recursively subdivide capped queries) and
        return a flat, de-duplicated list of raw posting dicts."""
        self._resolve_instance()  # follow data-center migrations before crawling
        seen: set[str] = set()
        postings: list[dict[str, Any]] = []

        def absorb(batch: list[dict[str, Any]]) -> None:
            for item in batch:
                key = _posting_key(item)
                if key in seen:
                    continue
                seen.add(key)
                postings.append(item)

        self._exhaust(_FIXED_FACETS_BY_SLUG.get(self.slug, {}), absorb, depth=0)
        # Second pass: fill each posting's detail fields concurrently (bounded); a failed
        # fetch leaves ``_detail`` empty so the job is still kept.
        # What the lost details were lost to. The count on its own cannot tell a throttled
        # Board from a tenant whose detail URLs 404 from a spare egress that went away
        # mid-pass — and those want opposite responses. Counted single-threaded on the async
        # path (one event loop per Board); on the `HEADSTART_ASYNC_FANOUT=0` fallback these
        # increments race across `fan_out`'s worker threads, so treat that path's breakdown as
        # a shape rather than an exact tally.
        classes: Counter[str] = Counter()
        if self.async_fanout_enabled():
            details = self.fan_out_async(
                postings,
                lambda session, item: self._job_detail_async(
                    session, item.get("externalPath"), classes
                ),
            )
        else:
            details = self.fan_out(
                postings,
                lambda item: self._job_detail(item.get("externalPath"), classes),
                workers=_DETAIL_WORKERS,
            )
        self._report_detail_losses(details, classes)
        for item, detail in zip(postings, details):
            item["_detail"] = detail or {}
        return postings

    def _detail_url(self, external_path: str) -> str:
        company, instance, site = self._parts()
        return (
            f"https://{company}.{instance}.myworkdayjobs.com"
            f"/wday/cxs/{company}/{site}{external_path}"
        )

    @staticmethod
    def _extract_detail(response: Any) -> dict[str, Any] | None:
        """The useful jobPostingInfo fields from a posting-detail response (None on non-200):
        the raw-HTML description, plus startDate/timeType — the list payload only carries a
        relative posted date ("30+ Days Ago") and no employment type — plus the real
        location(s), country and remoteType, which the list payload rolls up or omits (see
        :func:`_location_from`)."""
        if response.status_code != 200:
            return None
        info = response.json().get("jobPostingInfo") or {}
        country = info.get("country")
        return {
            "description": info.get("jobDescription"),
            "startDate": info.get("startDate"),
            "timeType": info.get("timeType"),
            "location": info.get("location"),
            "additionalLocations": info.get("additionalLocations"),
            "country": country.get("descriptor") if isinstance(country, dict) else None,
            "remoteType": info.get("remoteType"),
        }

    def _job_detail(
        self, external_path: str | None, classes: Counter[str] | None = None
    ) -> dict[str, Any] | None:
        """GET one posting's detail fields (None on failure). Sync path.

        ``classes`` records *what* a lost detail was lost to — see :meth:`_note_detail`."""
        if not external_path:
            self._note_detail(classes, "no externalPath")
            return None
        try:
            response = http.fetch(
                "GET",
                self._detail_url(external_path),
                timeout=30,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                **self._egress(),
            )
        except http.RequestsError as exc:
            self._note_detail(classes, _failure_class(exc))
            return None  # a missing detail must not drop the job
        if response.status_code != 200:
            self._note_detail(classes, f"HTTP {response.status_code}")
        return self._parsed_detail(response, classes)

    async def _job_detail_async(
        self,
        session: Any,
        external_path: str | None,
        classes: Counter[str] | None = None,
    ) -> dict[str, Any] | None:
        """Same as :meth:`_job_detail` but over the shared multiplexed ``AsyncSession``."""
        if not external_path:
            self._note_detail(classes, "no externalPath")
            return None
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(external_path),
                timeout=30,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                **self._egress(),
            )
        except http.RequestsError as exc:
            self._note_detail(classes, _failure_class(exc))
            return None
        if response.status_code != 200:
            self._note_detail(classes, f"HTTP {response.status_code}")
        return self._parsed_detail(response, classes)

    @staticmethod
    def _note_detail(classes: Counter[str] | None, label: str) -> None:
        """Record one lost detail under ``label``, or do nothing when nobody is counting.

        A detail can go missing four ways that call for four different responses — a settled
        4xx, a settled 5xx, a request that outlived its retry ladder, and a posting the listing
        gave no ``externalPath`` for — and until this existed all four returned the same
        untyped None. Optional because `scripts/enrich/salary_sample.py` and the tests call the
        detail fetchers directly for one posting, where there is no pass to summarise.
        """
        if classes is not None:
            classes[label] += 1

    def _parsed_detail(
        self, response: Any, classes: Counter[str] | None
    ) -> dict[str, Any] | None:
        """:meth:`_extract_detail` with its likeliest raised loss counted rather than escaping.

        A 200 whose body is not the JSON the API promises makes ``response.json()`` raise, and
        both fan-outs turn a raising item into ``None`` — so before this the posting counted as
        lost while naming no class, which is precisely the untyped ``None`` this change exists to
        remove. ``scripts/bench/probe_workday_detail.py`` already bucketed it as ``unparseable``;
        the scraper now agrees with its own probe.

        ``ValueError`` deliberately, not bare ``except``: it covers what a malformed body raises
        (``JSONDecodeError`` and ``UnicodeDecodeError`` both subclass it) without swallowing a
        real defect. A body that parses but isn't an object raises ``AttributeError`` instead and
        is left alone — rarer, and it lands in the ``unclassified`` bucket rather than vanishing.
        """
        try:
            return self._extract_detail(response)
        except ValueError:
            self._note_detail(classes, "unparseable")
            return None

    def _report_detail_losses(
        self, details: Sequence[Any], classes: Counter[str]
    ) -> None:
        """Log what a detail pass's gaps actually were, once per Board, or nothing if it had none.

        Deliberately worded to :meth:`_paginate`'s shape — ``N of M thing(s) failed mid-crawl
        (class xN, …) — tail`` — so one regex reads the listing and detail passes alike and the
        noun says which pass it was. This replaces :meth:`~BaseScraper.report_detail_gaps`'s
        count-only line rather than adding to it: the two carried the same two numbers, and a
        second near-synonym line ("missing" beside "lost") both read as a different fact and
        double-counted every Board for anything grepping them.

        The tail is the standing answer to "do rows get evicted over this?", and is deliberately
        narrow: it says only that *this* pass does not mark the Board truncated — unconditionally
        true — and points at the pass that would. It claims neither that the listing was whole
        (`_paginate` can `mark_truncated` and return, so one Board can lose pages *and* details in
        a run) nor that the loss is harmless — it still costs ADR-0021's null fields and an
        ADR-0050 gap entry. It no longer costs *identity*: :func:`_posting_key` read the detail's
        ``jobReqId`` until ADR-0097, so a lost detail used to *rename* the Job rather than merely
        under-fill it. ADR-0088 has both arguments and why neither widens this line's claim.
        """
        missing = sum(1 for detail in details if detail is None)
        if not missing:
            return
        # Every label is recorded on a path that also yields None, so the tally can only fall
        # *short* of `missing` — and it does whenever a loss escaped labelling. Naming the
        # remainder stops "(HTTP 404 x10)" on a 3,536-loss Board reading as the explanation.
        tally = Counter(classes)
        unclassified = missing - sum(tally.values())
        if unclassified > 0:
            tally["unclassified"] = unclassified
        # Only the four largest are shown (as `_paginate` does — the shape is what's wanted, not
        # a long tail), so the trailing "…" marks the times that is a sample rather than the
        # whole tally. Without it a truncated list reads as a complete account of `missing`.
        shown = tally.most_common(4)
        why = ", ".join(f"{cls} x{n}" for cls, n in shown)
        if len(tally) > len(shown):
            why += ", …"
        report = (
            _log.warning
            if missing / len(details) > _MAX_LOST_DETAIL_SHARE
            else _log.info
        )
        report(
            f"{self.board_key()}: {missing} of {len(details)} detail(s) failed mid-crawl"
            + (f" ({why})" if why else "")
            + " — not a truncation (the listing pass reports its own)"
        )

    def _exhaust(self, applied: dict[str, list[str]], absorb, depth: int) -> None:
        """Exhaust one filter combination: paginate normally, or subdivide when the
        2,000 cap is hit and a fresh facet is available."""
        # Depth 0 raises on 404 (site gone — ADR-0058 needs the error); a subdivided slice
        # keeps the None path so one vanished slice degrades to a reported truncation while
        # its siblings' postings still ship.
        first = self._post(applied, offset=0, raise_gone=not depth)
        if not first:
            if depth:
                # A subdivided slice that 404s on its first page is dropped whole, while the
                # Board still emits every posting its sibling slices found — so it stays in the
                # eviction scope and sync reads the vanished slice as delistings (ADR-0053).
                self.mark_truncated(
                    f"no first page for {_slice_label(applied)} — "
                    "none of that slice's postings were read"
                )
            return
        total = int(first.get("total", 0))
        absorb(first.get("jobPostings") or [])
        if total <= _PAGE_LIMIT:
            return

        capped = total == _QUERY_TOTAL_CAP
        facet = (
            _pick_subdivision_facet(first.get("facets") or [], set(applied))
            if capped and depth < _MAX_DEPTH
            else None
        )
        if capped and facet is None:
            # The reported total sticks at exactly 2,000 while the real one is higher, and with
            # no facet left to split there is no second query to reach the rest — so this
            # paginates 2,000 of a knowingly larger board. Eightfold's page ceiling in Workday
            # form, and it must be reported the same way (ADR-0053).
            self.mark_truncated(
                f"{_slice_label(applied)} capped at {_QUERY_TOTAL_CAP} with no facet left "
                "to split — postings past the cap were not read"
            )
        if facet is None:  # not capped, or capped with nothing left to split
            self._paginate(applied, total, absorb)
            return

        param, values = facet
        _log.debug(
            f"{self.board_key()}: total {total} is capped — subdividing by {param} "
            f"into {len(values)} queries (depth {depth + 1})"
        )
        for value_id, _count in values:
            sliced = {**applied, param: [value_id]}
            try:
                self._exhaust(sliced, absorb, depth + 1)
            except http.RequestsError as exc:
                # One slice of a union, not the board: its siblings' postings are already
                # absorbed and are real. Costing them a slice's 429 is what #194 is about, and
                # on a capped board every page after the first is fetched down here — so
                # without this the fix reaches nvidia, Walmart and every other subdivided board
                # not at all. Same trade the first-page 404 branch above already makes: the
                # slice is dropped whole and reported, never quietly.
                self.mark_truncated(
                    f"{_slice_label(sliced)} failed ({exc}) — "
                    "none of that slice's postings were read"
                )

    def _paginate(self, applied: dict[str, list[str]], total: int, absorb) -> None:
        """Page through offsets [20, total), fanned out over at most ``_PAGE_STREAMS`` concurrent
        streams (mirrors :meth:`fan_out_async`'s bounded-semaphore/shared-session shape, as its
        own small gather rather than a call to it — see :meth:`_paginate_async`). A page that
        404s or spends its retry ladder mid-crawl is skipped, and one warning reports how many
        went missing — the tripwire for a truncated list — unless more than
        ``_MAX_LOST_PAGE_SHARE`` of the query's pages went that way, which is a failed crawl
        rather than a truncated one and raises (ADR-0076).

        Falls back to the old one-at-a-time :meth:`_post` loop when
        :meth:`~BaseScraper.async_fanout_enabled` says no (``HEADSTART_ASYNC_FANOUT=0``): that
        is this codebase's one incident-response kill switch for async traffic against an ATS
        (ADR-0016), already honoured by this file's own detail pass and five other scrapers'.
        Pagination staying async regardless would leave "stop all async requests to Workday"
        only half true, on what is now the *larger* share of that traffic.
        """
        offsets = range(_PAGE_LIMIT, total, _PAGE_LIMIT)
        if not offsets:
            return
        # What the failed pages actually were. `missing` alone cannot tell throttling from a
        # dead host from a mid-crawl 404, and that is the first question asked of every
        # short crawl — answering it previously meant opening the shard log and reading
        # `scrape_run`'s run-wide retry totals, which are shared across every Board in the shard
        # and so cannot be attributed to this one.
        classes: Counter[str] = Counter()
        if self.async_fanout_enabled():
            missing, error = asyncio.run(
                self._paginate_async(applied, offsets, absorb, classes)
            )
        else:
            missing, error = self._paginate_sync(applied, offsets, absorb, classes)
        if not missing:
            return
        # Page 1 is in the denominator because it is in hand: :meth:`_exhaust` fetched it before
        # calling this, and it is as much a page of ``total`` as the ones fanned out here.
        # Counting only ``offsets`` reads a 21-40 posting query — one page here, three of
        # nvidia's fifteen slices — as 100% lost the moment its single page 429s, which is the
        # whole of what #194 asked this not to do.
        page_count = len(offsets) + 1
        why = ", ".join(f"{cls} x{n}" for cls, n in classes.most_common(4))
        shortfall = f"{missing} of {page_count} page(s) failed mid-crawl" + (
            f" ({why})" if why else ""
        )
        if missing / page_count > _MAX_LOST_PAGE_SHARE:
            _log.warning(
                f"{self.board_key()}: {shortfall} — too little of {total} listed read to keep"
            )
            # Re-raise what the origin actually said rather than a fresh exception of our own:
            # `board_failures._GONE` reads the "HTTP Error {code}" text out of the recorded
            # reason to tell a vanished board from a throttled one, and a generic error would
            # read as neither. Only an all-404 majority arrives with nothing to re-raise.
            raise error or RuntimeError(
                f"{shortfall} — too little of {total} listed postings was read"
            )
        _log.warning(
            f"{self.board_key()}: {shortfall} — Board unauthoritative this run "
            f"({total} listed)"
        )
        # The warning was the whole record until ADR-0053: `index sync` could not see it, so
        # the pages this dropped were evicted as delistings. Now it travels with the Jobs.
        self.mark_truncated(f"{shortfall} of {total} listed postings")

    async def _paginate_async(
        self,
        applied: dict[str, list[str]],
        offsets: range,
        absorb,
        classes: Counter[str],
    ) -> tuple[int, http.RequestsError | None]:
        """Fetch every offset in ``offsets`` concurrently over one shared ``AsyncSession``,
        bounded to at most ``_PAGE_STREAMS`` in flight — narrower once the origin has walled this
        shard (:func:`~headstart.spare_egress.stream_width`) — and return how many pages came back
        short together with the first request error that made one (None when only 404s did).

        Not a call to :meth:`fan_out_async`: that method's per-item contract swallows *every*
        exception into ``default``, which would lose the one thing :meth:`_paginate` decides on
        — whether enough of the query was read to be worth keeping — and would swallow an
        unexpected exception with it. A 404 (handled inside :meth:`_post_async`) and a request
        error that outlived ``fetch_async``'s retry ladder are both one page of a board whose
        other pages arrived, so both are counted rather than raised here; anything else is a bug
        in this code, not a page that failed, and propagates.

        ``absorb`` runs on the event loop's single thread, one call at a time, so concurrent
        callers can't race its ``seen``/``postings`` state the way concurrent OS threads would —
        no lock needed. Nothing here orders it by offset; the postings it's building are
        deduplicated and looked up by id, never by position, so completion order is irrelevant.
        """
        from curl_cffi.requests import AsyncSession

        # The same clamp `fan_out_async` applies to its own resolved width, at the second call
        # site rather than through it: this gather exists precisely because that method's
        # exception contract is not the one `_paginate` needs (above), so the width policy is
        # shared as a function and the two fan-outs stay apart (#195).
        sem = asyncio.Semaphore(
            spare_egress.stream_width(self._egress().get("egress_group"), _PAGE_STREAMS)
        )
        missing = 0
        error: http.RequestsError | None = None

        async with AsyncSession(impersonate="chrome") as session:

            async def one(offset: int) -> None:
                nonlocal missing, error
                async with sem:
                    try:
                        payload = await self._post_async(session, applied, offset)
                    except http.RequestsError as exc:
                        missing += 1
                        classes[_failure_class(exc)] += 1
                        error = error or exc
                        return
                    if payload is None:
                        missing += 1
                        classes["404 mid-crawl"] += 1
                    absorb((payload or {}).get("jobPostings") or [])

            await asyncio.gather(*(one(offset) for offset in offsets))
        return missing, error

    def _paginate_sync(
        self,
        applied: dict[str, list[str]],
        offsets: range,
        absorb,
        classes: Counter[str],
    ) -> tuple[int, http.RequestsError | None]:
        """The pre-concurrency fallback: walk ``offsets`` one at a time via the sync
        :meth:`_post`, exactly as :meth:`_paginate` did before it fanned out. Only reached
        when :meth:`~BaseScraper.async_fanout_enabled` is off — and it counts a failed page the
        same way :meth:`_paginate_async` does, so flipping the kill switch changes how the pages
        are fetched, never how much of a struggling board survives."""
        missing = 0
        error: http.RequestsError | None = None
        for offset in offsets:
            try:
                payload = self._post(applied, offset=offset)
            except http.RequestsError as exc:
                missing += 1
                classes[_failure_class(exc)] += 1
                error = error or exc
                continue
            if payload is None:
                missing += 1
                classes["404 mid-crawl"] += 1
            absorb((payload or {}).get("jobPostings") or [])
        return missing, error

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        company, _instance, site = self._parts()
        display = (
            self.company if self.company and self.company != self.slug else company
        )
        base = self.slug.rstrip("/")

        jobs: list[Job] = []
        for item in raw:
            external_path = item.get("externalPath") or ""
            ats_id = _posting_key(item)
            detail = item.get("_detail") or {}
            location = _location_from(item.get("locationsText"), detail)
            # ``remoteType`` is absent on ~99% of listings; when it's silent, the detail's own
            # remoteType is tried (present on 25% of them against the listing's 5%), and only
            # then does the location string decide ("Remote - Colombia", "US, Remote"). A
            # decisive listing remoteType still wins over both.
            remote = _remote_from(item.get("remoteType"))
            if remote is None:
                remote = _remote_from(detail.get("remoteType"))
            if remote is None and not _is_rollup(location):
                # A rollup that survived — the detail never arrived — must not decide this.
                # ``is_remote("3 Locations")`` returns False, which asserts on-site when the
                # honest answer is that we cannot tell.
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=f"{self.ats}:{company}/{site}:{ats_id}",
                    ats=self.ats,
                    company=display,
                    title=(item.get("title") or "Untitled").strip(),
                    location=location,
                    remote=remote,
                    department=(item.get("jobFamilyGroup") or "").strip() or None,
                    url=f"{base}{external_path}" if external_path else base,
                    # the list only gives relative strings ("30+ Days Ago"); the detail
                    # JSON carries the absolute date
                    posted_at=detail.get("startDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("description")),
                    employment_type=item.get("timeType") or detail.get("timeType"),
                )
            )
        return jobs


# A requisition id's shape, live-validated against 1,029 real listing items across 129 tenants
# (docs/pipeline/2026-08-23_false-board-eviction-root-cause.md §6, option A1): a short
# letter-prefix plus digits (``JR00258``, ``R-0012714``, ``PT-JR042569``, ``REQ2026 - 9929``), a
# digit-then-letter-suffix form (``2409195-R``), or a bare 4+-digit number. ``bulletFields[0]`` —
# what the code used to trust unconditionally — was never actually the req id on any of 25 boards
# checked live: a location, a relative posted-date, a closing-date label, an employment-type tag,
# a store name, or a subsidiary name, all of which change value across scrapes for the same live
# posting and so were silently evicted-then-reinserted every run.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{1,2}$")
_REQ_ID_SHAPE = re.compile(
    r"^(?:[A-Za-z]{1,5}[-_ ]?){1,2}\d[\dA-Za-z]*(?:[-_ ]+\d[\dA-Za-z]*)*$"
    r"|^\d[\dA-Za-z]*[-_][A-Za-z]{1,3}$"
    # Three digit-leading shapes the 129-tenant sample above did not contain, each measured live
    # on the board that needed it. They matter more than their rarity suggests: while these were
    # rejected, `_posting_key`'s listing tier disagreed with the detail's `jobReqId`, so a lost
    # detail *renamed* the posting rather than merely under-filling it — 58% of all index
    # flapping across 12 runs (ADR-0097).
    r"|^\d{6,}[-_]\d{3,}$"  # roche: `202607-119609` (YYYYMM-serial). Six digits, not five,
    # so a bare US ZIP+4 (`12345-6789`) cannot reach this — bulletFields carries addresses.
    r"|^\d{4,}[A-Za-z]{1,3}$"  # pwc/crm: `726071WD`
    # Exactly two letters, not two-or-three: `\d{2,}[A-Za-z]{3}\d{4,}` also admits a `10JAN2026`
    # closing-date label, which the module comment above records living in bulletFields and which
    # is shared across a tenant's postings — the collision this ordering exists to avoid.
    r"|^\d{2,}[A-Za-z]{2}\d{4,}$"  # autodesk: `26WD100347`
)
_BARE_NUMERIC = re.compile(r"^\d+$")


def _looks_like_req_id(field: str) -> bool:
    field = field.strip()
    if not field or _ISO_DATE.match(field):
        return False
    if _BARE_NUMERIC.match(field):
        # A short bare number is as likely to be a stray flag/count as a req id (measured: a
        # "0"/"1" bulletFields entry on one tenant that isn't one) — 4+ digits before trusting it.
        return len(field) >= 4
    return bool(_REQ_ID_SHAPE.match(field))


def _posting_key(item: dict[str, Any]) -> str:
    """Stable per-posting id, computed **only from the listing** — never from the per-job detail
    response. Prefers, in order: the ``bulletFields`` entry shaped like a requisition id, wherever
    it falls in the array — never a fixed index, which varies by tenant (index 1 on most affected
    tenants, index 2 on others); the ``externalPath`` tail — Workday's own URL slug,
    ``{title}_{req-id-or-similar}``, so specific to one posting by construction;
    ``bulletFields[0]`` dead last, only when ``externalPath`` is itself empty.

    **The detail's own ``jobReqId`` used to outrank all three, and must not** (ADR-0097). It is
    only present once :meth:`WorkdayScraper.parse` runs, after a detail fetch that fails for
    68-97% of a board's postings on a bad run — so preferring it made a posting's identity depend
    on whether an optional network call succeeded. A lost detail did not leave a posting
    *missing*; it *renamed* it, and the old id was evicted on its second consecutive absence
    (ADR-0083) and re-added the moment the detail pass recovered. ADR-0088 predicted this exact
    failure here and deferred the fix to this function.

    ``externalPath`` outranks ``bulletFields[0]`` here — tempting to reach for first since it at
    least came from the same array the real req id sometimes lives in — because live tenants
    exist (``tutorperini``, ``nkg``) where *every* posting shares one ``bulletFields[0]`` value
    (their employer/subsidiary name), collapsing hundreds of distinct postings onto a handful of
    ids: measured live, 228 real ``tutorperini`` postings resolved to just 15 distinct ids under
    the bulletFields[0]-preferred order — an id *collision*, actively worse than the instability
    this function exists to fix, since a collision silently drops postings rather than merely
    cycling their id."""
    bullet_fields = item.get("bulletFields") or []
    candidates = [
        field
        for field in bullet_fields
        if isinstance(field, str) and _looks_like_req_id(field)
    ]
    if candidates:
        return re.sub(r"\s+", "", candidates[0])
    tail = (item.get("externalPath") or "").rsplit("/", 1)[-1]
    if tail:
        return tail
    bullet = (bullet_fields or [None])[0]
    return str(bullet) if bullet else "unknown"


def _slice_label(applied: dict[str, list[str]]) -> str:
    """Name the filter combination a query stands for, so a truncation says *which* query
    came up short — the crawl subdivides, and "the board was partial" alone does not locate it."""
    return (
        ", ".join(
            f"{param}={'/'.join(values)}" for param, values in sorted(applied.items())
        )
        or "the unfiltered query"
    )


# "5 Locations" — what a multi-location posting's ``locationsText`` says instead of a place.
_LOCATION_ROLLUP = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def _is_rollup(text: Any) -> bool:
    """Is this ``locationsText`` a count of places rather than a place?"""
    return isinstance(text, str) and bool(_LOCATION_ROLLUP.match(text))


def _location_from(listed: Any, detail: dict[str, Any]) -> str | None:
    """The posting's real location(s), preferring the listing and repairing it from the detail,
    then appending the country (see :func:`_with_country`) to whichever branch produced a real
    place — an unrepaired rollup stays exactly that string, so callers can still recognize it.

    ``locationsText`` is a *rollup* on multi-location postings ("5 Locations") and null outright
    on some tenants — measured 2026-08-18 across 800 listing rows on 40 boards: 9.5% rolled up
    (23.1% on the largest boards), and Accenture null on 60/60. Shipping either is worse than
    cosmetic: it is what the location filter matches on (ADR-0024) and what remote detection
    falls back to, and ``is_remote("2 Locations")`` returns *False* — asserting on-site rather
    than admitting it does not know.

    The detail response is already fetched for the description, so the repair costs no request:
    its ``location`` plus ``additionalLocations`` gave a real place for 45/45 sampled rollups,
    the count matching the rollup every time. All of them are joined, not just the primary —
    the filter is a substring ``LIKE``, so a posting open in five cities should match all five
    (measured spread: 154/200 single-location, max 5, joined length p90 60 chars).
    """
    listing = listed.strip() if isinstance(listed, str) and listed.strip() else None
    if listing and not _is_rollup(listing):
        location = listing
    else:
        primary = detail.get("location")
        if not isinstance(primary, str) or not primary.strip():
            # No detail (a failed fetch keeps the Job anyway) — the rollup is still what the
            # listing said, and saying "5 Locations" beats saying nothing.
            location = listing
        else:
            # `isinstance(..., list)` on the container, not only its items: `or []` over a bare
            # string iterates it character by character and would join "Dublin" as
            # "D; u; b; l; i; n".
            extra = detail.get("additionalLocations")
            places = [
                p.strip()
                for p in (extra if isinstance(extra, list) else [])
                if isinstance(p, str) and p.strip()
            ]
            location = "; ".join([primary.strip(), *places])
    if _is_rollup(location):
        # Still an unrepaired rollup ("2 Locations") — leave it exactly that shape. parse()'s
        # remote-detection guard keys on `_is_rollup` matching this literal string; appending a
        # country here would silently defeat it and flip an honest "don't know" into "on-site".
        return location
    return _with_country(location, detail.get("country"))


def _with_country(location: str | None, country: Any) -> str | None:
    """Append ``jobPostingInfo.country.descriptor`` to ``location`` when it isn't already named
    there, case-insensitively.

    Populated on 99.06% of detail records and was never copied in
    (experiment/location-audit-2026-08-25/workday.md, 700 boards / 5,460 listings live-sampled
    2026-08-25): 81.45% of served locations named no country at all, and 26.37% ended in a bare
    US-state code a country filter can't match. The detail response is already fetched for the
    description, so this costs no extra request. The free-text "Location contains" box is a raw
    substring ``LIKE`` (ADR-0024); ``geo.where()`` layers ``NOT LIKE`` exclusion guards on top of
    the same substring match. Neither cares whether a term appears once or twice, so this guard is
    for a clean served string, not filter correctness — a country already named in ``location``
    should not be repeated.
    """
    if not location or not isinstance(country, str) or not country.strip():
        return location
    country = country.strip()
    if country.lower() in location.lower():
        return location
    return f"{location}; {country}"


def _remote_from(remote_type: Any) -> bool | None:
    if not isinstance(remote_type, str) or not remote_type.strip():
        return None
    norm = remote_type.strip().lower()
    if norm in _REMOTE_TYPE_PATTERNS:
        return _REMOTE_TYPE_PATTERNS[norm]
    if "hybrid" in norm:
        return None
    if "remote" in norm:
        return True
    if "site" in norm or "office" in norm:
        return False
    return None


def _pick_subdivision_facet(
    facets: list[dict[str, Any]], already_applied: set[str]
) -> tuple[str, list[tuple[str, int]]] | None:
    """Pick the best facet to subdivide on: ``(param, [(value_id, count), ...])``,
    or None if nothing useful remains. Skips already-applied facets (re-applying
    one just hits the cap again) and facets with fewer than two values."""
    by_param: dict[str, list[tuple[str, int]]] = {}
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        param = facet.get("facetParameter")
        values = facet.get("values") or []
        if not param or param in already_applied or len(values) < 2:
            continue
        items = [
            (v.get("id"), int(v.get("count") or 0))
            for v in values
            if isinstance(v, dict) and v.get("id") and v.get("count", 0) > 0
        ]
        if items:
            by_param[param] = items

    for preferred in _SUBDIVISION_FACETS:
        if preferred in by_param:
            return preferred, by_param[preferred]
    if by_param:
        return max(by_param.items(), key=lambda kv: len(kv[1]))
    return None
