"""Recruitee job-board scraper ({slug}.recruitee.com offers API).

Adapted from jobhive's Recruitee scraper (kalil0321/ats-scrapers, MIT) to this project's
BaseScraper contract:
    https://{slug}.recruitee.com/api/offers/
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


def _salary(sal: dict | None) -> str | None:
    """Format Recruitee's structured salary, e.g. '50000-70000 EUR per year'. None if blank."""
    sal = sal or {}
    lo, hi = sal.get("min"), sal.get("max")
    if not lo and not hi:
        return None
    rng = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(str(x) for x in (rng, sal.get("currency"), sal.get("period")) if x)


class RecruiteeScraper(BaseScraper):
    ats = "recruitee"

    def url(self) -> str:
        return f"https://{self.slug}.recruitee.com/api/offers/"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for o in raw.get("offers", []):
            location = (
                o.get("location")
                or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{o['id']}",
                    ats=self.ats,
                    company=o.get("company_name") or self.company,
                    title=(o.get("title") or "").strip(),
                    location=location,
                    remote=bool(o.get("remote")) or is_remote(location),
                    department=o.get("department"),
                    url=o.get("careers_url") or o.get("careers_apply_url", ""),
                    posted_at=o.get("published_at") or o.get("created_at"),
                    scraped_at=scraped_at,
                    # requirements is a separate field — dropping it starves experience
                    # extraction and the embedding of the qualifications text
                    description=html_to_text(
                        "\n".join(
                            s
                            for s in (o.get("description"), o.get("requirements"))
                            if s
                        )
                    ),
                    experience=o.get("experience_code"),
                    employment_type=o.get("employment_type_code"),
                    salary=_salary(o.get("salary")),
                )
            )
        return jobs
