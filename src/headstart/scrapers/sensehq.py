"""SenseHQ job-board scraper ({slug}.sensehq.com).

SenseHQ exposes a clean public JSON feed (no auth) — found by probing, not in any existing
scraper repo:
    https://{slug}.sensehq.com/careers/api/jobs   ->  {"success", "data": {"rows": [...]}}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


class SenseHQScraper(BaseScraper):
    ats = "sensehq"

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self._page = 0

    def url(self) -> str:
        return f"https://{self.slug}.sensehq.com/careers/api/jobs?page={self._page}"

    def fetch_raw(self) -> Any:
        # SenseHQ returns 10 rows/page (0-indexed ?page=N) — page through to the count.
        rows: list[dict] = []
        self._page = 0
        while True:
            data = json.loads(self._get()).get("data") or {}
            batch = data.get("rows", [])
            rows.extend(batch)
            self._page += 1
            if len(batch) < 10 or len(rows) >= data.get("count", 0) or self._page > 100:
                break
        return {"data": {"rows": rows}}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for r in (raw.get("data") or {}).get("rows", []):
            posted = None
            if r.get("created_on"):
                posted = datetime.fromtimestamp(
                    r["created_on"] / 1000, tz=UTC
                ).isoformat()
            location = r.get("location")
            workplace = r.get("workplace_type") or ""
            start, end = r.get("experience_start"), r.get("experience_end")
            experience = (
                f"{start}-{end}" if start is not None and end is not None else None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{r['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(r.get("title") or "").strip(),
                    location=location,
                    remote="remote" in workplace.lower() or is_remote(location),
                    department=r.get("department"),
                    url=f"https://{self.slug}.sensehq.com/careers/jobs/{r['id']}",
                    posted_at=posted,
                    scraped_at=scraped_at,
                    description=html_to_text(r.get("description_external")),
                    experience=experience,
                    employment_type=r.get("job_type"),
                )
            )
        return jobs
