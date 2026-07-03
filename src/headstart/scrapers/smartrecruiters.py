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
import os
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_UA = "headstart/0.1 (job-board reader)"
_DETAIL_WORKERS = 8


class SmartRecruitersScraper(BaseScraper):
    ats = "smartrecruiters"

    def url(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?limit=100"

    def fetch_raw(self) -> Any:
        # Second pass: fill each posting's description concurrently (bounded); a failed detail fetch
        # leaves ``_description`` None so the job is still kept. HEADSTART_ASYNC_FANOUT=1 runs the
        # detail pass over one multiplexed HTTP/2 connection instead of the thread pool (A/B).
        data = json.loads(self._get())
        postings = data.get("content") or []
        if os.environ.get("HEADSTART_ASYNC_FANOUT") == "1":
            descriptions = self.fan_out_async(
                postings,
                lambda session, p: self._job_description_async(session, p.get("id")),
            )
        else:
            descriptions = self.fan_out(
                postings,
                lambda p: self._job_description(p.get("id")),
                workers=_DETAIL_WORKERS,
            )
        for posting, description in zip(postings, descriptions):
            posting["_description"] = description
        return data

    def _detail_url(self, posting_id: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings/{posting_id}"

    @staticmethod
    def _extract_description(response: Any) -> str | None:
        """Pull the raw-HTML jobDescription out of a posting-detail response (None on non-200)."""
        if response.status_code != 200:
            return None
        sections = (response.json().get("jobAd") or {}).get("sections") or {}
        return (sections.get("jobDescription") or {}).get("text")

    def _job_description(self, posting_id: str | None) -> str | None:
        """GET one posting's detail and return its jobDescription (None on failure). Sync path."""
        if not posting_id:
            return None
        try:
            response = http.fetch(
                "GET",
                self._detail_url(posting_id),
                timeout=30,
                headers={"User-Agent": _UA, "Accept": "application/json"},
            )
        except http.RequestsError:
            return None  # a missing description must not drop the job
        return self._extract_description(response)

    async def _job_description_async(
        self, session: Any, posting_id: str | None
    ) -> str | None:
        """Same as :meth:`_job_description` but over the shared multiplexed ``AsyncSession``."""
        if not posting_id:
            return None
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(posting_id),
                timeout=30,
                headers={"User-Agent": _UA, "Accept": "application/json"},
            )
        except http.RequestsError:
            return None
        return self._extract_description(response)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for p in raw.get("content", []):
            loc = p.get("location") or {}
            location = (
                loc.get("fullLocation")
                or ", ".join(
                    x
                    for x in (loc.get("city"), loc.get("region"), loc.get("country"))
                    if x
                )
                or None
            )
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
