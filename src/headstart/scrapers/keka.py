"""Keka job-board scraper ({slug}.keka.com careers embed API).

Reverse-engineered from a browser HAR (no public docs; Keka's documented API is OAuth-only).
The careers SPA loads jobs from an unauthenticated embed API in two steps:
  1. GET /careers/api/organization/default/careerportalinfo  -> carries the tenant UUID
  2. GET /careers/api/embedjobs/default/active/{tenant_uuid}  -> JSON array of active jobs

The UUID in step 1 only rides along inside ``careersBackgroundPath`` (the portal's background-image
URL), so a portal with no custom background image carries no UUID there — for those we fall back to
the ``/careers`` page HTML, which embeds it. Keka also soft-errors at HTTP 200 with an HTML page
("Invalid Tenant" for an unknown slug, "Forbidden Access" for a disabled portal) — either means no
public board, so we yield no jobs rather than misreading the HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Keka renders these at HTTP 200 (not 404/403): an unknown slug -> "Invalid Tenant", a disabled
# careers portal -> "Forbidden Access". Either means there is no public board to read.
_DEAD_MARKERS = ("Invalid Tenant", "Forbidden Access")


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
        # step 1: portal info. A soft-error HTML page (200) means no public board.
        info = self._get()
        if any(marker in info for marker in _DEAD_MARKERS):
            return []
        tenant = self._tenant_uuid(info)
        if not tenant:
            return []
        self._tenant = tenant
        # step 2: the active-jobs array
        return json.loads(self._get())

    def _tenant_uuid(self, info: str) -> str | None:
        """The org UUID: from careerportalinfo when a background image carries it, else from the
        ``/careers`` page (portals with no custom background omit it from careerportalinfo)."""
        match = _UUID_RE.search(info)
        if match:
            return match.group(0)
        page = self._get(f"https://{self.slug}.keka.com/careers")
        match = _UUID_RE.search(page)
        return match.group(0) if match else None

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
