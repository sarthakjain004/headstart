"""Polymer's unauthenticated public hiring API (ADR-0387).

The listing states `meta.count` postings and `meta.total` pages, not the reverse.
Asking past the end returns an empty list but is_last=false and an increasing next_page
(Aru Labs page 2, 2026-10-03), so the declared count/page bound must end the walk.
Descriptions and a distinct department need the public detail; no title-only tech gate.
"""

from __future__ import annotations

import re
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, remote_from_workplace
from headstart.scrapers.base import (
    DEFAULT_REQUEST_HEADERS,
    BaseScraper,
    DetailRequest,
    DetailWithoutDescription,
)
from headstart.scrapers.country_codes import ISO_ALPHA2_NAMES
from headstart.scrapers.pacer import Pacer

# Mixed-board throughput flattened after eight concurrent (120/120 HTTP 200).
_PACER = Pacer(0.125)


class PolymerScraper(BaseScraper):
    ats = "polymer"
    spare_on_transport_error = True
    has_detail_pass = True
    detail_workers = 8
    url_shape = r"https://jobs\.polymer\.co/[\w-]+/\d+"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        match = re.search(r"jobs\.polymer\.co/([\w-]+)", url or tenant, re.IGNORECASE)
        return (match[1] if match else tenant).lower().strip()

    def url(self) -> str:
        return f"https://api.polymer.co/v1/hire/organizations/{self.slug}/jobs"

    def job_url(self, native_id: str) -> str:
        return f"https://jobs.polymer.co/{self.slug}/{native_id}"

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        match = re.search(
            r"(?:jobs\.polymer\.co/|api\.polymer\.co/v1/hire/organizations/)([\w-]+)",
            landing_url,
        )
        return match[1].lower() if match else None

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        _PACER.wait()
        return super()._fetch(method, url, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await _PACER.wait_async()
        return await super()._fetch_async(session, method, url, **kwargs)

    def fetch_raw(self) -> dict:
        response = self._fetch(
            "GET", self.url(), headers=dict(DEFAULT_REQUEST_HEADERS), timeout=30
        )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            self.note_unreadable_board("an items list", "invalid Polymer feed")
            raise TypeError("invalid Polymer feed")
        meta = raw.get("meta") or {}
        expected, pages = meta.get("count"), meta.get("total")
        if not isinstance(expected, int) or not isinstance(pages, int):
            raise TypeError("Polymer listing has no count/page bound")
        rows = {str(row["id"]): row for row in raw["items"]}
        for page in range(2, pages + 1):
            if len(rows) >= expected:
                break
            try:
                response = self._fetch(
                    "GET",
                    f"{self.url()}?page={page}",
                    headers=dict(DEFAULT_REQUEST_HEADERS),
                    timeout=30,
                )
                response.raise_for_status()
                following = response.json()
                batch = following["items"]
                if not isinstance(batch, list):
                    raise TypeError("invalid items on next page")
                before = len(rows)
                rows.update((str(row["id"]), row) for row in batch)
            except Exception as exc:  # noqa: BLE001 - preserve the read prefix, never certify it whole
                self.mark_truncated(f"listing page {page}: {type(exc).__name__}")
                break
            if len(rows) == before:
                break
        self.mark_truncated_unless_negligible(len(rows), expected, "Polymer meta.count")
        raw["items"] = list(rows.values())
        raw["details"] = self.run_detail_pass(
            raw["items"],
            key_of=lambda row: str(row["id"]),
            what="public job details",
        )
        return raw

    def detail_request(self, row: dict) -> DetailRequest:
        return DetailRequest(f"{self.url()}/{row['id']}")

    def read_detail(self, row: dict, response: Any) -> dict | DetailWithoutDescription:
        detail = response.json()
        if not detail.get("description"):
            return DetailWithoutDescription(detail, "200 without description")
        return detail

    def _salary_field(self, raw: Any) -> str | None:
        text = raw.get("salary_pretty")
        if not text:
            return None
        # Public strings say "an hour" / "a month"; the shared field codec needs a
        # period phrase (13.50 USD an hour and 1,200-1,400 USD a month, 2026-10-03).
        text = re.sub(
            r"(\d+(?:,\d{3})*(?:\.\d+)?)K\b",
            lambda m: str(float(m[1].replace(",", "")) * 1000),
            text,
        )
        text = re.sub(r"\b(?:a|an) (hour|day|week|month|year)\b", r"per-\1", text)
        match = re.fullmatch(
            r"([\d,.]+)(?:\s*-\s*([\d,.]+))? ([A-Z]{3}) (per-(?:hour|day|week|month|year))",
            text,
        )
        if not match:
            return None
        return salary.to_field(
            match[1].replace(",", ""),
            match[2].replace(",", "") if match[2] else None,
            match[3],
            match[4],
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            self.note_unreadable_board("an items list", "invalid Polymer feed")
            raise TypeError("invalid Polymer feed")
        jobs = []
        for row in raw["items"]:
            detail = raw.get("details", {}).get(str(row["id"])) or {}
            location = ", ".join(
                x
                for x in (row.get("city"), row.get("state_region"), row.get("country"))
                if x
            ) or row.get("display_location")
            remote_places = [
                ISO_ALPHA2_NAMES.get(code, code)
                for code in row.get("remote_restriction_country_list") or []
            ]
            location = (
                "; ".join(
                    dict.fromkeys(
                        x
                        for x in [
                            location,
                            row.get("remote_restriction_city"),
                            *remote_places,
                        ]
                        if x
                    )
                )
                or None
            )
            workplace = row.get("remoteness_pretty")
            remote = (
                False
                if workplace == "No remote"
                else remote_from_workplace(workplace, location)
            )
            jobs.append(
                Job(
                    id=self.job_id(row["id"]),
                    ats=self.ats,
                    company=row.get("organization_name") or self.company,
                    title=row["title"],
                    location=location,
                    remote=remote,
                    department=detail.get("department") or row.get("job_category_name"),
                    url=self.job_url(row["id"]),
                    posted_at=row.get("published_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("description")),
                    employment_type=row.get("kind_pretty"),
                    salary=self._salary_field(row),
                )
            )
        return jobs
