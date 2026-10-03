"""JobScore public HTML Boards and posting pages (ADR-0385).

The published feed asks for at most hourly polling, while direct scrapes and chained runs
can repeat faster. All fetch paths therefore read public HTML, which robots permits, and
never the feed. The atom link is read only as canonical identity, never requested.
Public cards matched the feed census on all 502 candidates; the largest was 197 jobs.
A posting's 22-character native id survives its title slug (1,833/1,833 measured ids).
"""

from __future__ import annotations

import html
import re
from functools import cached_property
from html.parser import HTMLParser
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper, DetailRequest, DetailWithoutDescription
from headstart.scrapers.job_posting_jsonld import find_job_posting, job_posting_fields
from headstart.scrapers.pacer import Pacer

# Eight starts/s stays below the bounded public-HTML ramp (120/120 HTTP 200).
_PACER = Pacer(0.125)
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _PublicPage(HTMLParser):
    """The stable JobScore card and posting fields; nested description divs stay whole."""

    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.jobs: list[dict] = []
        self.fields: dict[str, str] = {}
        self.department = ""
        self.stack: list[str] = []
        self.capture: tuple[str, int, list[str]] | None = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if "js-job-list-item" in classes:
            path = attr.get("data-url") or ""
            match = re.fullmatch(r"/careers/[\w-]+/jobs/(.+-([\w-]{22}))", path)
            if not match:
                raise ValueError("JobScore card has no stable posting id")
            self.jobs.append(
                {"id": match[2], "url_slug": match[1], "department": self.department}
            )
        if self.capture:
            self.capture[2].append(self.get_starttag_text())
        else:
            key = next(
                (
                    key
                    for cls, key in (
                        ("js-job-department", "department"),
                        ("js-job-title", "card_title"),
                        ("js-job-location", "card_location"),
                        ("js-title", "title"),
                        ("js-subtitle", "subtitle"),
                        ("js-job-description", "description"),
                    )
                    if cls in classes
                ),
                "page_title" if tag == "title" else None,
            )
            if key:
                self.capture = (key, len(self.stack), [])
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in _VOID or tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        del self.stack[index:]
        if self.capture:
            key, depth, parts = self.capture
            if len(self.stack) <= depth:
                text = html_to_text("".join(parts)) or ""
                self.capture = None
                if key == "department":
                    self.department = text
                elif key.startswith("card_") and self.jobs:
                    self.jobs[-1][key.removeprefix("card_")] = text
                else:
                    self.fields[key] = text
            else:
                parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.capture:
            self.capture[2].append(html.escape(data))


def public_board(page: str) -> dict:
    """Canonical label, company name and complete public cards; unreadable is not empty."""
    canonical = re.search(r"careers\.jobscore\.com/jobs/([\w-]+)/feed\.atom", page)
    parsed = _PublicPage(page)
    if not canonical or (
        not parsed.jobs and "there are no open positions at this time" not in page
    ):
        raise ValueError("not a readable JobScore public board")
    if any(not row.get("title") for row in parsed.jobs):
        raise ValueError("JobScore card has no title")
    name = parsed.fields.get("page_title", "").split(
        " Jobs, Careers & Employment Opportunities", 1
    )[0]
    return {
        "company_code": canonical[1].lower(),
        "company_name": name,
        "jobs": list({r["id"]: r for r in parsed.jobs}.values()),
    }


class JobScoreScraper(BaseScraper):
    ats = "jobscore"
    spare_on_transport_error = True
    url_shape = r"https://careers\.jobscore\.com/careers/[\w-]+/jobs/[^/?#]+"
    has_detail_pass = True
    detail_workers = 8

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        match = re.search(
            r"jobscore\.com/(?:careers|jobs)/([\w-]+)", url or tenant, re.IGNORECASE
        )
        return (match[1] if match else tenant).strip().lower()

    def url(self) -> str:
        return f"https://careers.jobscore.com/careers/{self.slug}"

    def job_url(self, row: dict) -> str:
        return f"{self.url()}/jobs/{row['url_slug']}"

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        _PACER.wait()
        return super()._fetch(method, url, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await _PACER.wait_async()
        return await super()._fetch_async(session, method, url, **kwargs)

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        match = re.search(r"jobscore\.com/(?:careers|jobs)/([\w-]+)", landing_url)
        return match[1].lower() if match else None

    def fetch_raw(self) -> dict:
        _PACER.wait()
        self._unavailable.clear()
        raw = public_board(self._get())
        if raw["company_code"] != self.slug:
            raise ValueError("JobScore board belongs to another canonical label")
        # The posting can supply/change department; no measured title-only gate.
        # Re-read held details because they also carry date, employment type and salary.
        raw["details"] = self.run_detail_pass(
            raw["jobs"], key_of=lambda row: row["id"], what="public posting pages"
        )
        raw["unavailable"] = sorted(self._unavailable)
        return raw

    def detail_request(self, row: dict) -> DetailRequest:
        return DetailRequest(self.job_url(row))

    @cached_property
    def _unavailable(self) -> set[str]:
        return set()

    def detail_status_loss(self, response: Any) -> str:
        # The provider's own cards can point at gone postings: the Pricefx card's
        # exact link failed in the browser and returned 404 in both HTTP clients.
        if response.status_code in (404, 410):
            url = getattr(response, "url", "")
            if url.startswith(self.url() + "/jobs/"):
                self._unavailable.add(url.rsplit("/", 1)[-1][-22:])
        return super().detail_status_loss(response)

    def read_detail(self, row: dict, response: Any) -> dict | DetailWithoutDescription:
        parsed = _PublicPage(response.text)
        schema = find_job_posting(response.text) or {}
        detail = {**job_posting_fields(schema), **parsed.fields, "schema": schema}
        detail["description"] = html_to_text(
            parsed.fields.get("description") or schema.get("description")
        )
        parts = detail.get("subtitle", "").split("|")
        if len(parts) >= 3:
            detail.update(
                department=parts[0].strip(),
                location=parts[1].strip(),
                employment_type=parts[2].strip(),
            )
        if not detail.get("description"):
            return DetailWithoutDescription(detail, "200 without description")
        return detail

    def _salary_field(self, raw: Any) -> str | None:
        pay = (raw.get("schema") or {}).get("baseSalary") or {}
        value = pay.get("value") or {}
        if not isinstance(value, dict):
            return None
        low = value.get("minValue", value.get("value"))
        if low is None:
            return None  # a lone ceiling is not a floor
        return salary.to_field(
            low, value.get("maxValue"), pay.get("currency"), value.get("unitText")
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        if not isinstance(raw, dict) or not isinstance(raw.get("jobs"), list):
            self.note_unreadable_board("public job cards", "invalid public page")
            raise TypeError("invalid JobScore public page")
        jobs = []
        for row in raw["jobs"]:
            if row["id"] in raw.get("unavailable", ()):
                continue
            detail = raw.get("details", {}).get(row["id"]) or {}
            location = detail.get("location") or row.get("location")
            remote = (
                None
                if "hybrid" in (location or "").lower()
                else True
                if detail.get("remote")
                else is_remote(location)
            )
            jobs.append(
                Job(
                    id=self.job_id(row["id"]),
                    ats=self.ats,
                    company=raw.get("company_name") or self.company,
                    title=detail.get("title") or row["title"],
                    location=location,
                    remote=remote,
                    department=detail.get("department") or row.get("department"),
                    url=self.job_url(row),
                    posted_at=detail.get("posted_at"),
                    scraped_at=scraped_at,
                    description=detail.get("description"),
                    employment_type=detail.get("employment_type"),
                    salary=self._salary_field(detail),
                )
            )
        return jobs
