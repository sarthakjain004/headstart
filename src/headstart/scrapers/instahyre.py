"""Instahyre public marketplace listings (ADR-0389).

The complete anonymous listing names an Instahyre employer profile beside each current job.
That profile is not an ATS Board or verified company identity, so it is kept only as marketplace
metadata. The public detail gives HTML description and experience bounds, but no posting date or
usable native salary.

The list reports 12,919 current jobs in 35-row pages but rejects offset 9,975. Its 107 public
job-function ids, accepted at most three per request, union with the accessible prefix back to
all 12,919 ids; those listing pages run at 32 workers. A repeated-id detail burst reached 128
HTTP 200s, but distinct detail traffic later returned 429 with Retry-After 55, so 64 thread
workers retain the repository's 429 spare-egress fallback instead of treating that burst as a
durable budget. Measurements and the identity caveat: docs/instahyre/2026-10-03_public-api-and-scraper-research.md.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    DEFAULT_REQUEST_HEADERS,
    BaseScraper,
    DetailRequest,
    DetailWithoutDescription,
)


class InstahyreScraper(BaseScraper):
    ats = "instahyre"
    source_kind = "marketplace"
    url_shape = r"https://www\.instahyre\.com/job-\d+-[^/]+/?$"
    has_detail_pass = True
    # A repeated-id ramp reached 128 HTTP 200s, but a distinct-job crawl later exhausted the
    # route's window. Threaded requests avoid the shared HTTP/2 stall; 429 rotates egress below.
    async_fanout = False
    detail_workers = 64
    egress_fallback_on = frozenset({429})
    _LISTING_PAGE_SIZE = 35
    _LISTING_WORKERS = 32
    _GLOBAL_OFFSET_CAP = 9_975
    _FUNCTION_GROUP_SIZE = 3
    _LISTING_ENDPOINT = "https://www.instahyre.com/api/v1/job_search"
    _FUNCTIONS_ENDPOINT = "https://www.instahyre.com/api/v1/job_function/"

    def url(self) -> str:
        return self._listing_url(0)

    def _listing_url(self, offset: int, functions: tuple[int, ...] = ()) -> str:
        query = [
            ("limit", str(self._LISTING_PAGE_SIZE)),
            ("offset", str(offset)),
            *(("job_functions", str(function)) for function in functions),
        ]
        return f"{self._LISTING_ENDPOINT}?{urlencode(query)}"

    def job_url(self, row: dict) -> str:
        return row["public_url"]

    def fetch_raw(self) -> Any:
        def listing_page(url: str) -> dict:
            response = self._fetch(
                "GET", url, headers=dict(DEFAULT_REQUEST_HEADERS), timeout=30
            )
            response.raise_for_status()
            page = response.json()
            batch = page.get("objects") if isinstance(page, dict) else None
            meta = page.get("meta") if isinstance(page, dict) else None
            if not isinstance(batch, list) or not isinstance(meta, dict):
                raise TypeError("Instahyre listing has no objects/meta envelope")
            return page

        def read_pages(
            pending: dict[tuple[tuple[int, ...], int], str], label: str
        ) -> dict:
            pages: dict[tuple[tuple[int, ...], int], dict] = {}
            for attempt in range(1, 4):
                if not pending:
                    break
                requested = list(pending.items())
                outcomes = self.fan_out(
                    [url for _, url in requested],
                    listing_page,
                    workers=self._LISTING_WORKERS,
                    what=self.board_key(),
                )
                pending = {}
                for (key, url), page in zip(requested, outcomes):
                    if page is None or page["meta"].get("offset") not in (None, key[1]):
                        pending[key] = url
                    else:
                        pages[key] = page
                if pending:
                    self._log.info(
                        f"{self.board_key()}: retrying {len(pending)} unread {label} page(s) "
                        f"after pass {attempt}"
                    )
            if pending:
                self._log.info(
                    f"{self.board_key()}: {len(pending)} {label} page(s) remained unread"
                )
            return pages

        first = listing_page(self.url())
        first_meta = first["meta"]
        expected = first_meta.get("total_count")
        if not isinstance(expected, int):
            self.note_unreadable_board(
                "Instahyre numeric total_count", repr(first_meta)
            )
            raise TypeError("Instahyre listing has no numeric total_count")
        rows = {
            str(row["id"]): row
            for row in first["objects"]
            if isinstance(row, dict) and row.get("id") is not None
        }
        if expected > len(rows) and not first_meta.get("next"):
            self.mark_truncated("Instahyre listing omitted its next cursor")
        global_end = min(expected, self._GLOBAL_OFFSET_CAP)
        global_pages = read_pages(
            {
                ((), offset): self._listing_url(offset)
                for offset in range(
                    self._LISTING_PAGE_SIZE, global_end, self._LISTING_PAGE_SIZE
                )
            },
            "global listing",
        )
        for page in global_pages.values():
            for row in page["objects"]:
                if isinstance(row, dict) and row.get("id") is not None:
                    rows.setdefault(str(row["id"]), row)
        if expected > self._GLOBAL_OFFSET_CAP:
            try:
                response = self._fetch(
                    "GET",
                    self._FUNCTIONS_ENDPOINT,
                    headers=dict(DEFAULT_REQUEST_HEADERS),
                    timeout=30,
                )
                response.raise_for_status()
                catalog = response.json()
                functions = [
                    int(row["id"])
                    for row in (
                        catalog.get("objects") if isinstance(catalog, dict) else []
                    )
                    or []
                    if isinstance(row, dict) and isinstance(row.get("id"), int)
                ]
            except Exception as exc:  # noqa: BLE001 - preserve the global prefix
                self.mark_truncated(
                    f"Instahyre job-function catalog: {type(exc).__name__}"
                )
                functions = []
            if not functions:
                self.mark_truncated("Instahyre job-function catalog was unreadable")
            groups = [
                tuple(functions[start : start + self._FUNCTION_GROUP_SIZE])
                for start in range(0, len(functions), self._FUNCTION_GROUP_SIZE)
            ]
            first_slices = read_pages(
                {(group, 0): self._listing_url(0, group) for group in groups},
                "job-function listing",
            )
            following = {
                (group, offset): self._listing_url(offset, group)
                for (group, _), page in first_slices.items()
                for offset in range(
                    self._LISTING_PAGE_SIZE,
                    min(page["meta"].get("total_count", 0), self._GLOBAL_OFFSET_CAP),
                    self._LISTING_PAGE_SIZE,
                )
            }
            sliced_pages = first_slices | read_pages(following, "job-function listing")
            for page in sliced_pages.values():
                for row in page["objects"]:
                    if isinstance(row, dict) and row.get("id") is not None:
                        rows.setdefault(str(row["id"]), row)
        self.mark_truncated_unless_negligible(
            len(rows), expected, "Instahyre meta.total_count"
        )
        listed = list(rows.values())
        return {
            "objects": listed,
            "details": self.run_detail_pass(
                listed,
                key_of=lambda row: str(row.get("id") or ""),
                title_of=lambda row: row.get("title"),
                what="public marketplace job details",
            ),
        }

    def detail_request(self, row: dict) -> DetailRequest:
        return DetailRequest(
            f"https://www.instahyre.com/api/v1/employer_public_jobs/{row['id']}"
        )

    def read_detail(self, row: dict, response: Any) -> dict | DetailWithoutDescription:
        detail = response.json()
        if not isinstance(detail, dict) or not detail.get("description"):
            return DetailWithoutDescription(
                detail if isinstance(detail, dict) else {}, "200 without description"
            )
        return detail

    @staticmethod
    def _experience(detail: dict) -> str | None:
        low, high = detail.get("workex_min"), detail.get("workex_max")
        if isinstance(low, int) and isinstance(high, int):
            return f"{low}-{high} years"
        return None

    @staticmethod
    def _remote(location: str | None) -> bool | None:
        places = [
            place.strip().lower() for place in (location or "").split(";") if place
        ]
        if places == ["work from home"]:
            return True
        if "work from home" in places:
            return (
                None  # a named city plus this label does not state a fully remote job
            )
        return is_remote(location)

    def _salary_field(self, raw: Any) -> str | None:
        # No full-time salary is present; internship stipends omit currency and pay period.
        return None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        rows = raw.get("objects") if isinstance(raw, dict) else None
        details = raw.get("details") if isinstance(raw, dict) else None
        if not isinstance(rows, list) or not isinstance(details, dict):
            self.note_unreadable_board("Instahyre objects and details", repr(raw))
            raise TypeError("invalid Instahyre marketplace response")
        jobs = []
        for row in rows:
            native_id = str(row.get("id") or "")
            employer = row.get("employer") or {}
            detail = details.get(native_id) or {}
            if not native_id or not row.get("title") or not row.get("public_url"):
                continue
            location = "; ".join(detail.get("locations") or []) or row.get("locations")
            internship = detail.get("is_internship")
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=employer.get("company_name")
                    or detail.get("hiring_company_name")
                    or "",
                    title=row["title"],
                    location=location,
                    remote=self._remote(location),
                    department=None,
                    url=self.job_url(row),
                    posted_at=None,
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("description")),
                    experience=self._experience(detail),
                    employment_type=(
                        "Internship"
                        if internship is True
                        else "Full-time"
                        if internship is False
                        else None
                    ),
                    salary=self._salary_field(detail),
                    marketplace_employer_id=(
                        str(employer["id"]) if employer.get("id") is not None else None
                    ),
                )
            )
        return jobs
