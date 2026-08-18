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

Adapted from jobhive (kalil0321/ats-scrapers) to this project's synchronous design (the shared
pooled ``http`` client, no asyncio), mapped onto our leaner Job.
"""

from __future__ import annotations

import re
from typing import Any

from headstart import http, log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

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

_USER_AGENT = "headstart/0.1 (job-board reader)"
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
                        "User-Agent": _USER_AGENT,
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
            "User-Agent": _USER_AGENT,
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

        self._exhaust({}, absorb, depth=0)
        # Second pass: fill each posting's detail fields concurrently (bounded); a failed
        # fetch leaves ``_detail`` empty so the job is still kept.
        if self.async_fanout_enabled():
            details = self.fan_out_async(
                postings,
                lambda session, item: self._job_detail_async(
                    session, item.get("externalPath")
                ),
            )
        else:
            details = self.fan_out(
                postings,
                lambda item: self._job_detail(item.get("externalPath")),
                workers=_DETAIL_WORKERS,
            )
        self.report_detail_gaps(details, "details")
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
        location(s) and remoteType, which the list payload rolls up or omits (see
        :func:`_location_from`)."""
        if response.status_code != 200:
            return None
        info = response.json().get("jobPostingInfo") or {}
        return {
            "description": info.get("jobDescription"),
            "startDate": info.get("startDate"),
            "timeType": info.get("timeType"),
            "location": info.get("location"),
            "additionalLocations": info.get("additionalLocations"),
            "remoteType": info.get("remoteType"),
        }

    def _job_detail(self, external_path: str | None) -> dict[str, Any] | None:
        """GET one posting's detail fields (None on failure). Sync path."""
        if not external_path:
            return None
        try:
            response = http.fetch(
                "GET",
                self._detail_url(external_path),
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                **self._egress(),
            )
        except http.RequestsError:
            return None  # a missing detail must not drop the job
        return self._extract_detail(response)

    async def _job_detail_async(
        self, session: Any, external_path: str | None
    ) -> dict[str, Any] | None:
        """Same as :meth:`_job_detail` but over the shared multiplexed ``AsyncSession``."""
        if not external_path:
            return None
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(external_path),
                timeout=30,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                **self._egress(),
            )
        except http.RequestsError:
            return None
        return self._extract_detail(response)

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
            self._exhaust({**applied, param: [value_id]}, absorb, depth + 1)

    def _paginate(self, applied: dict[str, list[str]], total: int, absorb) -> None:
        """Page through offsets [20, total) sequentially. Pages whose ``_post`` 404s
        mid-crawl are skipped as before, but one warning now reports how many went
        missing — the tripwire for a partial board."""
        missing = 0
        for offset in range(_PAGE_LIMIT, total, _PAGE_LIMIT):
            payload = self._post(applied, offset=offset)
            if payload is None:
                missing += 1
            absorb((payload or {}).get("jobPostings") or [])
        if missing:
            _log.warning(
                f"{self.board_key()}: {missing} page(s) 404ed mid-crawl — "
                f"board partial ({total} listed)"
            )
            # The warning was the whole record until ADR-0053: `index sync` could not see it, so
            # the pages this dropped were evicted as delistings. Now it travels with the Jobs.
            self.mark_truncated(
                f"{missing} page(s) 404ed mid-crawl of {total} listed postings"
            )

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


def _posting_key(item: dict[str, Any]) -> str:
    """Stable per-posting id: bulletFields[0] (requisition id on tenants that
    surface it) else the externalPath tail."""
    bullet = (item.get("bulletFields") or [None])[0]
    if bullet:
        return str(bullet)
    return (item.get("externalPath") or "").rsplit("/", 1)[-1] or "unknown"


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
    """The posting's real location(s), preferring the listing and repairing it from the detail.

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
        return listing
    primary = detail.get("location")
    if not isinstance(primary, str) or not primary.strip():
        # No detail (a failed fetch keeps the Job anyway) — the rollup is still what the
        # listing said, and saying "5 Locations" beats saying nothing.
        return listing
    # `isinstance(..., list)` on the container, not only its items: `or []` over a bare string
    # iterates it character by character and would join "Dublin" as "D; u; b; l; i; n".
    extra = detail.get("additionalLocations")
    places = [
        p.strip()
        for p in (extra if isinstance(extra, list) else [])
        if isinstance(p, str) and p.strip()
    ]
    return "; ".join([primary.strip(), *places])


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
