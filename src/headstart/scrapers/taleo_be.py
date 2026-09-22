"""Oracle Taleo Business Edition (TBE) scraper.

``slug`` is the full search-results URL.  A TBE Board cannot be addressed by
its ``org`` code alone: the shard, instance and career-site id are all part of
the address.  Listings are ten rows at a time and their relative ``next`` link
needs the ``JSESSIONID`` set by the first request, so the walk is serial.

The detail HTML — the tenant's own label/value spans and the ``cwsJobDescription`` anchor,
rather than the ``JobPosting`` JSON-LD several third-party clients assume — supplies the job
description and labelled metadata. That JSON-LD is not universally absent: measured live
2026-09-22 on 3 boards, NBF1199 rid=11231 carries it (description byte-identical to this
scraper's own ``cwsJobDescription`` read, so it rescues nothing there) while CLINIPACE rid=8094
and YKHC rid=18951 carry neither JSON-LD nor a readable ``cwsJobDescription`` anchor — a second
layout this scraper does not yet handle. So JSON-LD is a redundant path where this scraper
already works, not a free fix for the layout it doesn't.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_MAX_PAGES = 1_000
_DETAIL_WORKERS = 16
_BLOCK = re.compile(
    r'<div class="oracletaleocwsv2-accordion-block">(.*?)(?=</div>\s*<!--/.accordion-block-->)',
    re.DOTALL,
)
_JOB = re.compile(
    r'<h4[^>]*>\s*<a[^>]*href="(?P<href>[^"]*viewRequisition[^"]*\brid=(?P<id>\d+)[^"]*)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_HEAD_FIELDS = re.compile(
    r"<h4[^>]*>.*?</h4>(?P<fields>.*?)(?=</div>\s*<!--/.accordion-head-info)", re.DOTALL
)
_DIV = re.compile(r"<div[^>]*>(.*?)</div>", re.DOTALL | re.IGNORECASE)
#: The listing's "Sort by" options name the card's columns: ``sortColumn=0`` is the title and
#: ``sortColumn=N`` the card's Nth head-info div. Tenants choose and order their own columns
#: (dates, job codes, statuses, shifts), so fields are read by header, never by position —
#: measured live 2026-09-23, 30 org-deduped hiring tenants: 30/30 carry the select and 244/244
#: cards have exactly one div per non-title option.
_SORT_COLUMN = re.compile(
    r"<option[^>]*\bsortColumn=(?P<n>\d+)[^>]*>(?P<label>.*?)</option>",
    re.DOTALL | re.IGNORECASE,
)
_NEXT = re.compile(r'<a\s+href="(?P<href>[^"]+)"\s+class="jscroll-next"', re.IGNORECASE)
_COMPANY = re.compile(r"Company:\s*(?P<company>[^%<\r\n]+)", re.IGNORECASE)
#: The description container's *opening* tag. Its extent is found by depth-counting
#: :data:`_DIV_TAG` rather than by a forward-looking terminator.
#:
#: What this replaced was ``(?=<section\b)``, which fires only on the minority of pages that
#: happen to carry a ``<section>`` *after* the anchor. Measured live 2026-09-16 over 14 random
#: hiring tenants: the old terminator matched 5 and silently dropped the description on the other
#: 9, whose ``<section>`` tags all precede the anchor. Depth-counting parses 13 of the same 14 and
#: loses none of the 5 the old one handled. That matches the logs, where taleo_be lost 737-795 of
#: every 1,000 detail-Jobs across five runs (23,954 of 31,215).
#:
#: The fetch *succeeds* on these pages, so nothing raised and no cause was ever recorded —
#: taleo_be was the only ATS with a five-figure loss and no ``detail loss causes:`` line at all.
#: The 14th tenant (Caidya) is a second layout carrying no anchor. Its body is still lost — only
#: the *reporting* changes: the caller now records a cause, so it is distinguishable from a posting
#: that genuinely has no description, though not from one whose container we simply could not
#: parse. A selector for that layout is follow-up work.
_DETAIL_OPEN = re.compile(r'<div[^>]*\bname="cwsJobDescription"[^>]*>', re.IGNORECASE)
_DIV_TAG = re.compile(r"<(?P<close>/?)div\b", re.IGNORECASE)
_LABEL = re.compile(
    r"<span[^>]*>\s*(?P<label>[^<]+?)\s*</span>\s*<strong>\s*(?P<value>.*?)\s*</strong>",
    re.DOTALL | re.IGNORECASE,
)
#: The JobPosting JSON-LD's ``datePosted`` — the only date TBE pages state: live 2026-09-22,
#: 0 of 15 tenants render a "Date Posted" label while 9 of 15 carry this key.
_DATE_POSTED = re.compile(r'"datePosted"\s*:\s*"(?P<value>[^"]+)"')
_CUSTOM_LABEL = re.compile(
    r"<div[^>]*cws-V2-reqfieldcell-right[^>]*>\s*(?P<label>.*?)\s*</div>\s*"
    r"<div[^>]*cws-V2-reqfieldcell-left[^>]*>\s*<strong>\s*(?P<value>.*?)\s*</strong>",
    re.DOTALL | re.IGNORECASE,
)


def _text(value: str | None) -> str | None:
    return html_to_text(value) if value else None


def _description_html(page: str) -> str | None:
    """The raw HTML inside the ``name="cwsJobDescription"`` container, or None if absent.

    Depth-counts ``<div>``/``</div>`` from the anchor so the body ends at its *own* closing tag.
    Returns None for the second live layout, which carries no anchor at all (YKHC, INVXIS) —
    the caller records that as a cause rather than reporting an empty description.
    """
    opening = _DETAIL_OPEN.search(page)
    if not opening:
        return None
    start = opening.end()
    depth = 1
    for tag in _DIV_TAG.finditer(page, start):
        depth += -1 if tag.group("close") else 1
        if depth == 0:
            return page[start : tag.start()]
    # Unbalanced markup: refuse rather than pollute. Running to the end of the page would put the
    # site footer and navigation into the job's description, and a description that is wrong is
    # worse for the embedding than one that is absent — the caller records a cause either way.
    return None


def _canonical(url: str) -> str:
    """Keep only the stable coordinates of a TBE Board URL."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    kept = [(key, query[key][0]) for key in ("org", "cws") if query.get(key)]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(kept),
            "",
        )
    )


def _labels(page: str) -> dict[str, str]:
    pairs = list(_LABEL.finditer(page)) + list(_CUSTOM_LABEL.finditer(page))
    labels: dict[str, str] = {}
    for match in pairs:
        label, value = _text(match.group("label")), _text(match.group("value"))
        if label and value:
            labels[label.lower()] = value
    return labels


def _column(
    headers: dict[int, str], fields: list[str | None], *words: str
) -> str | None:
    """The card field under the first header naming any of ``words`` (see :data:`_SORT_COLUMN`).
    Only unambiguous words: a "Division" was a legal entity on one tenant and a department on
    another in the same sample, so it is left for the detail labels to state."""
    for n, header in sorted(headers.items()):
        if n and n <= len(fields) and any(word in header for word in words):
            return fields[n - 1]
    return None


def _field(labels: dict[str, str], *names: str) -> str | None:
    for name in names:
        if value := labels.get(name.lower()):
            return value
    return None


# TBE tenants configure their own field labels, so a workplace-arrangement field (if a tenant
# states one at all) has no single spelling. Measured live 2026-09-15 over 80 distinct tenants
# (~280 detail pages, all 7 shard hosts, org-deduped) sampled from the 533-row live ledger: only
# 2/80 state a discrete field. Two different label spellings, two different value vocabularies —
# "Workplace Arrangement:" (1199SEIU Funds, org=NBF1199: Hybrid/In-Office) and "Location Type"
# (Covestic, org=COVESTIC2: Onsite/Remote). No other spelling appeared. "hybrid" stays None —
# neither purely remote nor onsite — matching workday._remote_from's convention (also
# ashby._remote, eightfold's workLocationOption map).
_WORKPLACE_ARRANGEMENT_PATTERNS = {
    "remote": True,
    "hybrid": None,
    "onsite": False,
    "on-site": False,
    "on site": False,
    "in-office": False,
    "in office": False,
}


def _workplace_remote(value: str | None) -> bool | None:
    if not value:
        return None
    norm = value.strip().lower()
    if norm in _WORKPLACE_ARRANGEMENT_PATTERNS:
        return _WORKPLACE_ARRANGEMENT_PATTERNS[norm]
    if "hybrid" in norm:
        return None
    if "remote" in norm:
        return True
    if "site" in norm or "office" in norm:
        return False
    return None


def _posted_at(value: str | None) -> str | None:
    """Parse the tenant's own "Date Posted" label span. ``%Y-%m-%d %H:%M:%S.%f`` covers the
    trailing ``.0`` seen on the live page — verified 2026-09-22, NBF1199 rid=11231 serves
    ``"2026-08-12 00:00:00.0"`` for this exact field, which the plain-seconds format below
    fails on (a bare ``%Y-%m-%d %H:%M:%S`` has no fractional-second group to consume the
    ``.0``, so ``strptime`` raises rather than truncating)."""
    if not value:
        return None
    for fmt in (
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            pass
    return None


class TaleoBEScraper(BaseScraper):
    """TBE scraper whose slug is a full canonical search-results URL."""

    ats = "taleo_be"
    # Taleo Business Edition emits its own canonical detail URL from every listing card. The
    # board coordinates stay in the query string and `rid` is the native requisition id; both
    # were verified live on ICANN on 2026-09-13. TBE is intentionally separate from Taleo
    # Enterprise's Career Section URL family, which this scraper does not support.
    url_shape = (
        r"https://[a-z0-9-]+\.tbe\.taleo\.net/[a-z0-9-]+/ats/careers/v2/"
        r"viewRequisition\?[^#]*\brid=\d+"
    )
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        # The liveness ledger may disambiguate two same-named career sites with a stable Taleo
        # suffix.  It is a Board identity, not the company name shown to job seekers.
        self.company = re.sub(r" \[Taleo [^]]+\]$", "", self.company)

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return _canonical(url)

    def board_key(self) -> str:
        return f"{self.ats}:{_canonical(self.slug)}"

    def url(self) -> str:
        return _canonical(self.slug)

    def job_url(self, url: str) -> str:
        """Every listing card carries its own canonical detail URL; nothing to build, so this
        simply names that as the declared source (ADR-0153)."""
        return url

    def alias_key(self) -> str | None:
        """The final canonical TBE board URL, when this Board redirects to one.

        A host alone cannot be an alias key here: every customer shares Taleo's regional hosts.
        The full final URL is in the same slug space as the liveness ledger, which lets the
        generic alias resolver bury only a Board whose redirect target is itself live.
        """
        try:
            response = self._fetch(
                "GET",
                self.url(),
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                allow_redirects=True,
                stream=True,
            )
            try:
                return _canonical(response.url)
            finally:
                response.close()
        except Exception:  # noqa: BLE001 - an unreachable surface has earned no alias verdict
            return None

    def _listing(self) -> list[dict[str, str | None]]:
        page_url = self.url()
        seen_pages: set[str] = set()
        seen_jobs: set[str] = set()
        listed: list[dict[str, str | None]] = []
        for _ in range(_MAX_PAGES):
            if page_url in seen_pages:
                self.mark_truncated("listing next link looped before the Board ended")
                break
            seen_pages.add(page_url)
            page = self._get(page_url)
            headers = {
                int(m.group("n")): (_text(m.group("label")) or "").lower()
                for m in _SORT_COLUMN.finditer(page)
            }
            for block in _BLOCK.findall(page):
                match = _JOB.search(block)
                if not match or match.group("id") in seen_jobs:
                    continue
                seen_jobs.add(match.group("id"))
                fields_match = _HEAD_FIELDS.search(block)
                fields = (
                    [
                        _text(value)
                        for value in _DIV.findall(fields_match.group("fields"))
                    ]
                    if fields_match
                    else []
                )
                company_match = _COMPANY.search(block)
                listed.append(
                    {
                        "id": match.group("id"),
                        "url": self.job_url(
                            html.unescape(urljoin(page_url, match.group("href")))
                        ),
                        "title": _text(match.group("title")),
                        "department": _column(
                            headers, fields, "department", "département", "dept"
                        ),
                        "location": _column(
                            headers, fields, "location", "localisation", "lieu"
                        ),
                        "company": _text(company_match.group("company"))
                        if company_match
                        else None,
                    }
                )
            next_match = _NEXT.search(page)
            if not next_match:
                return listed
            page_url = urljoin(page_url, html.unescape(next_match.group("href")))
        else:
            self.mark_truncated(f"hit the {_MAX_PAGES}-page cap at {len(listed)} jobs")
        return listed

    def fetch_raw(self) -> Any:
        listed = self._listing()
        details = self.fan_out(
            listed, lambda item: self._detail(item["url"]), workers=self.detail_workers
        )
        self.report_detail_gaps(
            [
                detail if detail and detail.get("description") else None
                for detail in details
            ],
            "detail pages",
        )
        return list(zip(listed, details))

    def _detail(self, url: str | None) -> dict[str, str | None] | None:
        if not url:
            self.note_detail_unattempted("no detail URL")
            return None
        try:
            page = self._get(url)
        except Exception as exc:  # noqa: BLE001 - isolated detail failure; listing row is useful
            self.note_detail_exception(exc)
            return None
        labels = _labels(page)
        body = _description_html(page)
        if not body:
            # A 200 whose body we cannot read is a loss like any other, and until this existed it
            # was the only kind that recorded nothing: the fetch succeeded, so no exception
            # reached `note_detail_exception` and the cause map stayed empty.
            self.note_detail_loss("200 without a parseable description body")
        date = _DATE_POSTED.search(page)
        return {
            "description": _text(body) if body else None,
            "location": _field(labels, "Primary Location", "Location"),
            "department": _field(labels, "Department"),
            "employment_type": _field(labels, "Employment Type", "Job Type"),
            "posted_at": _posted_at(
                _field(labels, "Date Posted", "Posting Date")
                or (date.group("value") if date else None)
            ),
            "salary": self._salary_field(labels),
            # Both spellings are real, measured live (80-tenant sample): NBF1199 states
            # "Workplace Arrangement:" (with the trailing colon _field matches literally —
            # the colonless spelling was never observed and is deliberately not listed here),
            # Covestic states "Location Type" (no colon).
            "workplace_arrangement": _field(
                labels,
                "Workplace Arrangement:",
                "Location Type",
            ),
        }

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for listed, detail in raw:
            detail = detail or {}
            location = detail.get("location") or listed["location"]
            # The board's own workplace-arrangement field wins when it's decisive; "hybrid" is
            # not decisive (see `_workplace_remote`), so it falls through to the location text
            # like every board that states no such field at all.
            remote = _workplace_remote(detail.get("workplace_arrangement"))
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=self.job_id(listed["id"]),
                    ats=self.ats,
                    company=listed.get("company") or self.company,
                    title=listed["title"] or "",
                    location=location,
                    remote=remote,
                    department=detail.get("department") or listed["department"],
                    url=self.job_url(listed["url"] or ""),
                    posted_at=detail.get("posted_at"),
                    scraped_at=scraped_at,
                    description=detail.get("description"),
                    employment_type=detail.get("employment_type"),
                    salary=detail.get("salary"),
                )
            )
        return jobs

    def _salary_field(self, raw: dict[str, str]) -> str | None:
        low = next(
            (
                value
                for name, value in raw.items()
                if "salary" in name and "low" in name
            ),
            None,
        )
        high = next(
            (
                value
                for name, value in raw.items()
                if "salary" in name and "high" in name
            ),
            None,
        )
        if low and high:
            return f"{low} - {high}"
        for name, value in raw.items():
            if any(
                word in name for word in ("salary", "pay range", "compensation")
            ) and not any(
                word in name
                for word in ("low", "high", "minimum", "maximum", "min", "max")
            ):
                return value
        return None
