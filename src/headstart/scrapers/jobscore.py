"""JobScore's published JSON feed, one complete request per Board (ADR-0385).

The public feed carries descriptions, department, location and the real opened date;
`last_updated_date` is an edit date. JobScore asks consumers to poll at most hourly.
The pipeline is daily; discovery uses its public sitemap and never polls a feed in a loop.
Robots disallows the application flow, so links use the public posting instead.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.pacer import Pacer

# Conservative process-wide starts below this session's courtesy sample; knee unknown.
_PACER = Pacer(1.5)


class JobScoreScraper(BaseScraper):
    ats = "jobscore"
    url_shape = r"https://careers\.jobscore\.com/careers/[\w-]+/jobs/[^/?#]+"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        match = re.search(
            r"jobscore\.com/(?:careers|jobs)/([\w-]+)", url or tenant, re.IGNORECASE
        )
        return (match[1] if match else tenant).strip().lower()

    def url(self) -> str:
        return f"https://careers.jobscore.com/jobs/{self.slug}/feed.json"

    def fetch_raw(self) -> dict:
        _PACER.wait()
        return super().fetch_raw()

    def job_url(self, row: dict) -> str:
        return (
            f"https://careers.jobscore.com/careers/{self.slug}/jobs/{row['url_slug']}"
        )

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        match = re.search(r"jobscore\.com/(?:careers|jobs)/([\w-]+)", landing_url)
        return match[1].lower() if match else None

    def _salary_field(self, raw: Any) -> str | None:
        # JobScore uses cents: Avispa's 4,000 means $40/hour, Good Day Farm's
        # 5,000,000 means $50,000/year (2026-10-03). A lone ceiling is no floor.
        low, high = raw.get("public_salary_minimum"), raw.get("public_salary_maximum")
        if not low:
            return None
        # imgix's JPY 10,000,000 states ¥10,000,000: yen has no fractional minor unit.
        divisor = 1 if raw.get("currency_code") == "JPY" else 100
        return salary.to_field(
            low / divisor,
            high / divisor if high else None,
            raw.get("currency_code"),
            raw.get("public_compensation_interval"),
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        if not isinstance(raw, dict) or not isinstance(raw.get("jobs"), list):
            self.note_unreadable_board("a jobs list", "invalid JobScore feed")
            raise TypeError("invalid JobScore feed")
        if raw.get("company_code", self.slug).lower() != self.slug:
            raise ValueError("JobScore feed belongs to its canonical company_code")
        jobs = []
        for row in raw["jobs"]:
            location = row.get("location")
            workplace = (row.get("remote") or "").split("|", 1)[0].strip().lower()
            remote = {"yes": True, "no": False, "hybrid": None}.get(workplace)
            if not workplace:
                remote = is_remote(location)
            parts = urlsplit(row.get("detail_url") or self.job_url(row))
            jobs.append(
                Job(
                    id=self.job_id(row["id"]),
                    ats=self.ats,
                    company=raw.get("company_name") or self.company,
                    title=row["title"],
                    location=location,
                    remote=remote,
                    department=row.get("department"),
                    url=urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
                    posted_at=row.get("opened_date"),
                    scraped_at=scraped_at,
                    description=html_to_text(row.get("description")),
                    experience=row.get("experience_level"),
                    employment_type=row.get("job_type"),
                    salary=self._salary_field(row),
                )
            )
        return jobs
