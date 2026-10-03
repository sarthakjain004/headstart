"""Recruiterflow's anonymous, server-rendered public careers pages.

Measured 2026-10-03 on 41 candidate Boards: `window.jobsList` is the complete
listing, repeated in department and location views. The largest held 112 Jobs;
page/limit parameters did not paginate it. Actual department/location query
parameters do filter it, so the scraper constructs an unfiltered Board URL.

The department view supplies titles and departments. The location view preserves
all places: 170/662 Jobs named multiple locations, while all 78 sampled detail
JSON-LD records named only one. Never replace the listing locations with that field.
See docs/recruiterflow/2026-10-03_public-api-measurement.md.
"""

import json
import math
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
    gone_board_error,
)
from headstart.scrapers.pacer import Pacer

# 159 successful requests were measured at <=0.8 starts/s, not a saturation test.
# One process-wide bound keeps concurrent Boards within that observed envelope.
_PACER = Pacer(1.25)


def _embedded_object(page: str, assignment: str) -> dict | None:
    match = re.search(assignment + r"\s*=\s*", page)
    if not match:
        return None
    value, _end = json.JSONDecoder().raw_decode(page[match.end() :])
    if not isinstance(value, dict):
        raise TypeError("public page data is not an object")
    return value


class _BoardName(HTMLParser):
    name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "og:title":
            title = values.get("content") or ""
            suffix = " is hiring! Apply now."
            if title.endswith(suffix):
                self.name = title.removesuffix(suffix).strip() or None


def public_listing(page: str) -> dict:
    """The complete public listing, before normalizing its duplicate grouping views."""
    value = _embedded_object(page, r"\bwindow\.jobsList")
    if value is None:
        raise BoardUnreadable("Recruiterflow page has no jobsList")
    return value


def public_detail(page: str) -> dict | None:
    """The public Job record, shared by detail reading and alias verification."""
    return _embedded_object(page, r"\bvar\s+convertedToJSON")


def inactive_board(page: str) -> bool:
    """The measured inactive template, distinct from a valid empty jobsList."""
    return (
        "window.jobsList" not in page
        and "Oops! No jobs found." in page
        and "We are currently not accepting any applications" in page
    )


def listed_jobs(listing: dict) -> list[dict]:
    """One record per native id, with the department and every listed location."""
    locations: dict[int, list[str]] = {}
    for location, rows in listing["location"]:
        if location:
            for row in rows:
                locations.setdefault(row["job_id"], []).append(location)
    jobs: dict[int, dict] = {}
    for department, rows in listing["department"]:
        for row in rows:
            if (
                not isinstance(row, dict)
                or not str(row.get("job_id", "")).isdecimal()
                or not row.get("job_name")
            ):
                raise BoardUnreadable(
                    "Recruiterflow listing has an incomplete Job identity"
                )
            native_id = row["job_id"]
            jobs[native_id] = {
                **row,
                "department": department or None,
                "location": "; ".join(dict.fromkeys(locations.get(native_id, [])))
                or row.get("details")
                or None,
            }
    other_ids = set(locations)
    for _group, rows in listing.get("group", []):
        other_ids.update(row["job_id"] for row in rows)
    if other_ids - jobs.keys():
        raise BoardUnreadable("Recruiterflow grouping views disagree about listed Jobs")
    return list(jobs.values())


def _posted_at(value: str | None) -> str | None:
    return datetime.fromisoformat(value).isoformat() if value else None


def _remote(row: dict) -> bool | None:
    if row.get("remote_type") == "Remote":
        return True
    if row.get("remote_type") == "Hybrid":
        return None
    return is_remote(row.get("location"))


def _experience(detail: dict) -> str | None:
    low, high = detail.get("experience_range_start"), detail.get("experience_range_end")
    if not isinstance(low, int | float) or not math.isfinite(low):
        return None
    if isinstance(high, int | float) and math.isfinite(high):
        return f"{math.floor(low)}-{math.ceil(high)} years"
    return f"{math.floor(low)}+ years"


class RecruiterflowScraper(BaseScraper):
    ats = "recruiterflow"
    url_shape = r"https://recruiterflow\.com/[^/?#]+/jobs/\d+"
    has_detail_pass = True
    detail_workers = 1
    pacer = _PACER

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        value = (url or tenant).strip()
        if "://" in value:
            parts = urlsplit(value)
            path = parts.path.strip("/").split("/")
            if (
                parts.hostname not in {"recruiterflow.com", "www.recruiterflow.com"}
                or len(path) < 2
                or path[1] != "jobs"
            ):
                raise ValueError(f"not a Recruiterflow Board URL: {value!r}")
            return unquote(path[0]).lower()
        return value.lower()

    def url(self) -> str:
        return f"https://recruiterflow.com/{quote(self.slug, safe='')}/jobs"

    def job_url(self, native_id: str) -> str:
        return f"{self.url()}/{native_id}"

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        self.pacer.wait()
        return super()._fetch(method, url, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await self.pacer.wait_async()
        return await super()._fetch_async(session, method, url, **kwargs)

    def fetch_raw(self) -> Any:
        response = self._fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=30,
        )
        response.raise_for_status()
        page = response.text
        if inactive_board(page):
            raise gone_board_error("Recruiterflow Board is not accepting applications")
        try:
            listing = public_listing(page)
            rows = listed_jobs(listing)
        except (ValueError, KeyError, TypeError) as exc:
            self.note_unreadable_board("public jobsList grouping data", str(exc))
            raise BoardUnreadable("Recruiterflow listing is unreadable") from exc
        name = _BoardName()
        name.feed(page)
        self.adopt_company(name.name)
        details = self.run_detail_pass(
            rows,
            key_of=lambda row: str(row["job_id"]),
            what="Recruiterflow public job details",
            title_of=lambda row: row["job_name"],
            department_of=lambda row: row["department"],
        )
        for detail in details.values():
            if detail.get("company_name"):
                self.adopt_company(detail["company_name"])
                break
        return {"listing": listing, "details": details}

    def detail_request(self, item: dict) -> DetailRequest:
        return DetailRequest(self.job_url(str(item["job_id"])))

    def read_detail(self, item: dict, response: Any) -> Any:
        detail = public_detail(response.text)
        if detail is None or str(detail.get("job_id")) != str(item["job_id"]):
            raise DetailLost("no matching public job data")
        if detail.get("job_visibility_id") == 6:
            raise DetailLost("job no longer open")
        if not html_to_text(detail.get("about_position")):
            return DetailWithoutDescription(detail, "public job has no description")
        return detail

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs = []
        for row in listed_jobs(raw["listing"]):
            detail = raw["details"].get(str(row["job_id"]), {})
            jobs.append(
                Job(
                    id=self.job_id(str(row["job_id"])),
                    ats=self.ats,
                    company=self.company,
                    title=row["job_name"],
                    department=row["department"],
                    location=row["location"],
                    remote=_remote(row),
                    url=self.job_url(str(row["job_id"])),
                    posted_at=_posted_at(row.get("last_opened")),
                    scraped_at=scraped_at,
                    employment_type=row.get("employment_type"),
                    description=html_to_text(detail.get("about_position")),
                    experience=_experience(detail),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        # No salary field in 78 measured public details; prose uses the shared extractor.
        return None
