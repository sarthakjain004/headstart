"""Oracle Taleo Enterprise Career Section scraper.

Enterprise Career Sections are distinct from Taleo Business Edition.  A board is
the full ``https://{zone}.taleo.net/careersection/{section}`` address.  Its HTML
shell provides a portal id and the configured result headers; the public JSON
job-board endpoint provides paginated listings; jobdetail pages provide bodies.
"""

from __future__ import annotations

import html
import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_PORTAL = re.compile(r"portalNo:\s*'?(\d+)")
_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_JOBS_TABLE = re.compile(
    r'<table[^>]+id="jobs"[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE
)
_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
_INITIAL_HISTORY = re.compile(
    r'id="initialHistory"[^>]*value="(.*?)"', re.DOTALL | re.IGNORECASE
)
_SECTION = re.compile(r"^/careersection/([^/?#]+)/?")
_DETAIL_WORKERS = (
    4  # conservative until a completed Enterprise rate-limit measurement exists
)


def _canonical(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.hostname.endswith(".taleo.net"):
        raise ValueError(f"not a Taleo Enterprise host: {url!r}")
    match = _SECTION.match(parsed.path)
    if not match:
        raise ValueError(f"no Career Section in URL: {url!r}")
    return urlunsplit(
        ("https", parsed.hostname.lower(), f"/careersection/{match.group(1)}", "", "")
    )


def _headers(shell: str) -> list[str]:
    table = _JOBS_TABLE.search(shell)
    if not table:
        return []
    return [
        text for value in _TH.findall(table.group(1)) if (text := html_to_text(value))
    ]


def _company(shell: str) -> str | None:
    match = _TITLE.search(shell)
    if not match:
        return None
    title = html_to_text(match.group(1))
    if not title:
        return None
    return title.removeprefix("Careers | ").strip() or None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            pass
    return None


def _location(values: list[Any], indices: list[int]) -> str | None:
    places: list[str] = []
    for index in indices:
        if index >= len(values) or not isinstance(values[index], str):
            continue
        try:
            parts = json.loads(values[index])
        except json.JSONDecodeError:
            parts = [values[index]]
        places.extend(
            part.strip() for part in parts if isinstance(part, str) and part.strip()
        )
    return "; ".join(dict.fromkeys(places)) or None


def _column(
    headers: list[str], values: list[Any], words: tuple[str, ...]
) -> str | None:
    for index, header in enumerate(headers):
        if index < len(values) and any(word in header.lower() for word in words):
            value = values[index]
            return value.strip() if isinstance(value, str) and value.strip() else None
    return None


def _description(page: str) -> str | None:
    match = _INITIAL_HISTORY.search(page)
    if not match:
        return None
    decoded = unquote(html.unescape(match.group(1))).replace(r"\:", ":")
    parts = [html_to_text(part) for part in decoded.split("!*!") if "<" in part]
    return " ".join(dict.fromkeys(part for part in parts if part)) or None


class TaleoEnterpriseScraper(BaseScraper):
    """Public Oracle Taleo Enterprise Career Section scraper."""

    ats = "taleo_enterprise"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return _canonical(url)

    def board_key(self) -> str:
        return f"{self.ats}:{_canonical(self.slug)}"

    def url(self) -> str:
        return f"{_canonical(self.slug)}/jobsearch.ftl?lang=en"

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "tz": "GMT+00:00",
            "Referer": self.url(),
        }

    def _listing(self, shell: str) -> list[dict[str, Any]]:
        portal = _PORTAL.search(shell)
        if not portal:
            raise ValueError("Career Section shell has no portalNo")
        headers = _headers(shell)
        parsed = urlsplit(_canonical(self.slug))
        api = (
            f"{parsed.scheme}://{parsed.netloc}/careersection/rest/jobboard/searchjobs?"
            + urlencode({"lang": "en", "portal": portal.group(1)})
        )
        payload = {
            "advancedSearchFiltersSelectionParam": {
                "searchFilterSelections": [
                    {"id": key, "selectedValues": []}
                    for key in (
                        "ORGANIZATION",
                        "LOCATION",
                        "JOB_FIELD",
                        "URGENT_JOB",
                        "EMPLOYEE_STATUS",
                        "STUDY_LEVEL",
                        "WILL_TRAVEL",
                        "JOB_SHIFT",
                        "JOB_NUMBER",
                    )
                ]
            },
            "fieldData": {
                "fields": {"JOB_TITLE": "", "KEYWORD": "", "LOCATION": ""},
                "valid": True,
            },
            "filterSelectionParam": {
                "searchFilterSelections": [
                    {"id": key, "selectedValues": []}
                    for key in (
                        "POSTING_DATE",
                        "LOCATION",
                        "JOB_FIELD",
                        "JOB_TYPE",
                        "JOB_SCHEDULE",
                        "JOB_LEVEL",
                    )
                ]
            },
            "multilineEnabled": False,
            "sortingSelection": {
                "ascendingSortingOrder": "false",
                "sortBySelectionParam": "3",
            },
        }
        listed: list[dict[str, Any]] = []
        seen: set[str] = set()
        total: int | None = None
        pages: int | None = None
        for page_no in range(1, 2_001):
            response = http.fetch(
                "POST",
                api,
                json={**payload, "pageNo": page_no},
                headers=self._request_headers(),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            paging = data.get("pagingData") or {}
            if total is None:
                total, size = paging.get("totalCount"), paging.get("pageSize")
                if not isinstance(total, int) or not isinstance(size, int) or size < 1:
                    raise ValueError(
                        "Career Section listing omitted totalCount/pageSize"
                    )
                pages = math.ceil(total / size)
            for record in data.get("requisitionList") or []:
                job_id = str(record.get("jobId") or "")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                values = record.get("column") or []
                title_index = record.get("linkedColumn")
                title = (
                    values[title_index]
                    if isinstance(title_index, int) and title_index < len(values)
                    else None
                )
                locations = _location(values, record.get("locationsColumns") or [])
                listed.append(
                    {
                        "id": job_id,
                        "title": title,
                        "location": locations,
                        "department": _column(
                            headers,
                            values,
                            ("department", "job field", "job category", "function"),
                        ),
                        "employment_type": _column(
                            headers,
                            values,
                            (
                                "employment type",
                                "job schedule",
                                "appointment type",
                                "position type",
                            ),
                        ),
                        "posted_at": _date(_column(headers, values, ("posting date",))),
                        "url": f"{_canonical(self.slug)}/jobdetail.ftl?"
                        + urlencode({"lang": "en", "job": job_id}),
                    }
                )
            if pages is not None and page_no >= pages:
                break
        if total is not None and len(seen) < total:
            self.mark_truncated_unless_negligible(
                len(seen), total, f"read {len(seen)} of {total} requisitions"
            )
        return listed

    def _detail(self, url: str) -> str | None:
        try:
            return _description(self._get(url))
        except Exception as exc:  # noqa: BLE001 - listing survives one detail failure
            self.note_detail_exception(exc)
            return None

    def fetch_raw(self) -> Any:
        shell = self._get()
        self.company = _company(shell) or self.company
        listed = self._listing(shell)
        details = self.fan_out(
            listed, lambda item: self._detail(item["url"]), workers=self.detail_workers
        )
        self.report_detail_gaps(details, "detail pages")
        return list(zip(listed, details))

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        return [
            Job(
                id=f"{self.board_key()}:{item['id']}",
                ats=self.ats,
                company=self.company,
                title=item["title"] or "",
                location=item["location"],
                remote=is_remote(item["location"]),
                department=item["department"],
                url=item["url"],
                posted_at=item["posted_at"],
                scraped_at=scraped_at,
                description=detail,
                employment_type=item["employment_type"],
            )
            for item, detail in raw
        ]
