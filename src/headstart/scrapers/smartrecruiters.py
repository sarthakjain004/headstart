"""SmartRecruiters job-board scraper (api.smartrecruiters.com posting API).

Adapted from jobhive's SmartRecruiters scraper (kalil0321/ats-scrapers, MIT) to this
project's BaseScraper contract:
    https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100

The postings list has no description; a second pass fetches each posting's detail
(GET .../postings/{id} -> jobAd.sections.jobDescription.text) in a bounded thread pool to
fill it in. A failed detail fetch leaves description None — the job is still kept.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_UA = "headstart/0.1 (job-board reader)"
_DETAIL_WORKERS = 8
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class SmartRecruitersScraper(BaseScraper):
    ats = "smartrecruiters"

    def url(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?limit=100"

    def fetch_raw(self) -> Any:
        data = json.loads(self._get())
        self._attach_descriptions(data.get("content") or [])
        return data

    def _attach_descriptions(self, postings: list[dict]) -> None:
        """Second pass: fetch each posting's description concurrently (bounded). A failed
        detail fetch leaves ``_description`` None so the job is still kept."""
        if not postings:
            return
        with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
            futures = {pool.submit(self._job_description, p.get("id")): p for p in postings}
            for future in as_completed(futures):
                try:
                    futures[future]["_description"] = future.result()
                except Exception:  # noqa: BLE001 - one bad detail must not sink the batch
                    futures[future]["_description"] = None

    def _job_description(self, posting_id: str | None) -> str | None:
        """GET one posting's detail and return its raw-HTML jobDescription (None on failure)."""
        if not posting_id:
            return None
        url = f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings/{posting_id}"
        headers = {"User-Agent": _UA, "Accept": "application/json"}
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
            sections = ((response.json().get("jobAd") or {}).get("sections") or {})
            return (sections.get("jobDescription") or {}).get("text")
        return None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for p in raw.get("content", []):
            loc = p.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x
            ) or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{p['id']}",
                    ats=self.ats,
                    company=(p.get("company") or {}).get("name") or self.company,
                    title=(p.get("name") or "").strip(),
                    location=location,
                    remote=bool(loc.get("remote")) or is_remote(location),
                    department=(p.get("department") or {}).get("label"),
                    url=f"https://jobs.smartrecruiters.com/{self.slug}/{p['id']}",
                    posted_at=p.get("releasedDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(p.get("_description")),
                    experience=(p.get("experienceLevel") or {}).get("label"),
                    employment_type=(p.get("typeOfEmployment") or {}).get("label"),
                )
            )
        return jobs
