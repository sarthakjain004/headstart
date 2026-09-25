"""Oracle Taleo Enterprise Career Section scraper.

Enterprise Career Sections are distinct from Taleo Business Edition.  A board is
the full ``https://{zone}.taleo.net/careersection/{section}`` address.  Its HTML
shell provides a portal id and the configured result headers; the public JSON
job-board endpoint provides paginated listings; jobdetail pages provide bodies
and, on some tenants, compensation (see ``_salary_field``).

Reading compensation into ``salary`` does NOT need a `doc_prep.DERIVATIONS_VERSION` bump: like
smartrecruiters' own native-compensation field (see that scraper's docstring), ``salary`` is a
re-observed FACT_FIELD, so once a Board is rescraped its now-populated raw ``Job.salary`` differs
from the stored one and `refresh_row`'s `salary_inputs_moved` reprocesses it — no version sweep
required. A bump is for when unchanged input starts parsing differently; here the input itself
changes from ``None`` to a real string.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from headstart import company_name, salary
from headstart.models import Job, html_to_text, is_remote, requisition_of
from headstart.scrapers.base import (
    MIN_AUTHORITATIVE_SHARE,
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)

_PORTAL = re.compile(r"portalNo:\s*'?(\d+)")
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_IMG = re.compile(r"<img\b[^>]*>", re.DOTALL | re.IGNORECASE)
_ATTR = re.compile(r"\b(?:alt|title)=[\"']([^\"']+)", re.IGNORECASE)
_JOBS_TABLE = re.compile(
    r'<table[^>]+id="jobs"[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE
)
_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
_DETAIL_LIST = re.compile(
    r"api\.fillList\('requisitionDescriptionInterface', 'descRequisition', \[(.*?)\]\);",
    re.DOTALL,
)
_DETAIL_LABELS = re.compile(r"_hlid:\s*\[(.*?)\],", re.DOTALL)
_JS_STRING = re.compile(r"'((?:\\.|[^'])*)'")
_SECTION = re.compile(r"^/careersection/([^/?#]+)/?")
_DETAIL_WORKERS = (
    16  # clean at 16-way; 32 had one 30-second timeout (2026-09-13 ladder)
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


def _headers(shell: str) -> list[str | None]:
    table = _JOBS_TABLE.search(shell)
    if not table:
        return []
    return [html_to_text(value) for value in _TH.findall(table.group(1))]


def _aligned_headers(headers: list[str | None], values: list[Any]) -> list[str | None]:
    """Data headers only when they exactly align with a positional row."""
    data_headers = [
        header
        for header in headers
        if (header or "").strip().lower() not in {"icons", "actions"}
    ]
    return data_headers if len(data_headers) == len(values) else [None] * len(values)


def _last_title(shell: str) -> str | None:
    """The shell's last ``<title>`` tag, tags stripped — mirrors `company_name.title_of`.

    Enterprise shells serve *two* ``<title>`` tags: a fixed chrome placeholder first ("Job
    Search", literally, on all 150 of 150 sampled Boards) and, only when the tenant has
    themed the Career Section, its real title second. `title_of` reads the first ``<title>``
    it finds, so it can never reach the real one here — this is why `_company` cannot just
    call it directly. See `headstart.company_name`'s module docstring for the full measurement.
    """
    matches = _TITLE_TAG.findall(shell)
    if not matches:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", matches[-1])).strip()
    return text or None


def _company(shell: str, slug: str) -> str | None:
    name = company_name.from_title("taleo_enterprise", _last_title(shell), slug)
    if name:
        return name
    # Enterprise shells commonly serve only the generic placeholder title, or a shape this
    # module's patterns don't cover. Restrict to <img> tags that name themselves as a logo
    # (D.R. Horton, TTEC, Valero all do) rather than scanning every image on the page —
    # unscoped, chrome icons like the RSS/help/social buttons (none of which mention "logo")
    # get mistaken for the company name on shells with no real logo image at all (e.g.
    # easyjet, hyundaicapital both returned "Create an RSS feed" and the bare placeholder
    # title before this fix).
    #
    # What is left once " logo" comes off must still read as a name: `aa010`'s and
    # `elsewedyelectric`'s images are alt="logo" and `hdr`'s alt="hdr logo", and both served
    # "logo" and "hdr" as the company until this refused them (2026-09-24).
    ignored = {"access the online help", "close", "collapse this section", "image"}
    for image in _IMG.findall(shell):
        if "logo" not in image.lower():
            continue
        for value in _ATTR.findall(image):
            value = html_to_text(value)
            if value and value.lower() not in ignored and len(value) <= 100:
                name = re.sub(r"(?:^|\s+)logo$", "", value, flags=re.IGNORECASE).strip()
                if name and not company_name.looks_like_slug(name):
                    return name
    return None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y", "%Y-%m-%d"):
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
    headers: list[str | None], values: list[Any], words: tuple[str, ...]
) -> str | None:
    for index, header in enumerate(headers):
        if (
            header
            and index < len(values)
            and any(word in header.lower() for word in words)
        ):
            value = values[index]
            return value.strip() if isinstance(value, str) and value.strip() else None
    return None


def _detail_text(value: str) -> str | None:
    return html_to_text(unquote(value).replace(r"\:", ":").removeprefix("!*!") or None)


def _salary_field(
    payvalue: str | None,
    maximumsalary: str | None,
    currency: str | None,
    frequency: str | None,
) -> str | None:
    """``Job.salary`` from the detail page's ``reqlistitem.payvalue``/``maximumsalary``/
    ``currency``/``payfrequencybasis`` labels — real on a minority of tenants; most state none
    of them. Live samples, 2026-09-15: careerglobalhc job 121008 states payvalue="45,000.00" +
    maximumsalary="65,000.00" with no currency/frequency; tas-tgh job 684681 states only
    payvalue="19.00" (an hourly rate, but nothing in the data says so — see below); hyatt job
    3128720 states currency="Australian Dollar (AUD)" + payfrequencybasis="Yearly" with no
    payvalue/maximumsalary at all (a Sydney posting that discloses the currency and cadence but
    not a figure).

    ``currency`` is a full name with its ISO code already in parentheses ("Australian Dollar
    (AUD)", "US Dollar (USD)", "Indian Rupee (INR)") — passed through as-is rather than mapped,
    since `salary.py`'s `_CURRENCY_CODE` finds the parenthesized code inline regardless of the
    surrounding words.

    ``maximumsalary`` with no ``payvalue`` is refused rather than reported as a lone figure — the
    same ceiling-vs-floor risk iCIMS's own JSON-LD parser refuses for the identical reason
    (CLAUDE.md): `salary.py`'s `_field_generic` (taleo_enterprise has no dedicated Tier-1 parser)
    has no way to tell a bare number is a stated ceiling rather the whole truth, and misreading
    one as a floor overstates every job below it. A `payvalue` with no `maximumsalary` (tas-tgh's
    hourly case above) is reported as a single figure instead — `_field_generic` already reads a
    lone number as a floor with no ceiling, the correct shape for "at least this much" data,
    which is what a floor-only figure is. That tas-tgh figure still won't reach a Job: with no
    ``payfrequencybasis`` stated, `_field_generic` defaults to annual, and $19/year fails the
    plausibility floor — declined rather than guessed at hourly, since nothing in the data says
    it is."""
    if not payvalue:
        return None
    return salary.to_field(payvalue, maximumsalary or None, currency, frequency)


def _parse_detail_page(page: str) -> dict[str, str | None] | None:
    """``reqlistitem.jobtype`` (tenant-optional — live-confirmed populated on Burns & McDonnell as
    "New Grad", absent on Hyatt) feeds `Job.experience`, the same raw-label slot recruitee's
    "entry_level" already uses: not itself a number `experience.from_field` can parse, but text
    `from_seniority` reads via its "grad" pattern once `from_field`/`from_description` fall
    through. Reading this does NOT need a `doc_prep.DERIVATIONS_VERSION` bump: like the salary
    field below, it changes the raw `Job.experience` text itself, from ``None`` to a real string,
    so `update_meta.refresh_row`'s `inputs_moved` check already reaches an already-scraped Job for
    free on its next rescrape."""
    match, labels_match = _DETAIL_LIST.search(page), _DETAIL_LABELS.search(page)
    if not match or not labels_match:
        return None
    labels = _JS_STRING.findall(labels_match.group(1))
    values = [_detail_text(value) for value in _JS_STRING.findall(match.group(1))]
    if len(labels) != len(values):
        return None
    fields: dict[str, list[str]] = {}
    for label, value in zip(labels, values):
        if value:
            fields.setdefault(label, []).append(value)

    def joined(*names: str, separator: str = " ") -> str | None:
        field_values = [value for name in names for value in fields.get(name, [])]
        return separator.join(dict.fromkeys(field_values)) or None

    return {
        "description": joined("reqlistitem.description", "reqlistitem.qualification"),
        "department": joined("reqlistitem.jobfield"),
        "experience": joined("reqlistitem.jobtype"),
        "location": joined(
            "reqlistitem.primarylocation", "reqlistitem.otherlocations", separator="; "
        ),
        "employment_type": joined("reqlistitem.jobschedule"),
        "posted_at": _date(joined("reqlistitem.postingdate")),
        "salary": _salary_field(
            joined("reqlistitem.payvalue"),
            joined("reqlistitem.maximumsalary"),
            joined("reqlistitem.currency"),
            joined("reqlistitem.payfrequencybasis"),
        ),
    }


class TaleoEnterpriseScraper(BaseScraper):
    """Public Oracle Taleo Enterprise Career Section scraper."""

    ats = "taleo_enterprise"
    # Enterprise Career Sections use the measured `jobdetail.ftl?lang=en&job={id}` detail route.
    url_shape = r"https://[^/]+\.taleo\.net/careersection/[^/]+/jobdetail\.ftl\?lang=[^&]+&job=[^&]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True
    #: The thread path, measured faster (ADR-0167). Interleaved A/B of the Detail pass at width 16,
    #: 2026-09-24, two rounds on each of four Boards (items/s, multiplexed vs threads): aarcorp/1
    #: 17.2 vs 27.9 and 19.5 vs 30.3; baesystems/1 36.9 vs 57.6 and 5.6 vs 58.9; cfopitt
    #: pitt_staff_external 21.2 vs 28.7 and 8.0 vs 5.5; ccsd/3 (7 items) 3.4 vs 3.3 and 0.2 vs
    #: 4.7. Threads won 6 of 8 pairs; zero non-200s on either transport. Three pairs caught the
    #: ~30 s single-request tail the 2026-09-13 width ladder found (twice on the multiplexed
    #: path, once on threads); of the five it missed, threads won four and one was a tie.
    async_fanout = False

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return _canonical(url)

    def board_key(self) -> str:
        return f"{self.ats}:{_canonical(self.slug)}"

    def url(self) -> str:
        return f"{_canonical(self.slug)}/jobsearch.ftl?lang=en"

    def job_url(self, job_id: str) -> str:
        return f"{_canonical(self.slug)}/jobdetail.ftl?" + urlencode(
            {"lang": "en", "job": job_id}
        )

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        """The final Career Section URL, in the same identity space as this ledger. A landing
        off Taleo raises, which the base :meth:`alias_key` reads as no verdict."""
        return _canonical(landing_url)

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "tz": "GMT+00:00",
            "Referer": self.url(),
        }

    def _listing(self, shell: str, timeout: int = 30) -> list[dict[str, Any]]:
        portal = _PORTAL.search(shell)
        if not portal:
            raise ValueError("Career Section shell has no portalNo")
        headers = _headers(shell)
        board = _canonical(self.slug)
        parsed = urlsplit(board)
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
        page_no = 1
        while True:
            response = self._fetch(
                "POST",
                api,
                json={**payload, "pageNo": page_no},
                headers=self._request_headers(),
                timeout=timeout,
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
                row_headers = _aligned_headers(headers, values)
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
                        # The requisition number the recruiter sees: `parse` stores it, and
                        # `eightfold_backing_boards.py` reads it (ADR-0205, ADR-0210).
                        "contest_no": record.get("contestNo"),
                        "title": title,
                        "location": locations,
                        "department": _column(
                            row_headers,
                            values,
                            ("department", "job field", "job category", "function"),
                        ),
                        "employment_type": _column(
                            row_headers,
                            values,
                            (
                                "employment type",
                                "job schedule",
                                "appointment type",
                                "position type",
                            ),
                        ),
                        "posted_at": _date(
                            _column(row_headers, values, ("posting date",))
                        ),
                        "url": self.job_url(job_id),
                    }
                )
            if pages is not None and page_no >= pages:
                break
            page_no += 1
        if total is not None:
            # Complete D.R. Horton and TTEC walks disagree with their totals (592/594 and
            # 110/115) while every declared page arrived and IDs did not repeat. The total is a
            # page-count upper bound, not authoritative evidence of missing requisitions.
            self.telemetry["stated_total"] = total
            self.telemetry["unique_jobs"] = len(seen)
            if len(seen) < total * MIN_AUTHORITATIVE_SHARE:
                # Logged rather than truncated, as oracle does with its inflated counter: the
                # total is only an upper bound (TTEC's complete walk lands here), but a gap this
                # wide is worth watching.
                self._log.info(
                    f"{self.board_key()}: read {len(seen)} of a stated {total} requisitions"
                )
        return listed

    def detail_request(self, item: dict[str, Any]) -> DetailRequest:
        return DetailRequest(item["url"])

    def read_detail(
        self, item: dict[str, Any], response: Any
    ) -> dict[str, str | None] | DetailWithoutDescription:
        detail = _parse_detail_page(response.text)
        if detail is None:
            raise DetailLost("no labelled requisition fields on a 200")
        if not detail.get("description"):
            # Kept, not lost: its location, department and salary are real and `parse` prefers
            # them to the listing's; counted as a gap for the missing description.
            return DetailWithoutDescription(detail, "no description on the requisition")
        return detail

    def fetch_raw(self) -> Any:
        shell = self._get()
        self.company = _company(shell, self.slug) or self.company
        listed = self._listing(shell)
        # No tech gate: measured to lose tech postings here (ADR-0166, #510). No held-description
        # skip either: the detail page also supplies the fields `parse` prefers to the listing's.
        details = self.run_detail_pass(
            listed, key_of=lambda item: item["id"], what="detail pages"
        )
        return [(item, details.get(item["id"])) for item in listed]

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item, detail in raw:
            detail = detail or {}
            location = detail.get("location") or item["location"]
            jobs.append(
                Job(
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=item["title"] or "",
                    location=location,
                    remote=is_remote(location),
                    department=detail.get("department") or item["department"],
                    url=item["url"],
                    posted_at=detail.get("posted_at") or item["posted_at"],
                    scraped_at=scraped_at,
                    description=detail.get("description"),
                    employment_type=detail.get("employment_type")
                    or item["employment_type"],
                    salary=detail.get("salary"),
                    experience=detail.get("experience"),
                    # What an Eightfold site in front of this section states as `atsJobId`
                    # (ADR-0210).
                    requisition=requisition_of(item.get("contest_no")),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """Never actually called: the real computation runs inside module-level
        `_parse_detail_page()` (no `self` available there), and `parse()` reads its
        already-computed result off `detail.get("salary")`. This method exists only to satisfy
        `BaseScraper`'s abstract-method contract — the same structural exception icims.py's own
        `_salary_field` delegate documents."""
        return None
