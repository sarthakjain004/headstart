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
layout, read from its ``col-md-8`` column since 2026-09-25 (:func:`_description_html`). JSON-LD
turns up on some pages of both layouts (INVXIS carries it) and not on others, so it is a path
neither layout can rely on.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from headstart import company_name
from headstart.fetcher import Fetcher
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
    _head_of,
)

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
#: The listing's link to its next ten rows. Public: the liveness probe walks the same pages to
#: count a Board (ADR-0203).
NEXT_PAGE_LINK = re.compile(
    r'<a\s+href="(?P<href>[^"]+)"\s+class="jscroll-next"', re.IGNORECASE
)
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
#: The 14th tenant (Caidya) is the second layout, carrying no anchor; :func:`_description_html`
#: reads it from its ``col-md-8`` column since 2026-09-25.
_DETAIL_OPEN = re.compile(r'<div[^>]*\bname="cwsJobDescription"[^>]*>', re.IGNORECASE)
_DIV_TAG = re.compile(r"<(?P<close>/?)div\b[^>]*>", re.IGNORECASE)
#: The second layout's header column; its presence is what makes the ``col-md-8`` below a job's
#: body rather than any Bootstrap page's main column.
_SECOND_LAYOUT_HEADER = re.compile(
    r'<div[^>]*class="well oracletaleocwsv2-job-description"[^>]*>', re.IGNORECASE
)
_SECOND_LAYOUT_BODY = re.compile(
    r'<div[^>]*class="col-xs-12 col-sm-12 col-md-8"[^>]*>', re.IGNORECASE
)
_SECOND_LAYOUT_BUTTONS = re.compile(
    r'<div[^>]*class="oracletaleocwsv2-button-navigation[^"]*"[^>]*>', re.IGNORECASE
)
_STYLE_OR_SCRIPT = re.compile(
    r"<(style|script)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
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
    """The raw HTML of the job's description, from whichever of the two live layouts the page is.

    The first carries a ``name="cwsJobDescription"`` container. The second (INVXIS, ORBIS,
    COVESTIC2 and 13-17 Boards a run, measured 2026-09-25) carries none: a ``well`` header column
    sits beside a ``col-md-8`` main column holding the description, its own Back / Share / Apply
    buttons and, on some tenants, an inline ``<style>``. Both are cut by depth-counting
    ``<div>``/``</div>``, so the body ends at its container's *own* closing tag. None when neither
    container is on the page — the caller records that as a cause, not an empty description.
    """
    # The first layout is tried first and returns even when unbalanced: its pages carry the
    # second layout's markers too (STG_CITCO's anchor sits inside the same ``col-md-8``).
    opening = _DETAIL_OPEN.search(page)
    if opening:
        close = _div_close(page, opening.end())
        return page[opening.end() : close.start()] if close else None
    header = _SECOND_LAYOUT_HEADER.search(page)
    column = header and _SECOND_LAYOUT_BODY.search(page, header.end())
    if not column:
        return None
    close = _div_close(page, column.end())
    if close is None:
        return None
    body = _STYLE_OR_SCRIPT.sub("", page[column.end() : close.start()])
    buttons = _SECOND_LAYOUT_BUTTONS.search(body)
    bar_close = buttons and _div_close(body, buttons.end())
    if bar_close:
        body = body[: buttons.start()] + body[bar_close.end() :]
    return body


def _div_close(page: str, start: int) -> re.Match[str] | None:
    """The ``</div>`` closing the ``<div>`` whose opening tag ends at ``start``, by depth count."""
    depth = 1
    for tag in _DIV_TAG.finditer(page, start):
        depth += -1 if tag.group("close") else 1
        if depth == 0:
            return tag
    # Unbalanced markup: refuse rather than pollute. Running to the end of the page would put the
    # site footer and navigation into the job's description, and a description that is wrong is
    # worse for the embedding than one that is absent — the caller records a cause either way.
    return None


#: A ledger row with no name carries the Board key instead, ``ORG:CWS@host/path`` — the form
#: this ledger writes new rows in — and `looks_like_slug` does not read that shape as one. Not
#: `company_name.is_identifier`: it also counts a curated name that equals the org code (ICANN,
#: SEPAQ) as an identifier, and those Boards are named already.
_BOARD_KEY_NAME = re.compile(r"^[A-Za-z0-9]+:\d+@\S+$")
#: How much of the RSS feed is read for its channel ``<title>``, which comes first. The feed
#: carries every posting's description after it, so it is cut here rather than read whole.
_RSS_HEAD_BYTES = 8 * 1024
#: ``(host, org)`` -> the name that org's feed states, once per process: a tenant's career sites
#: all state their org's name (GATEWAYVENT's five, 2026-09-24), so its other Boards need no
#: request. Only a name is kept; a feed that yields none is asked again by the next Board.
_ORG_NAMES: dict[tuple[str, str], str] = {}


def _rss_url(slug: str) -> str:
    """The Board's RSS servlet, ``https://{host}/{inst}/ats/servlet/Rss?org=..&cws=..``."""
    parts = urlsplit(slug)
    inst = parts.path.strip("/").split("/")[0]
    query = parse_qs(parts.query)
    return (
        f"https://{parts.netloc}/{inst}/ats/servlet/Rss?"
        f"{urlencode({key: query[key][0] for key in ('org', 'cws') if query.get(key)})}"
    )


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
            # custom-field cells render "Employment Type: " — the colon is not the name
            labels[label.lower().rstrip(":").strip()] = value
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
    #: The thread path, measured faster (ADR-0167). Interleaved A/B of the Detail pass at width 16,
    #: 2026-09-24, two rounds on each of three Boards (items/s, multiplexed vs threads): TMCAZ:38
    #: 24.7 vs 36.6 and 25.2 vs 32.9; SAINTELIZABETH:45 33.3 vs 43.9 and 32.7 vs 44.3; YKHC:41
    #: 22.0 vs 18.9 and 23.9 vs 29.1. Threads won 5 of 6 pairs; zero non-200s on either transport.
    async_fanout = False

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        # The liveness ledger may disambiguate two same-named career sites with a stable Taleo
        # suffix.  It is a Board identity, not the company name shown to job seekers.
        self.company = re.sub(r" \[Taleo [^]]+\]$", "", self.company)

    def resolve_company(self) -> None:
        """Name a Board whose ledger row carries no name from its RSS feed's channel title,
        "{Name} Job Feed" or a localized form of it (`company_name`'s taleo_be patterns).

        Not the career site's page title, which is the tenant's own heading and names no one on
        most Boards. The feed is read only as far as its title, in one attempt that can never
        wall the ATS, and every failure leaves ``self.company`` as it was. A posting's own
        "Company:" field (:meth:`parse`) still outranks the Board's name.
        """
        if not (
            _BOARD_KEY_NAME.match(self.company)
            or company_name.looks_like_slug(self.company)
        ):
            return
        parts = urlsplit(self.slug)
        key = (parts.netloc, (parse_qs(parts.query).get("org") or [""])[0])
        if key in _ORG_NAMES:
            self.company = _ORG_NAMES[key]
            return
        try:
            response = self._fetch_once(
                "GET", _rss_url(self.slug), accept="application/rss+xml", stream=True
            )
            head = _head_of(response, _RSS_HEAD_BYTES)
        except Exception as exc:  # noqa: BLE001 - a display name is never worth failing a Board for
            self._log.info(
                f"{self.board_key()}: no company name — {_rss_url(self.slug)} raised "
                f"{type(exc).__name__}"
            )
            return
        name = company_name.from_title(self.ats, company_name.title_of(head), self.slug)
        if name:
            _ORG_NAMES[key] = name
            self.company = name

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

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        """The final canonical TBE board URL, when this Board redirects to one.

        A host alone cannot be an alias key here: every customer shares Taleo's regional hosts.
        The full final URL is in the same slug space as the liveness ledger, which lets the
        generic alias resolver bury only a Board whose redirect target is itself live.
        """
        return _canonical(landing_url)

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
            if not listed and "oracletaleocwsv2-accordion-group" not in page:
                # An empty Board still renders the (blockless) accordion group — 3 of 3 live-0
                # ledger Boards, and both Boards with postings, 2026-09-25 — so a first page
                # without it is one this cannot read.
                self.note_unreadable_board(
                    "an oracletaleocwsv2 accordion group",
                    f"{len(page)} bytes without one",
                )
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
            next_match = NEXT_PAGE_LINK.search(page)
            if not next_match:
                return listed
            page_url = urljoin(page_url, html.unescape(next_match.group("href")))
        else:
            self.mark_truncated(f"hit the {_MAX_PAGES}-page cap at {len(listed)} jobs")
        return listed

    def fetch_raw(self) -> Any:
        listed = self._listing()
        # No tech gate: measured to lose tech postings here (ADR-0166, #510). No held-description
        # skip either: the detail page also supplies the labels `parse` reads.
        details = self.run_detail_pass(
            listed, key_of=lambda item: item["id"], what="detail pages"
        )
        return [(item, details.get(item["id"])) for item in listed]

    def detail_request(self, item: dict[str, str | None]) -> DetailRequest:
        if not item["url"]:
            raise DetailLost("no detail URL")
        return DetailRequest(item["url"])

    def read_detail(
        self, item: dict[str, str | None], response: Any
    ) -> dict[str, str | None] | DetailWithoutDescription:
        page = response.text
        labels = _labels(page)
        body = _description_html(page)
        date = _DATE_POSTED.search(page)
        detail = {
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
            # "Workplace Arrangement:" (its trailing colon dropped by `_labels`), Covestic
            # states "Location Type".
            "workplace_arrangement": _field(
                labels,
                "Workplace Arrangement",
                "Location Type",
            ),
        }
        if not detail["description"]:
            # Kept for its labels, counted as a gap: YKHC's layout carries no body and still
            # states a location and department on 128 of 128 pages (measured 2026-09-24).
            return DetailWithoutDescription(
                detail, "200 without a parseable description body"
            )
        return detail

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
        # ICANN labels its bounds "Targeted Base Salary Low/High"; ASPENGOV "Pay Range (Min/Max)".
        low = next(
            (
                value
                for name, value in raw.items()
                if ("salary" in name or "pay range" in name)
                and re.search(r"\b(low|min|minimum)\b", name)
            ),
            None,
        )
        high = next(
            (
                value
                for name, value in raw.items()
                if ("salary" in name or "pay range" in name)
                and re.search(r"\b(high|max|maximum)\b", name)
            ),
            None,
        )
        if low and high:
            # ICANN writes each bound as "$40,000 + 10% Bonus + Benefits"; joined whole, the
            # tail between the figures leaves no range and extract keeps only the floor.
            return f"{low.split('+')[0].strip()} - {high.split('+')[0].strip()}"
        for name, value in raw.items():
            if any(
                word in name for word in ("salary", "pay range", "compensation")
            ) and not any(
                word in name
                for word in ("low", "high", "minimum", "maximum", "min", "max")
            ):
                return value
        return None
