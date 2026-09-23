"""ClearCompany scraper — reads the tenant's public HRM Direct Board (``{slug}.hrmdirect.com``).

ClearCompany's public Board is not on ``clearcompany.com``. ``{slug}.clearcompany.com/careers``
is a login SPA (1,989 bytes, byte-identical for a real tenant and an invented one), and a
ClearCompany job link resolves through ``/api/v1/careers/jobs/{guid}/posting-url`` to
``{slug}.hrmdirect.com/employment/job-opening.php?req=N`` — HRM Direct, the career site ClearCompany
owns. Measured 2026-09-23 over 244 tenant labels (176 hiring Boards, 6,377 postings) and written up
in ``docs/clearcompany/2026-09-23_careers-surfaces-measurement.md``; decided in ADR-0182.

**The slug is the subdomain label**, lowercased, and one label keys both hosts: 158 of 162 labels
seen only on ``hrmdirect.com`` answered ClearCompany's own API, and 53 of 60 seen only on
``clearcompany.com`` had a hiring HRM Direct Board (:meth:`slug_from`).

**The listing is ``/employment/xml.php``**, one response with no pagination, whose req count
equals the rendered Board's on every Board the rendered page could be counted on (155 of 155). It
states title, department, office, city/state/country, date, company and the req. Three traps:

- **One row per req per location** (434 of 6,377 reqs span 2-30 rows; only the place differs).
  Rows are grouped by req and every place is "; "-joined, so the location filter matches each.
- **``<date>`` is US-Eastern midnight labelled ``+0100``** on every row (7,770 read 04:00, 561
  read 05:00). The calendar date is real — stable across two passes (7,523 of 7,523) and equal to
  ClearCompany's ``OpenDate`` — so ``posted_at`` is the date alone.
- **Encodings are mixed within one document**, whatever its UTF-8 declaration says: 443 of 510
  detail pages fail UTF-8, and feeds carry cp1252 bytes (`0x92`), UTF-8, and cp1252
  double-encoded through Latin-1 (`C2 96`) side by side. :func:`decode_hrm_bytes` reads each
  byte UTF-8 rejects as cp1252 and maps leftover C1 controls through cp1252. A whole-document
  fallback left 174 of 16,676 feed titles/departments as mojibake on 46 of 174 Boards.

ClearCompany's own JSON feed (``careers-page.clearcompany.com/api/v1/careers/jobs``) lost: it was
short of the Board on 97 of 161 Boards (3,597 vs 4,138 postings), served postings for 14 tenants
whose Board is gone (newest 2011-2025), answered ``[]`` for 11 hiring Boards, and every
ClearCompany host's ``robots.txt`` is ``Disallow: /``. HRM Direct publishes no ``robots.txt``.

**The description is detail-only.** xml.php's ``descriptionrich`` stops at exactly 1,000 chars
(6,601 of 8,335 rows; none longer), so each posting's ``job-opening.php?req=N`` page supplies the
body (the ``jobDesc`` block: 509 of 510 pages, p50 5,269 chars) and the tenant's ``Salary:`` row
(15 of 510). The tech gate (ADR-0166) is **exact**: ``title`` and ``department`` come from xml.php
and the detail overrides neither. ADR-0048's skip of the already-described is **not** taken: the
detail is the only source of ``salary``, and skipping it would blank that field on later runs.
A closed or unknown req is a 200 with no posting on it — counted as a ``no posting`` detail gap.

**Dead versus empty is xml.php's status**: 404 for an unknown or departed tenant (51 of 51 had a
404 Board page too), 200 with no ``<job>`` for a live Board with nothing open (16). A 404 raises
here, so it reads as a failed Board rather than an empty one. The one Board too large to read,
``heartlandbehavior``, answers 500 after ~104 s server-side; its ledger rows are UNKNOWN, so it is
never scheduled. Its 16 ``state=`` slices read whole (4,558 reqs) and hold 0 tech postings, so no
split walker exists for it (ADR-0182).

Not mapped: ``experience`` and ``employment_type`` (no native field; tenant-custom labels appear on
at most 3 of 510 pages); ``remote`` has no field either (a ``Workplace Type:`` label on 1 tenant
of 176) and is read from the location and office text, where Fisher Phillips files remote reqs
under an office named "Remote". No rate limit was found — 90.5 req/s at conc 32 on one tenant's
detail pages, all 200 — and the host is User-Agent-agnostic.
"""

from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime
from typing import Any

from headstart import http
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

#: The largest Board that answers, `oakmontmanagement` (695 reqs, 3.68 MB), took 20.7 s — too
#: close to the 30 s every other request here gets. Past ~104 s the server gives up with a 500.
_FEED_TIMEOUT = 90
#: A detail page is p50 45 KB at p50 0.9 s (max 73 KB); the base scraper's usual 30 s.
_DETAIL_TIMEOUT = 30
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/xml, text/html"}
_JOB = re.compile(r"<job>(.*?)</job>", re.DOTALL)
_JOB_DESC = '<div class="jobDesc">'
_DIV = re.compile(r"<div\b|</div\s*>", re.IGNORECASE)
_FIELD = re.compile(
    r'class="viewFieldName"\s*>\s*<b>(.*?)</b>.*?class="viewFieldValue"\s*>(.*?)</td>',
    re.DOTALL,
)


#: A byte UTF-8 rejected, as `surrogateescape` hands it back.
_ESCAPED = re.compile("[\udc80-\udcff]")
#: C1 controls: a cp1252 byte stored as Latin-1 and re-encoded to UTF-8 (`C2 96` for an en dash).
_C1 = re.compile("[\x80-\x9f]")


def decode_hrm_bytes(body: bytes) -> str:
    """HRM Direct's bytes as text. They mix encodings within one document, whatever the XML
    declaration says: cp1252 bytes (`0x92`), UTF-8, and cp1252 double-encoded through Latin-1
    (`C2 96`). So UTF-8 is read wherever it is valid, each byte it rejects is read as cp1252, and
    a C1 control left behind is mapped through cp1252 too. Over 174 hiring feeds this left 0 of
    16,676 titles and departments broken, against 174 with mojibake from a whole-document
    UTF-8-else-cp1252 decode and 116 with C1 controls from the per-byte fallback alone."""
    # `surrogateescape` is a built-in handler: each byte UTF-8 rejects comes back as a lone
    # surrogate (U+DC80-U+DCFF), which is then read as the cp1252 byte it is. Nothing is
    # registered, so importing this module leaves the process's codec state alone.
    text = _ESCAPED.sub(
        lambda m: bytes([ord(m.group()) - 0xDC00]).decode("cp1252", "replace"),
        body.decode("utf-8", "surrogateescape"),
    )
    return _C1.sub(lambda m: bytes([ord(m.group())]).decode("cp1252", "replace"), text)


def _tag(row: str, tag: str) -> str | None:
    m = re.search(
        rf"<{tag}>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</{tag}>", row, re.DOTALL
    )
    if not m:
        return None
    value = html.unescape(m.group(1) if m.group(1) is not None else m.group(2)).strip()
    return value or None


def _place(row: str) -> str | None:
    parts = [
        p for p in (_tag(row, "city"), _tag(row, "state"), _tag(row, "country")) if p
    ]
    return ", ".join(parts) or None


def _date(value: str | None) -> str | None:
    """The calendar date of an RFC-822 `<date>`; the clock and its `+0100` are mislabelled."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def feed_reqs(xml: str) -> list[tuple[str, list[str]]]:
    """xml.php's rows grouped by req, in first-seen order — one row per req per location."""
    grouped: dict[str, list[str]] = {}
    for row in _JOB.findall(xml):
        req = _tag(row, "referencenumber")
        if req:
            grouped.setdefault(req, []).append(row)
    return list(grouped.items())


def _location(rows: list[str]) -> str | None:
    places: list[str] = []
    for row in rows:
        place = _place(row)
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


def _job_desc(page: str) -> str | None:
    """The `jobDesc` block's inner HTML, closed at its own matching `</div>` (the body can nest
    divs of its own). None on a page without one — a closed or unknown req."""
    start = page.find(_JOB_DESC)
    if start < 0:
        return None
    start += len(_JOB_DESC)
    depth = 1
    for m in _DIV.finditer(page, start):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return page[start : m.start()]
    return page[start:]


def _field(page: str, label: str) -> str | None:
    """The value of the detail page's `viewFields` row labelled `label` (case and a trailing
    colon ignored on both sides); tenants configure these rows themselves."""
    label = label.rstrip(" :").lower()
    for name, value in _FIELD.findall(page):
        if html_to_text(name).rstrip(" :").lower() == label:
            return html_to_text(value)
    return None


class ClearCompanyScraper(BaseScraper):
    ats = "clearcompany"
    url_shape = (
        r"https://[a-z0-9-]+\.hrmdirect\.com/employment/job-opening\.php\?req=\d+"
    )
    #: Detail pages peaked at 90.5 req/s at conc 32 on one tenant (676 requests, all 200); half.
    detail_workers = 16
    has_detail_pass = (
        True  # the per-req page fills `description` and `salary` (ADR-0050)
    )

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The lowercased first label: discovery emits `{slug}`, `{slug}.hrmdirect.com` and
        `{slug}.clearcompany.com`, all one tenant, and DNS ignores case."""
        return (tenant or host_of(url)).strip().lower().split(".")[0]

    def url(self) -> str:
        return f"https://{self.slug}.hrmdirect.com/employment/xml.php"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}.hrmdirect.com/employment/job-opening.php?req={native_id}"

    def _page(self, url: str, timeout: int) -> str:
        response = self._fetch("GET", url, headers=_HEADERS, timeout=timeout)
        response.raise_for_status()
        return decode_hrm_bytes(response.content)

    async def _page_async(self, session: Any, url: str, timeout: int) -> str:
        response = await self._fetch_async(
            session, "GET", url, headers=_HEADERS, timeout=timeout
        )
        response.raise_for_status()
        return decode_hrm_bytes(response.content)

    def fetch_raw(self) -> Any:
        xml = self._page(self.url(), timeout=_FEED_TIMEOUT)
        if "<source>" not in xml:
            self.note_unreadable_board(
                "an HRM Direct <source> feed", f"{len(xml)} chars without one"
            )
            return {"xml": "", "details": {}}
        reqs = feed_reqs(xml)
        wanted = self.tech_detail_wanted(
            reqs,
            lambda r: _tag(r[1][0], "title"),
            lambda r: _tag(r[1][0], "department"),
        )
        ids = [req for req, _rows in wanted]
        details: dict[str, str] = {}
        if ids:
            if self.async_fanout_enabled():
                fetched = self.fan_out_async(ids, self._detail_async)
            else:
                fetched = self.fan_out(ids, self._detail, workers=self.detail_workers)
            self.report_detail_gaps(fetched, "detail pages")
            details = {i: page for i, page in zip(ids, fetched) if page}
        return {"xml": xml, "details": details}

    def _posting(self, page: str) -> str | None:
        """The page if a posting is on it; a closed or unknown req is a 200 with none."""
        if _JOB_DESC in page:
            return page
        self.note_detail_loss("no posting")
        return None

    def _detail(self, req: str) -> str | None:
        try:
            return self._posting(self._page(self.job_url(req), _DETAIL_TIMEOUT))
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
            return None

    async def _detail_async(self, session: Any, req: str) -> str | None:
        try:
            return self._posting(
                await self._page_async(session, self.job_url(req), _DETAIL_TIMEOUT)
            )
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
            return None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for req, rows in feed_reqs(raw.get("xml") or ""):
            row = rows[0]
            location = _location(rows)
            office = _tag(row, "office")
            page = details.get(req) or ""
            jobs.append(
                Job(
                    id=self.job_id(req),
                    ats=self.ats,
                    company=_tag(row, "company") or self.company,
                    title=_tag(row, "title") or "",
                    location=location,
                    remote=is_remote("; ".join(p for p in (location, office) if p)),
                    department=_tag(row, "department"),
                    url=self.job_url(req),
                    posted_at=_date(_tag(row, "date")),
                    scraped_at=scraped_at,
                    description=html_to_text(_job_desc(page)),
                    salary=self._salary_field(page),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """The detail page's `Salary:` row, as the tenant wrote it."""
        return _field(raw, "salary") if raw else None
