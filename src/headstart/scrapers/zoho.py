"""Zoho Recruit career-site scraper.

Zoho server-renders the job list into the careers page as an HTML-entity-encoded
JSON array inside `<input type="hidden" id="jobs" value="[...]">`. There is no XHR
or CSRF handshake for the listing — we GET the page and extract that array.

A Zoho company's `slug` is its full careers host, e.g. "pnbcsl.zohorecruit.in"
(the data center varies: .in / .com / .eu), so the slug carries the right host.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from headstart.models import Job
from headstart.scrapers.base import BaseScraper

_JOBS_INPUT = re.compile(r'value="([^"]*)"\s+id="jobs"')
_CONFIG_AFTER_JOBS = re.compile(r'id="jobs">\s*<input[^>]*\bvalue="([^"]*)"')
_SLUG = re.compile(r"[^A-Za-z0-9]+")


class ZohoScraper(BaseScraper):
    ats = "zoho"

    def url(self) -> str:
        return f"https://{self.slug}/jobs/Careers"

    def fetch_raw(self) -> Any:
        return self._get()  # the careers page HTML, not JSON

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        match = _JOBS_INPUT.search(raw)
        if not match:
            return []
        try:
            records = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            return []

        company = self._company_name(raw) or self.company
        jobs: list[Job] = []
        for r in records:
            if r.get("Is_Locked") or not r.get("Publish", True):
                continue
            jid = r.get("id")
            if not jid:
                continue
            title = (r.get("Posting_Title") or r.get("Job_Opening_Name") or "").strip()
            location = r.get("City") or ", ".join(
                x for x in (r.get("State"), r.get("Country")) if x
            ) or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{jid}",
                    ats=self.ats,
                    company=company,
                    title=title,
                    location=location,
                    remote=bool(r.get("Remote_Job")),
                    department=(r.get("Industry") or "").strip() or None,
                    url=f"https://{self.slug}/jobs/Careers/{jid}/{_SLUG.sub('-', title)}?source=CareerSite",
                    posted_at=r.get("Date_Opened") or None,
                    scraped_at=scraped_at,
                )
            )
        return jobs

    @staticmethod
    def _company_name(raw: str) -> str | None:
        """Best-effort: the careers page embeds org_info.company_name in a config blob."""
        m = _CONFIG_AFTER_JOBS.search(raw)
        if not m:
            return None
        try:
            cfg = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            return None
        return ((cfg.get("org_info") or {}).get("company_name") or "").strip() or None
