"""Greenhouse job-board scraper (boards-api.greenhouse.io).

The bare ``/jobs`` list is metadata-only. ``?content=true`` inlines each posting's full
description (and a ``departments`` array) in the same single request — ~12x the payload but
no per-job fetch — so we use it to populate description and department.
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


class GreenhouseScraper(BaseScraper):
    ats = "greenhouse"

    def url(self) -> str:
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            location = (j.get("location") or {}).get("name")
            department = (j.get("departments") or [{}])[0].get("name") or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=j.get("company_name") or self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=department,
                    url=j.get("absolute_url", ""),
                    posted_at=j.get("first_published") or j.get("updated_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("content")),
                )
            )
        return jobs
