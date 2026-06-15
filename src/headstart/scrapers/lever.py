"""Lever job-board scraper (api.lever.co)."""

from __future__ import annotations

from typing import Any

from headstart.models import Job, epoch_ms_to_iso, is_remote
from headstart.scrapers.base import BaseScraper


class LeverScraper(BaseScraper):
    ats = "lever"

    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.slug}?mode=json"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            categories = j.get("categories") or {}
            location = categories.get("location")
            workplace = (j.get("workplaceType") or "").lower()
            remote = workplace == "remote" or bool(is_remote(location))
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("text") or "").strip(),
                    location=location,
                    remote=remote,
                    department=categories.get("department") or categories.get("team"),
                    url=j.get("hostedUrl", ""),
                    posted_at=epoch_ms_to_iso(j.get("createdAt")),
                    scraped_at=scraped_at,
                )
            )
        return jobs
