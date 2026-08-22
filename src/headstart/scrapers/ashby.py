"""Ashby job-board scraper (api.ashbyhq.com)."""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper


def _salary(compensation: dict | None) -> str | None:
    """A real, structured Salary-typed component from ``compensationTiers[].components[]``,
    formatted as "50000-70000 USD 1 YEAR" — the same RANGE + CODE + interval shape lever/
    recruitee/teamtailor already produce (``_field_range_currency_interval`` in salary.py) — not
    ``compensationTierSummary`` (the human-formatted "$80K – $100K • Offers Bonus" string this
    scraper used before). Found via direct API inspection (2026-08-22): 34% of jobs have a
    populated Salary component, close to 4x teamtailor's field-presence rate, and fixing this at
    the source avoids re-parsing a summary string that mixes in bonus/equity language the
    structured data already keeps separate (``compensationType`` "Bonus"/"Commission"/
    "EquityPercentage"/"EquityCashValue", all skipped here).

    A "1 TIME" interval (confirmed real: "Compensation per finished project", an onboarding rate)
    is a one-off payment, not a recurring salary — deliberately excluded rather than guessed at as
    if it were annual, the same no-fabrication principle every other Tier-1 field parser follows.
    Zero real tiers had more than one Salary component when checked; a job with several
    compensation tiers (13/1,972 in the same sample) takes the first tier with a usable one."""
    for tier in (compensation or {}).get("compensationTiers") or []:
        for c in tier.get("components") or []:
            if c.get("compensationType") != "Salary":
                continue
            lo, hi = c.get("minValue"), c.get("maxValue")
            if lo is None and hi is None:
                continue
            interval = c.get("interval")
            if interval == "1 TIME":
                continue
            span = f"{lo}-{hi}" if lo and hi else str(lo or hi)
            return " ".join(
                str(x) for x in (span, c.get("currencyCode"), interval) if x
            )
    return None


class AshbyScraper(BaseScraper):
    ats = "ashby"

    def url(self) -> str:
        # includeCompensation adds the structured compensation block to each posting
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}?includeCompensation=true"

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
                    description=html_to_text(
                        j.get("descriptionPlain") or j.get("descriptionHtml")
                    ),
                    employment_type=j.get("employmentType"),
                    salary=_salary(j.get("compensation")),
                )
            )
        return jobs
