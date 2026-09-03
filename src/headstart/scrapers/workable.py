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

    #: A 429 here is Cloudflare's Managed Challenge standing in for a spent per-IP request
    #: budget, so a second egress address is a second budget (ADR-0063). Traced to its origin
    #: before opting in, which is the bar ADR-0063 sets and the bar freshteam (#311) and personio
    #: (#312) each failed: measured live 2026-09-03 against the wall from run 33725210468, the
    #: same Board answers 429 on the walled address and 200 over WARP in the same second (twice
    #: each way); five *other* tenants also answer 429 from that address while three of them
    #: answer 200 over WARP, so the wall is per client IP across the whole origin and never a
    #: property of the tenant; and all 149 Boards the run lost serve 200 from a rested address,
    #: which is what rules out personio's departed-tenant shape. The wall clears in 15-31s and
    #: carries no ``Retry-After``, so the three-attempt ladder (~5s) can never outlast it —
    #: rotating is the only lever that reaches it. See
    #: ``docs/workable/2026-08-27_the-managed-challenge-is-a-spent-budget.md``.
    egress_fallback_on = frozenset({429})

    def url(self) -> str:
        return f"https://apply.workable.com/api/v1/widget/accounts/{self.slug}?details=true"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            location = (
                ", ".join(
                    p for p in (j.get("city"), j.get("state"), j.get("country")) if p
                )
                or None
            )
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
