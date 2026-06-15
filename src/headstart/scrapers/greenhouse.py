"""Greenhouse job-board scraper (boards-api.greenhouse.io)."""

from __future__ import annotations

from typing import Any

from headstart.models import Job, is_remote
from headstart.scrapers.base import BaseScraper


class GreenhouseScraper(BaseScraper):
    ats = "greenhouse"

    def url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            location = (j.get("location") or {}).get("name")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=j.get("company_name") or self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=None,  # not exposed by the jobs-list endpoint
                    url=j.get("absolute_url", ""),
                    posted_at=j.get("first_published") or j.get("updated_at"),
                    scraped_at=scraped_at,
                )
            )
        return jobs
