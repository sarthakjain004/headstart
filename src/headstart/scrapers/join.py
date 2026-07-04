"""JOIN job-board scraper (join.com).

A company's openings live at ``https://join.com/companies/{slug}``. That careers page embeds a
Next.js ``__NEXT_DATA__`` blob carrying the numeric ``companyId``; the public jobs API then
lists the openings (paginated):
    https://join.com/api/public/companies/{companyId}/jobs?locale=en&page=N&pageSize=50
The list is summary-only, so each posting's description is fetched from its detail endpoint
    https://join.com/api/public/jobs/{id}?locale=en
in a bounded thread pool. A failed detail fetch leaves description None — the job is still kept.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper

_UA = "headstart/0.1 (job-board reader)"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_PAGE_SIZE = 5  # 2026-07: the API rejects larger values ("pageSize: Invalid value")
_MAX_PAGES = 2000  # 2000 * 5 = 10k jobs ceiling per company
_DETAIL_WORKERS = 8
_REMOTE = {
    "REMOTE": True,
    "FULLY_REMOTE": True,
    "ONSITE": False,
    "ON_SITE": False,
}  # else None


class JoinScraper(BaseScraper):
    ats = "join"

    def url(self) -> str:
        return f"https://join.com/companies/{self.slug}"

    def _company(self) -> dict:
        """The company object (incl. numeric id) from the careers page __NEXT_DATA__."""
        resp = http.fetch("GET", self.url(), headers={"User-Agent": _UA}, timeout=30)
        if resp.status_code != 200:
            return {}
        m = _NEXT.search(resp.text)
        if not m:
            return {}
        state = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
        return (state.get("initialState") or {}).get("company") or {}

    def fetch_raw(self) -> Any:
        company = self._company()
        cid = company.get("id")
        if not cid:
            return {"company": company, "items": []}
        items: list[dict] = []
        page = 1
        while page <= _MAX_PAGES:
            api = (
                f"https://join.com/api/public/companies/{cid}/jobs"
                f"?locale=en&page={page}&pageSize={_PAGE_SIZE}"
            )
            data = http.fetch(
                "GET",
                api,
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=30,
            ).json()
            if isinstance(data, list):
                break  # a bare list is the API's validation-error shape, not items
            items.extend(data.get("items") or [])
            if page >= (data.get("pagination") or {}).get("pageCount", 1):
                break
            page += 1
        # Fill each posting's description concurrently (bounded); a failed fetch leaves it None.
        if self.async_fanout_enabled():
            descriptions = self.fan_out_async(
                items,
                lambda session, it: self._job_description_async(session, it.get("id")),
            )
        else:
            descriptions = self.fan_out(
                items,
                lambda it: self._job_description(it.get("id")),
                workers=_DETAIL_WORKERS,
            )
        for item, description in zip(items, descriptions):
            item["_description"] = description
        return {"company": company, "items": items}

    def _detail_url(self, jid: str) -> str:
        return f"https://join.com/api/public/jobs/{jid}?locale=en"

    @staticmethod
    def _extract_description(response: Any) -> str | None:
        """Description, or intro/tasks/requirements joined, from a detail response (None on non-200)."""
        if response.status_code != 200:
            return None
        d = response.json()
        return (
            d.get("description")
            or "\n\n".join(
                s for s in (d.get("intro"), d.get("tasks"), d.get("requirements")) if s
            )
            or None
        )

    def _job_description(self, jid) -> str | None:
        """GET one posting's detail and return its description body (None on failure). Sync path."""
        if not jid:
            return None
        try:
            resp = http.fetch(
                "GET",
                self._detail_url(jid),
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return None
        return self._extract_description(resp)

    async def _job_description_async(self, session: Any, jid) -> str | None:
        """Same as :meth:`_job_description` but over the shared multiplexed ``AsyncSession``."""
        if not jid:
            return None
        try:
            resp = await http.fetch_async(
                session,
                "GET",
                self._detail_url(jid),
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return None
        return self._extract_description(resp)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        company_name = (raw.get("company") or {}).get("name") or self.company
        jobs: list[Job] = []
        for it in raw.get("items", []):
            city = it.get("city") or {}
            country = it.get("country") or {}
            location = (
                ", ".join(
                    p
                    for p in (
                        city.get("label") or city.get("name"),
                        country.get("name"),
                    )
                    if p
                )
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['id']}",
                    ats=self.ats,
                    company=company_name,
                    title=(it.get("title") or "").strip(),
                    location=location,
                    remote=_REMOTE.get((it.get("workplaceType") or "").upper()),
                    department=(it.get("category") or {}).get("name"),
                    url=f"https://join.com/companies/{self.slug}/{it.get('idParam', '')}",
                    posted_at=it.get("createdAt"),
                    scraped_at=scraped_at,
                    description=html_to_text(it.get("_description")),
                    employment_type=(it.get("employmentType") or {}).get("name"),
                )
            )
        return jobs
