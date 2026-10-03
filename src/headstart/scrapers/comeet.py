"""Comeet / Spark Hire Recruit public hosted Boards (ADR-0386).

The hosted HTML embeds COMPANY_DATA and COMPANY_POSITIONS_DATA as JSON: full descriptions
without a token or per-posting request. Port has 27 postings, VAST Data 257 (2026-10-03).
A Board needs both its label and UID; a wrong label with Port's valid UID redirects to the
vendor home. Lowercase UIDs work (nSure a7.007, Scopio 87.00c), the fetch address is label/uid. The immutable UID is the Board key: renamed labels
(echo/echosoftware, both 9a.006) still publish one Board.
`time_updated` is an edit date, never a posted date. Explicit workplace_type wins over
location.is_remote: Port's Account Manager is Hybrid with that boolean true.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.jobs.job import Job, html_to_text, remote_from_workplace
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.pacer import Pacer

# Conservative process-wide starts below this session's courtesy sample; knee unknown.
_PACER = Pacer(1.0)

BOARD = re.compile(
    r"(?:https?://(?:www\.)?comeet\.co(?:m)?/jobs/)?([\w.-]+/[0-9a-f]+\.[0-9a-f]+)(?:/|$)",
    re.IGNORECASE,
)


def hosted_board(page: str) -> tuple[dict, list[dict]]:
    """Read the two public JSON assignments; missing data is never an empty Board."""
    values = []
    for name in ("COMPANY_DATA", "COMPANY_POSITIONS_DATA"):
        match = re.search(r"\b" + name + r"\s*=\s*([\[{])", page)
        if not match:
            raise ValueError(f"no {name} on hosted board")
        values.append(json.JSONDecoder().raw_decode(page[match.start(1) :])[0])
    company, rows = values
    if (
        not isinstance(company, dict)
        or not company.get("company_uid")
        or not isinstance(rows, list)
    ):
        raise ValueError("invalid hosted board envelope")
    return company, rows


class ComeetScraper(BaseScraper):
    ats = "comeet"
    url_shape = r"https://www\.comeet\.com/jobs/[\w.-]+/[\w.]+/[^/]+/[\w.]+"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        match = BOARD.search(url or tenant) or BOARD.fullmatch(tenant)
        if not match:
            raise ValueError("Comeet Board needs both label and company UID")
        return match[1].lower()

    def board_key(self) -> str:
        # A renamed label still publishes the same company's requisitions (echo/echosoftware).
        # The UID owns the Board; the label is only its current public address.
        return f"{self.ats}:{self.slug.rsplit('/', 1)[-1].lower()}"

    def url(self) -> str:
        return f"https://www.comeet.com/jobs/{self.slug}"

    def job_url(self, row: dict) -> str:
        return (
            row["url_comeet_hosted_page"]
            .split("?", 1)[0]
            .replace("www.comeet.co/", "www.comeet.com/")
        )

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        match = BOARD.search(landing_url)
        return match[1].lower() if match else None

    def fetch_raw(self) -> str:
        _PACER.wait()
        return self._get()

    def _salary_field(self, raw: Any) -> str | None:
        # No structured compensation in the measured public data; description extraction remains.
        return None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        company, rows = hosted_board(raw)
        if company["company_uid"].lower() != self.slug.rsplit("/", 1)[-1].lower():
            raise ValueError("hosted page belongs to another Comeet company")
        jobs = []
        for row in rows:
            if row.get("is_internal"):
                continue
            location = row.get("location") or {}
            fields = (row.get("custom_fields") or {}).get("details") or []
            description = "\n".join(
                f"{field.get('name', '')}\n{field['value']}"
                for field in fields
                if field.get("value")
            )
            jobs.append(
                Job(
                    id=self.job_id(row["uid"]),
                    ats=self.ats,
                    company=company["name"],
                    title=row["name"],
                    location=location.get("name"),
                    remote=remote_from_workplace(
                        row.get("workplace_type"), location.get("name")
                    ),
                    department=row.get("department"),
                    url=self.job_url(row),
                    posted_at=None,
                    scraped_at=scraped_at,
                    description=html_to_text(description),
                    experience=row.get("experience_level"),
                    employment_type=row.get("employment_type"),
                    salary=self._salary_field(row),
                )
            )
        return jobs
