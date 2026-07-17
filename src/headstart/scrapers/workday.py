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

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper

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
        self, applied_facets: dict[str, list[str]], offset: int
    ) -> dict[str, Any] | None:
        """POST one page of the jobs query (retry lives in fetch). Returns the JSON dict, or
        None on 404 (site gone)."""
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
            "POST", self.url(), json=body, headers=headers, timeout=30
        )
        if response.status_code == 404:
            return None  # site not found — treat as no jobs
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
        relative posted date ("30+ Days Ago") and no employment type."""
        if response.status_code != 200:
            return None
        info = response.json().get("jobPostingInfo") or {}
        return {
            "description": info.get("jobDescription"),
            "startDate": info.get("startDate"),
            "timeType": info.get("timeType"),
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
            )
        except http.RequestsError:
            return None
        return self._extract_detail(response)

    def _exhaust(self, applied: dict[str, list[str]], absorb, depth: int) -> None:
        """Exhaust one filter combination: paginate normally, or subdivide when the
        2,000 cap is hit and a fresh facet is available."""
        first = self._post(applied, offset=0)
        if not first:
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
        if not capped or facet is None:
            self._paginate(
                applied, total, absorb
            )  # not capped, or nothing left to split
            return

        param, values = facet
        for value_id, _count in values:
            self._exhaust({**applied, param: [value_id]}, absorb, depth + 1)

    def _paginate(self, applied: dict[str, list[str]], total: int, absorb) -> None:
        """Page through offsets [20, total) sequentially."""
        for offset in range(_PAGE_LIMIT, total, _PAGE_LIMIT):
            payload = self._post(applied, offset=offset)
            absorb((payload or {}).get("jobPostings") or [])

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
            jobs.append(
                Job(
                    id=f"{self.ats}:{company}/{site}:{ats_id}",
                    ats=self.ats,
                    company=display,
                    title=(item.get("title") or "Untitled").strip(),
                    location=item.get("locationsText"),
                    remote=_remote_from(item.get("remoteType")),
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
