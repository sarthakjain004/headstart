"""Radancy (TalentBrew) career fronts: one sitemap listing, then a JSON-LD job page per Job.

**Radancy is not an ATS.** It is a recruitment-marketing vendor, and TalentBrew is its career-site
CMS. A TalentBrew site is a **Career front** (CONTEXT.md): a branded job site that mirrors one or
more Boards on the company's real ATS and hands its Apply button off to that ATS, storing no
applications itself. ``jobs.intuit.com`` mirrors an Avature Tenant that lists nothing publicly;
``careers.amgen.com`` mirrors ``workday:amgen/careers``. In this repo's machinery a front is still
scraped as a Board keyed under the vendor — ``radancy:jobs.intuit.com`` — exactly as Phenom,
another career-front vendor, already is (ADR-0246).

Everything below was measured live on 2026-09-26; the write-up, with every sample size, is
``docs/radancy/2026-09-26_sitemap-and-job-page-measurement.md``.

**The Board is the front's host** (``jobs.intuit.com``), as it is for iCIMS and Phenom. A front can
mirror several TalentBrew company ids (23 of 227 hosts listing jobs: ``careers.munichre.com``
lists 7, ``careers.adeccogroup.com`` 4), and each such front is still one site the applicant sees,
so the host and not the company id is the Board. Job ids are TalentBrew-wide integers, so a job id
alone is the native id.

**The listing is ``/sitemap.xml``, and on some fronts it is capped.** Across 188 live fronts it
listed 175,485 postings against the 205,214 the fronts' own ``/search-jobs`` pages state
(``data-total-job-results``): 160 matched exactly and 169 to within five, and 14 were short by
more — 12 capped at exactly 500 and one at 10,000 (``jobs.walgreens.com`` lists 500 of 22,544).
No paging parameter reaches past the cap (``?page=2``, ``?p=2`` and ``sitemap-2.xml`` all return
the same 500). ``robots.txt`` on 151 of the 188 says ``Disallow: /search-jobs/``, where
TalentBrew's paginated results endpoint lives, so the sitemap is the one listing this scraper
reads, and a capped Board says how short it is (:meth:`RadancyScraper._stated_total`: one GET of
the ``/search-jobs`` page itself, which that rule does not cover). The sitemap redirects to the
site's default language (``/en/sitemap.xml``, ``/fr/sitemap.xml``), and every language's sitemap
lists the same ids (``jobs.veolia.com``: 2,919 in ``/fr/``, 2,919 in ``/en/``). An alias host's
sitemap is its canonical front's, so only job URLs on the Board's own host count
(:func:`sitemap_rows`).

**A job URL is recognised by its shape, not by the word ``/job/``.** The path word is localised —
``/emploi/`` on ``carrieres.walmart.ca``, ``/banen/`` on ``www.werkenbijdji.nl``, CJK on
``jobs.jabil.cn`` — so an English-only pattern read 0 jobs on 17 live fronts. Every job URL is
``[/{lang}]/{word}/{place}/{title}/{companyId}/{jobId}``, and the facet pages beside them in the
sitemap end in a one-digit page number, so :data:`JOB_PATH` keys on a trailing id of six or more
digits (11-12 on every job measured) after a non-numeric title segment.

**Every field is on the job page** — JSON-LD ``JobPosting`` on 177 of 185 fronts sampled (40
pages each), plus the ``gtm_tbcn_jobcategory`` meta tag for the department and the Apply button's
``apply-url`` attribute naming the Backing Board (6,775 of 6,816 pages read). The other 8 fronts'
templates write no JSON-LD, and their pages are read from TalentBrew's own meta tags instead
(:func:`_meta_fields`). The sitemap states nothing but the URL, so a description already held
does not spare the page (``skip_held`` stays off, as on Oracle).

**No pre-detail tech gate.** The only listing signal is the title slug in the URL. On 990 pages,
``is_tech`` over the slug agreed with ``is_tech(title, category)`` except that it dropped 51 of the
337 tech postings (15.1%) — all of them vague titles that ``tech_filter`` promotes on a technical
category ("Senior Architect" under Technology), which the sitemap never states. That is a recall
loss well past Avature's accepted 9.6%, so every page is fetched (CONTEXT.md §Detail pass).

**Front duplication is measured, not gated.** The owner's decision of 2026-09-26: every front is
scraped in full, including postings whose Backing Board a Scrapable Board already serves, and each
run logs the share per front (:meth:`RadancyScraper._report_front_duplication`) so the decision can
be revisited from numbers. The opposite of Phenom's landing rule, on purpose.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from headstart.boards import company_name
from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.jobs.salary import to_field
from headstart.network import http
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
)
from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    has_unparseable_jsonld,
    hiring_organization,
    job_location_text,
    job_posting_fields,
)

#: Well below the measured knee: one front answered 128 concurrent job-page GETs at 91.5 req/s
#: with 500 of 500 200s, and twelve fronts at once did the same (600 of 600), with no refusal at
#: any width tried. `harvest` multiplies this by the Boards it reads at once.
_DETAIL_WORKERS = 16

#: ``[/{lang}]/{word}/{place}/{title}/{companyId}/{jobId}``. The title segment must hold a
#: non-digit, which is what separates a job from a facet page such as
#: ``/employment/{place}-jobs/27595/68354/{geo}/4``; the job id's six-digit floor separates it from
#: a facet's one-digit page number.
JOB_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2,4})?/)?[^/]+/[^/]+/[^/]*[^/0-9][^/]*/\d+/(\d{6,})/?$",
    re.IGNORECASE,
)
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_APPLY_URL = re.compile(r'\bapply-url="([^"]*)"', re.IGNORECASE)
_ATS_DESCRIPTION = re.compile(r'<div[^>]*class="[^"]*\bats-description\b[^"]*"[^>]*>')
_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
_PAGE_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_STATED_TOTAL = re.compile(r'data-total-job-results="(\d+)"')

#: How much of a Board's pages must agree on one name before it names the Board — iCIMS's 0.9.
#: Measured over 17 fronts: the page title's "{Title} at {Company}" agreed on every page of 14,
#: and ``hiringOrganization`` named a division or a legal entity on 4 (Citi's "Early Career"
#: and "Professional", Palo Alto Networks' subsidiaries), so the title is read first. Over 40
#: pages each of 177 fronts, 164 were named this way; the other 13, multi-brand fronts such as
#: ``careers.munichre.com`` and ``jobs.citi.com`` among them, keep their host.
_AGREEMENT = 0.9

#: Above this a figure is annual, at or below it hourly. ``baseSalary`` states ``unitText: ""``
#: on every figure measured: 130 of 6,816 sampled pages state an amount, on 4 of 185 fronts, and
#: the amounts fall in two clusters, hourly up to 117.4 and annual from 51,300.
_HOURLY_CEILING = 1_000


class RadancyScraper(BaseScraper):
    """One Radancy TalentBrew career front, keyed by its host (e.g. ``jobs.intuit.com``)."""

    ats = "radancy"
    # The sitemap's own <loc>: [/{lang}]/{word}/{place}/{title}/{companyId}/{jobId}. Every one of
    # 990 fetched from 17 fronts answered 200 with that posting's JSON-LD.
    url_shape = (
        r"https://[^/]+/(?:[a-z]{2}(?:-[a-z]{2,4})?/)?[^/]+/[^/]+/[^/]+/\d+/\d{6,}"
    )
    has_detail_pass = True  # every field lives on the job page (ADR-0050)
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS
    #: The name this Board's job pages agree on, read by `fetch_raw` (:func:`_agreed_company`).
    _pages_company: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The front's host, whichever of the row's two columns carries it."""
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def fetch_raw(self) -> Any:
        response = self._fetch(
            "GET", self.url(), headers={"User-Agent": USER_AGENT}, timeout=60
        )
        response.raise_for_status()
        # `text/xml` with no charset and a UTF-8 BOM: decoding by header would read the BOM as
        # Latin-1 into the first <loc>'s prefix.
        xml = response.content.decode("utf-8-sig", "replace")
        listed = sitemap_rows(xml, self.slug)
        if not listed:
            # A front with nothing open lands here too (three measured, each stating
            # `data-total-job-results="0"`), so this names what the sitemap held.
            self.note_unreadable_board(
                "TalentBrew job URLs in the sitemap",
                f"{len(_SITEMAP_LOC.findall(xml))} <loc> entries, none of the job shape",
            )
            return []
        stated = self._stated_total()
        if stated is not None and stated > len(listed):
            # A capped sitemap: 20 of 188 fronts listed fewer jobs than they state, 12 of them
            # exactly 500 and one 10,000 (`jobs.walgreens.com`: 500 of 22,544). Nothing else a
            # front's robots.txt allows lists the rest, so the Board is short by a measured
            # amount, and a negligible shortfall (a posting added between the two reads) is
            # left to ADR-0083's grace period (ADR-0121).
            self.mark_truncated_unless_negligible(
                len(listed),
                stated,
                f"the sitemap lists {len(listed)} of the {stated} postings the front states",
            )
        pages = self.run_detail_pass(
            listed, key_of=lambda row: row[0], what="job pages"
        )
        if pages.missing:
            # Every field comes from the job page, so `parse` drops a Job whose page did not
            # arrive; the sitemap states the Board's whole set, so the loss is measured
            # (ADR-0053/0121).
            self.mark_truncated_unless_negligible(
                len(listed) - pages.missing,
                len(listed),
                f"{pages.missing}/{len(listed)} job pages unreadable",
            )
        items = [
            {"id": job_id, "url": url, "fields": pages.get(job_id)}
            for job_id, url in listed
        ]
        self._pages_company = _agreed_company(items)
        self._report_front_duplication(items)
        return items

    def _stated_total(self) -> int | None:
        """The count the front's own ``/search-jobs`` page states, or None where it states none
        on this host. That page is not the ``/search-jobs/`` results endpoint robots.txt
        disallows; a page that lands on another host is another front's filtered view."""
        try:
            response = self._fetch(
                "GET",
                f"https://{self.slug}/search-jobs",
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
        except (
            http.RequestsError
        ):  # the count is a check, never a reason to fail the Board
            return None
        if response.status_code != 200 or host_of(response.url or "") not in (
            "",
            self.slug,
        ):
            return None
        stated = _STATED_TOTAL.search(response.text)
        return int(stated.group(1)) if stated else None

    def _report_front_duplication(self, items: list[dict[str, Any]]) -> None:
        """Log and record the share of this front's postings whose Backing Board is a Scrapable
        Board — **Front duplication** (CONTEXT.md), measured every run and never acted on, by the
        owner's decision of 2026-09-26. One INFO line per Board: a WARNING is an Actions
        annotation against a quota of ten per step (ADR-0039)."""
        held = _scrapable_boards()
        if not held.identities:
            return  # no committed ledger beside this checkout: not measured, never "0%"
        read = [item["fields"] for item in items if item.get("fields")]
        backing = Counter(
            board
            for board in (backing_board(f.get("apply_url"), held) for f in read)
            if board
        )
        duplicated = sum(backing.values())
        self.telemetry["front_postings"] = len(read)
        self.telemetry["front_duplicated"] = duplicated
        boards = ", ".join(f"{board} {n}" for board, n in backing.most_common(3))
        self._log.info(
            f"{self.board_key()}: Front duplication {duplicated}/{len(read)} postings apply "
            f"on a Scrapable Board" + (f" ({boards})" if boards else "")
        )

    def resolve_company(self) -> None:
        """The name the Board's job pages agree on (:func:`_agreed_company`), read during the
        fetch at no extra request; no front-level page names the company in one template (the
        home page titles of 17 fronts were 17 different phrasings)."""
        super().resolve_company()
        if company_name.looks_like_slug(self.company) and self._pages_company:
            self.company = self._pages_company

    def detail_request(self, row: tuple[str, str]) -> DetailRequest:
        return DetailRequest(row[1], headers={"User-Agent": USER_AGENT})

    def read_detail(self, row: tuple[str, str], response: Any) -> dict[str, Any]:
        fields = _page_fields(response.text)
        if fields is None:
            if has_unparseable_jsonld(response.text):
                raise DetailLost("unparseable JSON-LD on a 200")
            raise DetailLost("no JobPosting JSON-LD or job meta tags on a 200")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        untitled = 0
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                untitled += bool(fields)
                continue
            location = fields.get("location")
            remote = fields.get("remote")
            jobs.append(
                Job(
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote if remote is not None else is_remote(location),
                    department=fields.get("department"),
                    url=self.job_url(item["url"]),
                    posted_at=fields.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                    salary=fields.get("salary"),
                )
            )
        self.note_unread_rows(untitled, len(raw), "had a JobPosting with no title")
        return jobs

    def job_url(self, job_url: str) -> str:
        """The sitemap's own <loc>: the page every posting was read from, and what it serves."""
        return job_url

    def _salary_field(self, raw: Any) -> str | None:
        """Delegates to :func:`_salary`, which the JSON-LD walk calls with no scraper in hand."""
        return _salary(raw)


def sitemap_rows(xml: str, host: str) -> list[tuple[str, str]]:
    """``(job_id, url)`` per posting on ``host``, deduped by id, in sitemap order.

    Only the front's own host counts. An alias host's sitemap redirects to its canonical front's
    (``www.takedajobs.com`` lists ``jobs.takeda.com``'s 838 postings), so reading its job URLs as
    its own would serve that Board twice under two keys.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for loc in _SITEMAP_LOC.findall(xml):
        parts = urlsplit(loc)
        match = JOB_PATH.match(parts.path)
        if (
            not match
            or (parts.hostname or "").lower() != host.lower()
            or match.group(1) in seen
        ):
            continue
        seen.add(match.group(1))
        rows.append((match.group(1), loc))
    return rows


def _page_fields(page: str) -> dict[str, Any] | None:
    """The fields of one job page: its JSON-LD ``JobPosting``, else the platform's own meta tags
    (:func:`_meta_fields`); None when it carries neither."""
    node = find_job_posting(page)
    if node is None:
        return _meta_fields(page)
    title = _PAGE_TITLE.search(page)
    places = node.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    location = "; ".join(
        dict.fromkeys(text for text in map(job_location_text, places) if text)
    )
    return {
        **job_posting_fields(node),
        # 126 of 990 pages name more than one place; each is kept, "; "-joined.
        "location": location or None,
        "department": _meta(page, "gtm_tbcn_jobcategory"),
        "posted_at": _iso_date(node.get("datePosted")),
        "salary": _salary(node.get("baseSalary")),
        "company": hiring_organization(node.get("hiringOrganization")),
        "title_company": _title_company(title.group(1)) if title else None,
        "apply_url": _apply_url(page),
    }


def _meta_fields(page: str) -> dict[str, Any] | None:
    """A job page's fields from the ``gtm_tbcn_*`` meta tags TalentBrew writes on every job page,
    for the fronts whose template carries no JSON-LD (8 of 185 sampled, every page of each:
    ``jobs.jabil.com``, ``jobs.cancer.org``, ``jobs.greatclips.com`` and five more). Measured
    against JSON-LD on 195 pages of 40 fronts that carry both: the title, category and apply URL
    agreed on 195, the ``ats-description`` body was present on 190 and within 10% of JSON-LD's
    length on 157, and ``gtm_firstindex`` — the day TalentBrew first indexed the posting, always
    month first — equalled ``datePosted`` on 151. Places are TalentBrew's full names ("Québec"
    where JSON-LD says "QC").
    """
    title = _meta(page, "gtm_tbcn_jobtitle")
    if not title:
        return None
    page_title = _PAGE_TITLE.search(page)
    place = _meta(page, "gtm_tbcn_location")
    return {
        "title": title,
        "description": _ats_description(page),
        # "Sherman~Illinois~United States|Grafton~Illinois~United States": places on "|".
        "location": "; ".join(
            dict.fromkeys(
                ", ".join(part.strip() for part in one.split("~") if part.strip())
                for one in (place or "").split("|")
                if one.strip()
            )
        )
        or None,
        "remote": None,
        "employment_type": None,
        "department": _meta(page, "gtm_tbcn_jobcategory"),
        "posted_at": _us_date(_meta(page, "gtm_firstindex")),
        "salary": None,
        "company": None,
        "title_company": _title_company(page_title.group(1)) if page_title else None,
        "apply_url": _apply_url(page),
    }


def _meta(page: str, name: str) -> str | None:
    match = re.search(rf'<meta name="{name}" content="([^"]*)"', page)
    return (html.unescape(match.group(1)).strip() or None) if match else None


def _apply_url(page: str) -> str | None:
    """The Apply button's ``apply-url``, else the ``search-job-apply-url`` meta tag."""
    match = _APPLY_URL.search(page)
    return (
        html.unescape(match.group(1)) if match else _meta(page, "search-job-apply-url")
    )


def _ats_description(page: str) -> str | None:
    """The HTML inside the first ``<div class="ats-description…">``, its nested divs balanced."""
    start = _ATS_DESCRIPTION.search(page)
    if not start:
        return None
    depth = 1
    for tag in _DIV_TAG.finditer(page, start.end()):
        depth += -1 if tag.group(1) else 1
        if not depth:
            return page[start.end() : tag.start()]
    return None


def _us_date(value: str | None) -> str | None:
    """``gtm_firstindex``'s "8/18/2026" as ISO-8601."""
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _iso_date(value: Any) -> str | None:
    """``datePosted`` as ISO-8601. TalentBrew writes it unpadded ("2026-9-3", 224 of 990) as
    often as padded; a date-only value, so the two-fetch fabrication test has nothing to move."""
    match = _DATE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _title_company(page_title: str) -> str | None:
    """The company in the page title's "{Title} at {Company}", or None."""
    title = html.unescape(re.sub(r"\s+", " ", page_title)).strip()
    _, sep, company = title.rpartition(" at ")
    return company.strip() or None if sep else None


def _agreed_company(items: list[dict[str, Any]]) -> str | None:
    """The page title's company where :data:`_AGREEMENT` of the pages agree, else the
    ``hiringOrganization`` they agree on, through the `company_name` guards; or None."""
    fields = [item.get("fields") or {} for item in items]
    for key in ("title_company", "company"):
        agreed = company_name.agreed_name((f.get(key) for f in fields), _AGREEMENT)
        name = company_name.from_field("radancy", agreed)
        if name:
            return name
    return None


def _salary(node: Any) -> str | None:
    """``baseSalary`` as a string ``salary.from_field`` reads, or None.

    Many pages state a currency and no amount, which is no salary. The period comes from
    magnitude (:data:`_HOURLY_CEILING`); a ceiling alone would read as a floor, and figures
    straddling the boundary fit no period, so both are refused.
    """
    value = node.get("value") if isinstance(node, dict) else None
    if not isinstance(value, dict):
        return None
    lo, hi = _number(value.get("minValue")), _number(value.get("maxValue"))
    if lo is None:
        return None
    if hi is not None and (lo <= _HOURLY_CEILING) != (hi <= _HOURLY_CEILING):
        return None
    period = "hourly" if lo <= _HOURLY_CEILING else "yearly"
    return to_field(
        _fmt(lo),
        _fmt(hi) if hi is not None and hi != lo else None,
        node.get("currency") or None,
        period,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    return None


def _fmt(value: float) -> str:
    return f"{value:g}" if value != int(value) else str(int(value))


class _HeldBoards:
    """The Scrapable Boards, as what an apply URL is matched against: every lowercased identity,
    and those whose slug is a bare host indexed by that host."""

    def __init__(self, identities: frozenset[str]) -> None:
        self.identities = identities
        self.by_host = {
            identity.split(":", 1)[1]: identity
            for identity in identities
            if _is_host(identity.split(":", 1)[1])
            and not identity.startswith("radancy:")
        }


def _is_host(slug: str) -> bool:
    return "." in slug and "/" not in slug and ":" not in slug


@cache
def _scrapable_boards() -> _HeldBoards:
    """Every Scrapable Board, read once per process from the committed ledger the way
    ``scrapable_boards.load`` reads it (Jibe's `_scraped_icims_tenants` is the precedent)."""
    # Imported here: `scrapable_boards` reaches the scraper registry, which imports this module.
    from headstart.boards import liveness_ledger, scrapable_boards

    ledger = liveness_ledger.dir_for(Path(__file__).resolve().parents[3])
    return _HeldBoards(
        frozenset(
            board.lowercase_identity
            for board in scrapable_boards.load(ledger, min_jobs=0)
        )
    )


_WORKDAY_HOST = re.compile(r"^[^.]+\.wd\d+\.myworkdayjobs\.com$")
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)


def backing_board(apply_url: str | None, held: _HeldBoards) -> str | None:
    """The Scrapable Board an apply URL hands off to, as its lowercased identity, or None.

    Built the way the ledger spells each ATS's row and read through that ATS's own ``slug_from``
    (``registry.company_from_row``), so the identity is the one ``scrapable_boards`` computes. The
    Backing ATSes measured behind 17 fronts: Workday (9 fronts), iCIMS, Taleo Enterprise,
    SmartRecruiters, Avature, Eightfold, SuccessFactors and Paradox. A host-keyed Board (iCIMS,
    SuccessFactors RMK, Eightfold, Phenom, Avature) matches on the apply URL's host. SuccessFactors'
    own apply form (``career2.successfactors.eu/…?company=cargill``) names a company id, not the
    RMK host its Board is keyed by, so it resolves to nothing: an undercount, stated as such.
    """
    parts = urlsplit(apply_url or "")
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if host in held.by_host:
        return held.by_host[host]
    segments = [s for s in parts.path.split("/") if s]
    row: tuple[str, str, str] | None = None
    if _WORKDAY_HOST.match(host):
        sites = [s for s in segments if not _LOCALE.match(s)]
        if sites:
            row = ("workday", "", f"https://{host}/{sites[0]}")
    elif host.endswith(".taleo.net") and segments[:1] == ["careersection"]:
        if len(segments) > 1:
            row = (
                "taleo_enterprise",
                "",
                f"https://{host}/careersection/{segments[1]}",
            )
    elif host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        if segments:
            row = ("smartrecruiters", segments[0], apply_url or "")
    elif host.endswith("greenhouse.io") and segments:
        row = ("greenhouse", segments[0], apply_url or "")
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and segments:
        row = ("lever", segments[0], apply_url or "")
    if row is None:
        return None
    # Imported here for the same cycle `_scrapable_boards` avoids.
    from headstart.boards.board_identity import board_identity, lower_key
    from headstart.scrapers.registry import company_from_row

    identity = lower_key(board_identity(company_from_row(*row)))
    return identity if identity in held.identities else None
