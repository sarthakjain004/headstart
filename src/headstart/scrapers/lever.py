"""Lever job-board scraper (api.lever.co, with EU-instance fallback).

Lever runs a global instance (api.lever.co) and a separate EU instance (api.eu.lever.co,
behind jobs.eu.lever.co). The company slug alone doesn't say which, so we try global first
and fall back to EU when the slug isn't found there.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, epoch_ms_to_iso, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


class LeverScraper(BaseScraper):
    ats = "lever"

    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.slug}?mode=json"

    def fetch_raw(self) -> Any:
        # try the global instance, then EU; a 404 on both means the company isn't on Lever.
        for host in ("api.lever.co", "api.eu.lever.co"):
            response = http.fetch(
                "GET", f"https://{host}/v0/postings/{self.slug}?mode=json"
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json()
        return []

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
                    description=html_to_text(
                        j.get("descriptionPlain") or j.get("description")
                    ),
                    employment_type=categories.get("commitment"),
                )
            )
        return jobs
