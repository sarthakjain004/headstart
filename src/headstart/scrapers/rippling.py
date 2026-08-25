"""Rippling job-board scraper (ats.rippling.com, public board API).

A company's board lives at ``https://ats.rippling.com/{slug}``; its openings are listed at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs        (summary only)
and each posting's full record — including the HTML ``description`` (a ``{company, role}``
object) — is at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
fetched in a bounded thread pool. A failed detail fetch leaves description None — job still kept.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_API = "https://api.rippling.com/platform/api/ats/v1/board"
_DETAIL_WORKERS = 8


def _location(item: dict) -> str | None:
    wl = item.get("workLocation") or {}
    if isinstance(wl, dict) and wl.get("label"):
        return wl["label"]
    wls = (item.get("_detail") or {}).get("workLocations") or []
    return wls[0] if wls else None


def _employment_type(detail: dict) -> str | None:
    """``employmentType.label`` is a clean 6-value enum (SALARIED_FT, HOURLY_FT, ...),
    94.2% populated; ``.id`` is tenant free text (347 distinct spellings measured live, 130
    of them singletons — experiment/location-audit-2026-08-25/rippling.md). The two subfields
    are inverted from what their names suggest. Falls back to ``.id`` when ``.label`` is null
    (~5.83% of jobs, where genuinely non-enum values like "Seasonal" live) rather than losing
    the field entirely."""
    et = detail.get("employmentType") or {}
    return et.get("label") or et.get("id")


def _description(detail: dict) -> str | None:
    d = detail.get("description")
    if isinstance(d, dict):
        return d.get("role") or d.get(
            "company"
        )  # `role` is the posting; `company` is the blurb
    return d if isinstance(d, str) else None


def _pay_range(ranges: list | None) -> str | None:
    """Format the true min/max across every payRangeDetails entry, e.g. '150000-250000 USD YEAR'.

    A job can carry more than one entry (e.g. per-level or per-region bands) — reading only
    entry [0] understates the real span whenever a later entry carries a wider range (live
    measurement: 47/2,057 salaried jobs, 2.29% — experiment/location-audit-2026-08-25/rippling.md).
    Currency/frequency come from entry [0]; observed live to be constant across a job's own bands.
    """
    entries = [r for r in (ranges or []) if r]
    if not entries:
        return None
    los = [r["rangeStart"] for r in entries if r.get("rangeStart")]
    his = [r["rangeEnd"] for r in entries if r.get("rangeEnd")]
    if not los and not his:
        return None
    lo = min(los) if los else None
    hi = max(his) if his else None
    span = f"{lo:g}-{hi:g}" if lo and hi else f"{(lo or hi):g}"
    r0 = entries[0]
    return " ".join(
        str(x) for x in (span, r0.get("currency"), r0.get("frequency")) if x
    )


class RipplingScraper(BaseScraper):
    ats = "rippling"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"{_API}/{self.slug}/jobs"

    def fetch_raw(self) -> Any:
        resp = http.fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        # Raise, don't return [] — a swallowed listing error reads as an empty board and
        # hides a dead one from the ADR-0058 quarantine forever.
        resp.raise_for_status()
        data = resp.json()
        items = (
            data
            if isinstance(data, list)
            else (data.get("items") or data.get("jobs") or [])
        )
        # Fill each posting's detail concurrently (bounded); a failed fetch leaves ``_detail`` {}.
        if self.async_fanout_enabled():
            details = self.fan_out_async(
                items,
                lambda session, it: self._detail_async(session, it.get("uuid")),
                default={},
            )
        else:
            details = self.fan_out(
                items,
                lambda it: self._detail(it.get("uuid")),
                workers=_DETAIL_WORKERS,
                default={},
            )
        # {} is this scraper's failure sentinel (a real record is never empty), so map
        # falsy to None for the gap count.
        self.report_detail_gaps([d or None for d in details], "details")
        for item, detail in zip(items, details):
            item["_detail"] = detail
        return items

    def _detail_url(self, uuid: str) -> str:
        return f"{_API}/{self.slug}/jobs/{uuid}"

    @staticmethod
    def _extract_detail(response: Any) -> dict:
        return response.json() if response.status_code == 200 else {}

    def _detail(self, uuid: str | None) -> dict:
        """GET one posting's full record (``{}`` on failure). Sync path."""
        if not uuid:
            return {}
        try:
            resp = http.fetch(
                "GET",
                self._detail_url(uuid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return {}
        return self._extract_detail(resp)

    async def _detail_async(self, session: Any, uuid: str | None) -> dict:
        """Same as :meth:`_detail` but over the shared multiplexed ``AsyncSession``."""
        if not uuid:
            return {}
        try:
            resp = await http.fetch_async(
                session,
                "GET",
                self._detail_url(uuid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return {}
        return self._extract_detail(resp)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for it in raw:
            detail = it.get("_detail") or {}
            location = _location(it)
            dept = it.get("department") or detail.get("department")
            if isinstance(dept, dict):
                dept = dept.get("name")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['uuid']}",
                    ats=self.ats,
                    company=detail.get("companyName") or self.company,
                    title=(it.get("name") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=dept,
                    url=it.get("url")
                    or f"https://ats.rippling.com/{self.slug}/jobs/{it['uuid']}",
                    posted_at=detail.get("createdOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(_description(detail)),
                    employment_type=_employment_type(detail),
                    salary=_pay_range(detail.get("payRangeDetails")),
                )
            )
        return jobs
