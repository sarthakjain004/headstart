"""Workable job-board scraper (apply.workable.com widget API).

Adapted from jobhive's Workable scraper (kalil0321/ats-scrapers, MIT) to this project's
BaseScraper contract. The public widget API returns all postings in one JSON payload:
    https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


class WorkableScraper(BaseScraper):
    ats = "workable"

    def url(self) -> str:
        return f"https://apply.workable.com/api/v1/widget/accounts/{self.slug}?details=true"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            location = ", ".join(
                p for p in (j.get("city"), j.get("state"), j.get("country")) if p
            ) or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['shortcode']}",
                    ats=self.ats,
                    company=raw.get("name") or self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=bool(j.get("telecommuting")) or is_remote(location),
                    department=j.get("department"),
                    url=j.get("application_url") or j.get("url", ""),
                    posted_at=j.get("published_on") or j.get("created_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("description")),
                    experience=j.get("experience"),
                    employment_type=j.get("employment_type"),
                )
            )
        return jobs
