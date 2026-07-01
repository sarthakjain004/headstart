"""Keka job-board scraper ({slug}.keka.com careers embed API).

Reverse-engineered from a browser HAR (no public docs; Keka's documented API is OAuth-only).
The careers SPA loads jobs from an unauthenticated embed API in two steps:
  1. GET /careers/api/organization/default/careerportalinfo  -> carries the tenant UUID
  2. GET /careers/api/embedjobs/default/active/{tenant_uuid}  -> JSON array of active jobs
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class KekaScraper(BaseScraper):
    ats = "keka"

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self._tenant: str | None = None

    def url(self) -> str:
        base = f"https://{self.slug}.keka.com/careers/api"
        if self._tenant is None:
            return f"{base}/organization/default/careerportalinfo"
        return f"{base}/embedjobs/default/active/{self._tenant}"

    def fetch_raw(self) -> Any:
        # step 1: portal info carries the tenant UUID (in careersBackgroundPath etc.)
        info = self._get()
        match = _UUID_RE.search(info)
        if not match:
            return []
        self._tenant = match.group(0)
        # step 2: the active-jobs array
        return json.loads(self._get())

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            loc = (j.get("jobLocations") or [{}])[0]
            location = (
                ", ".join(
                    x
                    for x in (
                        loc.get("city") or loc.get("name"),
                        loc.get("state"),
                        loc.get("countryName"),
                    )
                    if x
                )
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=j.get("departmentName"),
                    url=f"https://{self.slug}.keka.com/careers/jobdetails/{j['id']}",
                    posted_at=j.get("publishedOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("description") or j.get("excerpt")),
                    experience=j.get("experience"),
                )
            )
        return jobs
