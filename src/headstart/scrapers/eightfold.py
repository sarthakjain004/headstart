"""Eightfold AI scraper (public PCSX career sites: careers.qualcomm.com, jobs.nvidia.com,
paypal.eightfold.ai, ...).

A tenant's ``slug`` is its board host. Two public surfaces, primary + fallback (all probed live
2026-07-21; experiment/eightfold/artifacts/research_eightfold_phenom.md, proof_pcsx_50.json):

**Primary — the PCSX JSON API** (robots allows ``/api/pcsx``; the ``/api/apply/v2/jobs`` 403 is a
different, apply-flow namespace). The board's careers page carries ``_EF_GROUP_ID = "{domain}"``, the
API's ``domain`` param. Then:
  - ``GET /api/pcsx/search?domain={d}&start={n}`` — paginates 10 positions/page (``start`` += 10),
    each with ``name``/``department``/``locations``/``postedTs``/``workLocationOption``/``positionUrl``
    — every field but the description. ``data.count`` is the board total.
  - ``GET /api/pcsx/position_details?position_id={id}&domain={d}&hl=en`` — clean JSON per job with
    ``jobDescription``. ~15 KB JSON vs the ~280 KB HTML page. A failed detail leaves description None
    but the job is still kept — its metadata already came from the search list.
Proven on 40/50 live tenants; adds ``department`` (absent from the sitemap path).

**Fallback — sitemap → per-job JSON-LD** for the ~20 % of tenants that 403 the API (bot-hardened or
API-disabled — bayer, hsbc.eightfold.ai, libertymutual): ``GET {host}/careers/sitemap.xml`` lists
every job as ``/careers/job/{positionId}-{slug}?domain={co}.com`` (a ``sitemap_index`` of children is
followed one level); each job page embeds a schema.org ``JobPosting``. No ``department`` here.

Internal-mobility-only tenants (Infosys/Wipro/Walmart — ``{slug}.eightfold.ai`` behind SSO) expose
neither surface publicly, so they yield nothing — correct, they are not public boards.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_USER_AGENT = "headstart/0.1 (job-board reader)"
_DETAIL_WORKERS = 6  # sync-path detail fetches; bounded since they hit one host
_PAGE = 10  # PCSX search page size is fixed at 10 (num_items is ignored)
_MAX_PAGES = 2000  # loop bound: 2000 x 10 = 20k jobs, above any real board
_MAX_INDEX_CHILDREN = 50  # sitemap-fallback: child sitemaps to follow from an index

_EF_GROUP_ID = re.compile(r'_EF_GROUP_ID\s*=\s*"([^"]+)"')
# sitemap-fallback patterns
_JOB_LOC = re.compile(r"<loc>\s*([^<\s]*/careers/job/[^<\s]+?)\s*</loc>", re.I)
_CHILD_SITEMAP = re.compile(
    r"<loc>\s*([^<\s]*sitemap[^<\s]*\.xml[^<\s]*)\s*</loc>", re.I
)
_POSITION_ID = re.compile(r"/careers/job/(\d+)")
_LD_BLOCK = re.compile(
    r'<script type="application/ld\+json">\s*(.*?)\s*</script>', re.S
)

# workLocationOption -> remote. "hybrid" stays None (neither purely remote nor onsite).
_REMOTE_OPTION = {
    "remote": True,
    "fully remote": True,
    "onsite": False,
    "in office": False,
}


class EightfoldScraper(BaseScraper):
    """Eightfold AI scraper — ``slug`` is the board host."""

    ats = "eightfold"

    def url(self) -> str:
        return f"https://{self.slug}/careers/sitemap.xml"

    def _get(self, url: str | None = None, accept: str = "application/json") -> Any:
        return http.fetch(
            "GET",
            url or self.url(),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": accept,
                "Referer": f"https://{self.slug}/careers",
            },
            timeout=30,
        )

    # --- shared entry -------------------------------------------------------------------------

    def fetch_raw(self) -> Any:
        """Normalized job records via the PCSX API, or the sitemap fallback when the API 403s.
        Both paths yield ``{id, url, fields:{...}}`` so ``parse`` is uniform."""
        group_id = self._group_id()
        if group_id:
            positions = self._api_search(group_id)
            if positions is not None:
                return self._api_records(group_id, positions)
        return self._sitemap_records()

    def _group_id(self) -> str | None:
        """The board's ``_EF_GROUP_ID`` (the API ``domain`` param), read from its careers page."""
        try:
            r = self._get(f"https://{self.slug}/careers", accept="text/html")
        except http.RequestsError:
            return None
        if r.status_code != 200:
            return None
        m = _EF_GROUP_ID.search(r.text)
        return m.group(1) if m else None

    # --- primary: PCSX JSON API ---------------------------------------------------------------

    def _search_url(self, group_id: str, start: int) -> str:
        q = urllib.parse.urlencode(
            {"domain": group_id, "query": "", "location": "", "start": start}
        )
        return f"https://{self.slug}/api/pcsx/search?{q}"

    def _api_search(self, group_id: str) -> list[dict[str, Any]] | None:
        """Paginate ``/api/pcsx/search`` to the full position list. None signals "API unavailable"
        (403/non-200 on the first page) so the caller falls back to the sitemap."""
        first = self._get(self._search_url(group_id, 0))
        if first.status_code != 200:
            return None
        try:
            data = first.json().get("data") or {}
        except ValueError:
            return None
        positions = list(data.get("positions") or [])
        total = int(data.get("count") or 0)
        start = _PAGE
        pages = 1
        while len(positions) < total and pages < _MAX_PAGES:
            r = self._get(self._search_url(group_id, start))
            if r.status_code != 200:
                break
            batch = (r.json().get("data") or {}).get("positions") or []
            if not batch:
                break
            positions.extend(batch)
            start += _PAGE
            pages += 1
        return positions

    def _details_url(self, group_id: str, position_id: str) -> str:
        q = urllib.parse.urlencode(
            {"position_id": position_id, "domain": group_id, "hl": "en"}
        )
        return f"https://{self.slug}/api/pcsx/position_details?{q}"

    def _api_records(
        self, group_id: str, positions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge search metadata with a per-job description from position_details (fanned out).
        A failed detail only drops the description — the metadata already came from the search."""
        ids = [str(p.get("id")) for p in positions]
        if self.async_fanout_enabled():
            descs = self.fan_out_async(
                ids,
                lambda session, pid: self._description_async(session, group_id, pid),
            )
        else:
            descs = self.fan_out(
                ids,
                lambda pid: self._description(group_id, pid),
                workers=_DETAIL_WORKERS,
            )
        records = []
        for pos, desc in zip(positions, descs):
            position_id = str(pos.get("id"))
            path = pos.get("positionUrl") or f"/careers/job/{position_id}"
            records.append(
                {
                    "id": position_id,
                    "url": f"https://{self.slug}{path}"
                    if path.startswith("/")
                    else path,
                    "fields": {
                        "title": pos.get("name"),
                        "description": desc,
                        "location": _first_location(pos.get("locations")),
                        "posted_at": _ts_to_iso(pos.get("postedTs")),
                        "employment_type": None,  # not exposed by the PCSX API
                        "department": (pos.get("department") or "").strip() or None,
                        "remote": _remote_from(pos.get("workLocationOption")),
                    },
                }
            )
        return records

    def _description(self, group_id: str, position_id: str) -> str | None:
        r = self._get(self._details_url(group_id, position_id))
        return _description_of(r) if r.status_code == 200 else None

    async def _description_async(
        self, session: Any, group_id: str, position_id: str
    ) -> str | None:
        r = await http.fetch_async(
            session,
            "GET",
            self._details_url(group_id, position_id),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        return _description_of(r) if r.status_code == 200 else None

    # --- fallback: sitemap -> per-job JSON-LD -------------------------------------------------

    def _sitemap_records(self) -> list[dict[str, Any]]:
        listed = self._job_urls()
        if self.async_fanout_enabled():
            fields = self.fan_out_async(
                listed, lambda session, u: self._jsonld_async(session, u)
            )
        else:
            fields = self.fan_out(
                listed, lambda u: self._jsonld(u), workers=_DETAIL_WORKERS
            )
        return [
            {"id": _sitemap_position_id(u), "url": u, "fields": f}
            for u, f in zip(listed, fields)
        ]

    def _job_urls(self) -> list[str]:
        r = self._get(accept="application/xml")
        if r.status_code != 200:
            return []
        jobs = _dedupe(_JOB_LOC.findall(r.text))
        if jobs:
            return jobs
        children = [
            c
            for c in _dedupe(_CHILD_SITEMAP.findall(r.text))
            if "index" not in c.lower()
        ]
        found: list[str] = []
        for child in children[:_MAX_INDEX_CHILDREN]:
            cr = self._get(child, accept="application/xml")
            if cr.status_code == 200:
                found.extend(_JOB_LOC.findall(cr.text))
        return _dedupe(found)

    def _jsonld(self, job_url: str) -> dict[str, Any] | None:
        r = self._get(job_url, accept="text/html")
        return _jobposting(r.text) if r.status_code == 200 else None

    async def _jsonld_async(self, session: Any, job_url: str) -> dict[str, Any] | None:
        r = await http.fetch_async(
            session,
            "GET",
            job_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            timeout=30,
        )
        return _jobposting(r.text) if r.status_code == 200 else None

    # --- shared parse -------------------------------------------------------------------------

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            position_id = item.get("id")
            if not title or not position_id:
                continue  # unreadable / no id — nothing to key the job by
            location = fields.get("location")
            remote = fields.get("remote")
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{position_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=fields.get("department"),
                    url=item["url"],
                    posted_at=fields.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                )
            )
        return jobs


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _first_location(locations: Any) -> str | None:
    if isinstance(locations, list) and locations:
        return str(locations[0]).strip() or None
    if isinstance(locations, str):
        return locations.strip() or None
    return None


def _ts_to_iso(ts: Any) -> str | None:
    """PCSX ``postedTs`` (unix seconds, as int or str) -> ISO date. None if absent/garbled."""
    try:
        seconds = int(ts)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()


def _remote_from(option: Any) -> bool | None:
    if not isinstance(option, str):
        return None
    return _REMOTE_OPTION.get(option.strip().lower())


def _description_of(response: Any) -> str | None:
    try:
        data = response.json().get("data") or {}
    except ValueError:
        return None
    return data.get("jobDescription") or None


def _sitemap_position_id(url: str) -> str | None:
    m = _POSITION_ID.search(url)
    return m.group(1) if m else None


def _jobposting(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a job page's JSON-LD (sitemap fallback), or None without one."""
    for match in _LD_BLOCK.finditer(page):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if node_type != "JobPosting" and not (
                isinstance(node_type, list) and "JobPosting" in node_type
            ):
                continue
            employment = node.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(e) for e in employment) or None
            return {
                "title": node.get("title"),
                "description": node.get("description"),
                "location": _jsonld_location(node.get("jobLocation")),
                "posted_at": node.get("datePosted"),
                "employment_type": employment,
                "department": None,  # not in the JSON-LD
                "remote": True
                if node.get("jobLocationType") == "TELECOMMUTE"
                else None,
            }
    return None


def _jsonld_location(job_location: Any) -> str | None:
    """First ``Place``'s "City, Region, Country" from a JobPosting ``jobLocation``. The region
    often already carries the country ("Hsinchu City,TW"), so drop a country it already holds."""
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts: list[str] = []
    for value in (
        address.get("addressLocality"),
        address.get("addressRegion"),
        country,
    ):
        text = str(value).strip() if value else ""
        if text and text not in parts and not any(text in p.split(",") for p in parts):
            parts.append(text)
    return ", ".join(parts) or None
