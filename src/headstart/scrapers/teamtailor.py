"""Teamtailor job-board scraper ({slug}.teamtailor.com JSON Feed).

Teamtailor publishes a public JSON Feed (jsonfeed.org v1.1) at
``https://{slug}.teamtailor.com/jobs.json``. Each ``items`` entry carries the title, the job
URL, the publish date, the full ``content_html`` (the description), and a schema.org
``_jobposting`` block with the structured location and hiring organization. Everything we need
is inline — no per-job detail fetch.
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


def _location(jobposting: dict) -> str | None:
    """Join the first jobLocation's city/region/country from the schema.org block."""
    locs = jobposting.get("jobLocation") or []
    if not isinstance(locs, list) or not locs:
        return None
    addr = (locs[0] or {}).get("address") or {}
    parts = (
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("addressCountry"),
    )
    return ", ".join(p for p in parts if p) or None


class TeamtailorScraper(BaseScraper):
    ats = "teamtailor"

    def url(self) -> str:
        return f"https://{self.slug}.teamtailor.com/jobs.json"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        feed_company = raw.get("title") or self.company
        jobs: list[Job] = []
        for it in raw.get("items", []):
            jp = it.get("_jobposting") or {}
            location = _location(jp)
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['id']}",
                    ats=self.ats,
                    company=(jp.get("hiringOrganization") or {}).get("name")
                    or feed_company,
                    title=(it.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=None,  # not exposed in the public feed
                    url=it.get("url", ""),
                    posted_at=it.get("date_published") or jp.get("datePosted"),
                    scraped_at=scraped_at,
                    description=html_to_text(it.get("content_html")),
                    employment_type=jp.get("employmentType"),
                )
            )
        return jobs
