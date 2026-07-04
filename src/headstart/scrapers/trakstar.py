"""Trakstar Hire job-board scraper ({slug}.hire.trakstar.com).

Trakstar's job API is DataDome-protected and obfuscated, but the careers landing page
server-renders the opening list into the HTML (the bot wall only guards the XHR layer, not
the document GET). So, like Zoho, we GET the page and parse the rendered job cards:
    <div class="... js-careers-page-job-list-item" data-href="/jobs/{code}/">
      <h3 class="... js-job-list-opening-name" ... title="{title}">
      <div class="... js-job-list-opening-loc" ... title="{location}">

The card has no description; each job's own page (/jobs/{code}/) embeds a schema.org
JobPosting JSON-LD block whose ``description`` we extract in a second, bounded pass. Detail
pages go through curl_cffi (TLS impersonation) since they sit behind the same DataDome edge;
a failed fetch leaves description None — the job is still kept.
"""

from __future__ import annotations

import html as _html
import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_ITEM = "js-careers-page-job-list-item"
_CODE = re.compile(r'data-href="/jobs/([^/"]+)/?"')
_TITLE = re.compile(r'js-job-list-opening-name[^>]*\btitle="([^"]*)"')
_LOC = re.compile(r'js-job-list-opening-loc[^>]*\btitle="([^"]*)"')
# the card also renders the department in a bare rb-text-4 div and the employment type in
# the opening-meta span next to it — both sit in the same block, just unread until now.
_DEPT = re.compile(r'"rb-text-4">\s*([^<]+?)\s*</div>')
_EMPTYPE = re.compile(r"js-job-list-opening-meta[^>]*>\s*<span>\s*([^<]+?)\s*</span>")
_JSONLD = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I
)
_UA = "headstart/0.1 (job-board reader)"
_DETAIL_WORKERS = 4  # detail pages sit behind DataDome — keep the concurrency gentle


class TrakstarScraper(BaseScraper):
    ats = "trakstar"

    def url(self) -> str:
        return f"https://{self.slug}.hire.trakstar.com/"

    def fetch_raw(self) -> Any:
        html = self._get()  # the careers page HTML (job cards)
        codes = [
            m.group(1) for block in html.split(_ITEM)[1:] if (m := _CODE.search(block))
        ]
        # Each job page's JSON-LD JobPosting (description + datePosted), fetched concurrently
        # (bounded); failures -> None. The detail pages sit behind DataDome, so the async path
        # pins the multiplexing width to the gentle _DETAIL_WORKERS rather than the global
        # HEADSTART_H2_STREAMS.
        if self.async_fanout_enabled():
            results = self.fan_out_async(
                codes,
                lambda session, code: self._job_posting_async(session, code),
                concurrency=_DETAIL_WORKERS,
            )
        else:
            results = self.fan_out(codes, self._job_posting, workers=_DETAIL_WORKERS)
        postings = dict(zip(codes, results))
        return {"html": html, "postings": postings}

    def _detail_url(self, code: str) -> str:
        return f"https://{self.slug}.hire.trakstar.com/jobs/{code}/"

    @staticmethod
    def _extract_posting(response: Any) -> dict | None:
        """Pull the JobPosting JSON-LD block from a detail page (None on non-200)."""
        if response.status_code != 200:
            return None
        return _jsonld_posting(response.text)

    def _job_posting(self, code: str) -> dict | None:
        """GET one job page and return its JSON-LD JobPosting (None on failure). Sync path."""
        try:
            response = http.fetch(
                "GET",
                self._detail_url(code),
                timeout=30,
                headers={"User-Agent": _UA},
            )
        except http.RequestsError:
            return None  # a missing posting must not drop the job
        return self._extract_posting(response)

    async def _job_posting_async(self, session: Any, code: str) -> dict | None:
        """Same as :meth:`_job_posting` but over the shared multiplexed ``AsyncSession``."""
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(code),
                timeout=30,
                headers={"User-Agent": _UA},
            )
        except http.RequestsError:
            return None
        return self._extract_posting(response)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        html = raw["html"] if isinstance(raw, dict) else raw
        postings = raw.get("postings", {}) if isinstance(raw, dict) else {}
        jobs: list[Job] = []
        for block in html.split(_ITEM)[1:]:
            code = _CODE.search(block)
            if not code:
                continue
            title = _TITLE.search(block)
            loc = _LOC.search(block)
            dept = _DEPT.search(block)
            emp = _EMPTYPE.search(block)
            location = _html.unescape(loc.group(1)).strip() if loc else None
            posting = postings.get(code.group(1)) or {}
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{code.group(1)}",
                    ats=self.ats,
                    company=self.company,
                    title=_html.unescape(title.group(1)).strip() if title else "",
                    location=location,
                    remote=is_remote(location),
                    department=_html.unescape(dept.group(1)).strip() if dept else None,
                    url=f"https://{self.slug}.hire.trakstar.com/jobs/{code.group(1)}/",
                    # the listing card has no date; the detail JSON-LD does
                    posted_at=posting.get("datePosted"),
                    scraped_at=scraped_at,
                    employment_type=_html.unescape(emp.group(1)).strip()
                    if emp
                    else None,
                    description=html_to_text(posting.get("description")),
                )
            )
        return jobs


def _jsonld_posting(html: str) -> dict | None:
    """Pull the JobPosting object out of a detail page's schema.org JSON-LD block."""
    for match in _JSONLD.finditer(html):
        try:
            # strict=False: the JSON-LD embeds literal newlines inside string values
            data = json.loads(match.group(1), strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None
