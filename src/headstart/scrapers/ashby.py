"""Ashby job-board scraper (api.ashbyhq.com)."""

from __future__ import annotations

from typing import Any

from headstart.models import Job
from headstart.scrapers.base import BaseScraper


class AshbyScraper(BaseScraper):
    ats = "ashby"

    def url(self) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            if not j.get("isListed", True):
                continue  # skip postings the company has unlisted
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=j.get("location"),
                    remote=j.get("isRemote"),
                    department=j.get("department"),
                    url=j.get("jobUrl", ""),
                    posted_at=j.get("publishedAt"),
                    scraped_at=scraped_at,
                )
            )
        return jobs
