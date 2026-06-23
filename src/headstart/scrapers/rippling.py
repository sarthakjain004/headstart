"""Rippling job-board scraper (ats.rippling.com, public board API).

A company's board lives at ``https://ats.rippling.com/{slug}``; its openings are listed at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs        (summary only)
and each posting's full record — including the HTML ``description`` (a ``{company, role}``
object) — is at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
fetched in a bounded thread pool. A failed detail fetch leaves description None — job still kept.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_API = "https://api.rippling.com/platform/api/ats/v1/board"
_UA = "headstart/0.1 (job-board reader)"
_DETAIL_WORKERS = 8


def _location(item: dict) -> str | None:
    wl = item.get("workLocation") or {}
    if isinstance(wl, dict) and wl.get("label"):
        return wl["label"]
    wls = (item.get("_detail") or {}).get("workLocations") or []
    return wls[0] if wls else None


def _description(detail: dict) -> str | None:
    d = detail.get("description")
    if isinstance(d, dict):
        return d.get("role") or d.get("company")  # `role` is the posting; `company` is the blurb
    return d if isinstance(d, str) else None


class RipplingScraper(BaseScraper):
    ats = "rippling"

    def url(self) -> str:
        return f"{_API}/{self.slug}/jobs"

    def fetch_raw(self) -> Any:
        resp = http.fetch(
            "GET", self.url(), headers={"User-Agent": _UA, "Accept": "application/json"}, timeout=30
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data if isinstance(data, list) else (data.get("items") or data.get("jobs") or [])
        self._attach_details(items)
        return items

    def _attach_details(self, items: list[dict]) -> None:
        """Fetch each posting's detail concurrently (bounded) and attach it as ``_detail``."""
        if not items:
            return
        with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
            futures = {pool.submit(self._detail, it.get("uuid")): it for it in items}
            for fut in as_completed(futures):
                try:
                    futures[fut]["_detail"] = fut.result()
                except Exception:  # noqa: BLE001 - one bad detail must not sink the batch
                    futures[fut]["_detail"] = {}

    def _detail(self, uuid: str | None) -> dict:
        if not uuid:
            return {}
        try:
            resp = http.fetch(
                "GET", f"{_API}/{self.slug}/jobs/{uuid}",
                headers={"User-Agent": _UA, "Accept": "application/json"}, timeout=30,
            )
        except http.RequestsError:
            return {}
        return resp.json() if resp.status_code == 200 else {}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for it in raw:
            detail = it.get("_detail") or {}
            location = _location(it)
            dept = it.get("department") or detail.get("department")
            if isinstance(dept, dict):
                dept = dept.get("name")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['uuid']}",
                    ats=self.ats,
                    company=detail.get("companyName") or self.company,
                    title=(it.get("name") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=dept,
                    url=it.get("url") or f"https://ats.rippling.com/{self.slug}/jobs/{it['uuid']}",
                    posted_at=detail.get("createdOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(_description(detail)),
                    employment_type=(detail.get("employmentType") or {}).get("id"),
                )
            )
        return jobs
