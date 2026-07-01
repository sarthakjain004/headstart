"""Oracle Recruiting Cloud (HCM) scraper.

Adapted from jobhive's Oracle scraper (kalil0321/ats-scrapers, MIT). Each Oracle tenant has
its own host, so the slug is the careers host (e.g. ``fa-etvl-saasfaprod1.fa.ocs.oraclecloud
.com``), optionally ``host/CX_2`` to override the site number (default ``CX_1``). The public
CandidateExperience REST API returns requisitions under ``items[0].requisitionList`` — the
pagination params must live *inside* the ``finder`` string, not as separate query params.
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


class OracleScraper(BaseScraper):
    ats = "oracle"

    def __init__(self, slug: str, company: str | None = None) -> None:
        host, _, site = slug.partition("/")
        super().__init__(host, company)
        self.site = site or "CX_1"

    def url(self) -> str:
        finder = f"findReqs;siteNumber={self.site}%2Climit=200%2Coffset=0"
        return (
            f"https://{self.slug}/hcmRestApi/resources/latest/"
            f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder={finder}"
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        items = raw.get("items") or []
        reqs = items[0].get("requisitionList", []) if items else []
        jobs: list[Job] = []
        for r in reqs:
            location = r.get("PrimaryLocation") or r.get("PrimaryLocationCountry")
            workplace = r.get("WorkplaceType") or r.get("WorkplaceTypeCode") or ""
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{r['Id']}",
                    ats=self.ats,
                    company=r.get("LegalEmployer") or self.company,
                    title=(r.get("Title") or "").strip(),
                    location=location,
                    remote="remote" in workplace.lower() or is_remote(location),
                    department=r.get("Department") or r.get("JobFunction"),
                    url=(
                        f"https://{self.slug}/hcmUI/CandidateExperience/en/sites/"
                        f"{self.site}/job/{r['Id']}"
                    ),
                    posted_at=r.get("PostedDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(r.get("ShortDescriptionStr")),
                    employment_type=r.get("JobType") or r.get("JobSchedule"),
                )
            )
        return jobs
