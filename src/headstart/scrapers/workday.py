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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper

_URL_PATTERN = re.compile(
    r"^https://(?P<company>[^.]+)\.(?P<instance>wd\d+)\.myworkdayjobs\.com/(?P<site>[^/?#]+)"
)

_USER_AGENT = "headstart/0.1 (job-board reader)"
_PAGE_LIMIT = 20  # Workday hard-caps `limit` at 20 (higher returns 400).
_QUERY_TOTAL_CAP = 2000  # total reported as exactly 2000 => capped => subdivide.
_MAX_DEPTH = 4  # recursion bound; Accenture needs depth 3, 4 is a paranoid ceiling.
_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}  # 403 = burst throttle, retryable.
_MAX_ATTEMPTS = 3
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

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return url.rstrip("/")  # the full https://{co}.{inst}.myworkdayjobs.com/{site}

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
        return match.group("company"), match.group("instance"), match.group("site")

    def _post(self, applied_facets: dict[str, list[str]], offset: int) -> dict[str, Any] | None:
        """POST one page of the jobs query. Returns the JSON dict, or None on 404
        (site gone) / exhausted retries on a transient block."""
        body = {
            "appliedFacets": applied_facets,
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        }
        headers = {"User-Agent": _USER_AGENT, "Content-Type": "application/json",
                   "Accept": "application/json"}
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = http.post(self.url(), json=body, headers=headers, timeout=30)
            except http.RequestsError as exc:
                last_error = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            if response.status_code == 404:
                return None  # site not found — treat as no jobs
            if response.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        raise last_error  # pragma: no cover

    def fetch_raw(self) -> Any:
        """Crawl the tenant (paginate + recursively subdivide capped queries) and
        return a flat, de-duplicated list of raw posting dicts."""
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
        self._attach_descriptions(postings)
        return postings

    def _attach_descriptions(self, postings: list[dict[str, Any]]) -> None:
        """Second pass: fetch each posting's description concurrently (bounded). A failed
        detail fetch leaves ``_jobDescription`` None so the job is still kept."""
        if not postings:
            return
        with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
            futures = {
                pool.submit(self._job_description, item.get("externalPath")): item
                for item in postings
            }
            for future in as_completed(futures):
                try:
                    futures[future]["_jobDescription"] = future.result()
                except Exception:  # noqa: BLE001 - one bad detail must not sink the batch
                    futures[future]["_jobDescription"] = None

    def _job_description(self, external_path: str | None) -> str | None:
        """GET one posting's detail and return its raw-HTML jobDescription (None on failure)."""
        if not external_path:
            return None
        company, instance, site = self._parts()
        url = (f"https://{company}.{instance}.myworkdayjobs.com"
               f"/wday/cxs/{company}/{site}{external_path}")
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = http.get(url, headers=headers, timeout=30)
            except http.RequestsError:
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return None
            if response.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code != 200:
                return None
            return (response.json().get("jobPostingInfo") or {}).get("jobDescription")
        return None

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
            self._paginate(applied, total, absorb)  # not capped, or nothing left to split
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
        display = self.company if self.company and self.company != self.slug else company
        base = self.slug.rstrip("/")

        jobs: list[Job] = []
        for item in raw:
            external_path = item.get("externalPath") or ""
            ats_id = _posting_key(item)
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
                    posted_at=None,  # Workday only gives relative strings ("30+ Days Ago")
                    scraped_at=scraped_at,
                    description=html_to_text(item.get("_jobDescription")),
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
