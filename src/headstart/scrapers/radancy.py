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
more — 12 capped at exactly 500 (``jobs.walgreens.com`` lists 500 of 22,544), one at 10,000 and
one short by chance.
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

**A 403 is the runner's IP refused, not a client the front rejects** (ADR-0346). In each of 47
runs from 2026-09-27 to 09-29, 12.1% to 36.0% (median 23.7%) of 109,522 to 123,107 detail pages
answered HTTP 403, and a few sitemaps did. The loss is uneven across the 15 shards of one run (0 of
9,742 pages on one, 5,270 of 10,736 on another), and 43 of the 67 Boards walled in one of three
runs read clean in another. Walled Boards still served 251-265 pages before the first refusal on
15 of 47. From a laptop this scraper's own client read 1,920 of 1,920 pages of six walled Boards,
and Chrome 16 of 16 pages at 0.95 s each (median), against ~0.12 s a page on the multiplexed path.
The fronts sit behind Akamai (host -> ``{slug}.talentbrew.com`` -> ``edgekey.net``), so this is
Eightfold's shape (ADR-0063): a 403 moves the group onto the spare egress
(:attr:`egress_fallback_on`), not onto a browser that leaves from the same IP.
``docs/radancy/2026-09-29_detail-page-403-wall.md`` has every count.

**Front duplication is measured, not gated.** The owner's decision of 2026-09-26: every front is
scraped in full, including postings whose Backing Board a Scrapable Board already serves, and each
run logs the share per front (:meth:`RadancyScraper._report_front_duplication`) so the decision can
be revisited from numbers. The opposite of Phenom's landing rule, on purpose.
"""

from __future__ import annotations

import html
import re
import urllib.robotparser
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from headstart.boards import company_name
from headstart.jobs.job import Job, host_of, html_to_text, is_remote, requisition_of
from headstart.jobs.salary import to_field
from headstart.network import http
from headstart.scrapers import front_duplication
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
    job_posting_fields,
)

#: Well below the measured knee: one front answered 128 concurrent job-page GETs at 91.5 req/s
#: with 500 of 500 200s, and twelve fronts at once did the same (600 of 600), with no refusal at
#: any width tried, from a laptop; a runner's IP is refused at 16 (ADR-0346).
#: `harvest` multiplies this by the Boards it reads at once.
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
#: The sitemap sizes TalentBrew caps at. Of 188 live fronts, 14 listed more than five postings
#: fewer than they state: 12 at exactly 500 (`jobs.walgreens.com`: 500 of 22,544), one at exactly
#: 10,000 (`jobs.greatclips.com`: 10,000 of 11,989), one short by chance.
_SITEMAP_CAPS = frozenset({500, 10_000})

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

#: The words an employment type is written in. A JSON-LD ``employmentType`` (or a visible label's
#: value) is kept only if every letter-run in it is one of these or in :data:`_FILLER_WORDS` and at
#: least one is not filler; ``F`` alone, upper case, is the one other keeper. TalentBrew tenants
#: write whatever their ATS field holds into ``employmentType`` and the page copies it verbatim.
#: The 2026-09-29 probe read 28 such junk pages holding 17 distinct values, none naming a type:
#: org and shift codes on ``www.capitalonecareers.com`` (``SG``, ``BU``, ``SI``, ``DT``, ``FBENG``,
#: ``DE``, ``MA``, ``DP``, ``TP``), ``1ST`` on ``jobs.citizensbank.com``, ``9`` on
#: ``jobs.boeing.com``, pay grades (``Established`` on ``careers.arm.com``, ``Professional`` on
#: ``careers.cargill.com``, ``Standard`` on ``www.kaiserpermanentejobs.org``) and department names
#: (``Information Systems``, ``Software Engineering``, ``AI Engineering``). Its real values were
#: ``Full time``, ``Full-Time``, ``Regular`` and ``F``: ``jobs.appliedmaterials.com`` writes ``F``
#: and its page states "Time Type: Full time". Requiring every token, not any one, keeps
#: "Full Stack Engineering", "Fixed Income" and "Time Off" out. Only ``full``, ``part``, ``time``,
#: ``regular`` and ``permanent`` were seen on a Board; the rest are schema.org's
#: ``EmploymentType`` enumeration (``CONTRACTOR``, ``TEMPORARY``, ``INTERN``, ``PER_DIEM``) and
#: the owner's list, unmeasured on Radancy.
_EMPLOYMENT_TYPE_WORDS = frozenset(
    {
        "full",
        "part",
        "fulltime",
        "parttime",
        "ft",
        "pt",
        "fte",
        "permanent",
        "regular",
        "contract",
        "contractor",
        "temporary",
        "temp",
        "intern",
        "internship",
        "fixed",
        "casual",
        "seasonal",
        "freelance",
        "hourly",
        "salaried",
        "diem",
    }
)
#: Words that may sit beside a type word ("Full Time", "Fixed Term", "Per Diem") but name none.
_FILLER_WORDS = frozenset(
    {"time", "term", "and", "or", "per", "employee", "employment"}
)
_LETTER_RUN = re.compile(r"[a-z]+")
_TAG = re.compile(r"<[^>]*>")

#: A job page's visible labels for the type, best first, on the 4 of 60 Boards sampled that state
#: one outside JSON-LD (2026-09-29): ``jobs.jabil.com`` "Time Type", ``jobs.kansashealthsystem.com``
#: "Job Type", ``careers.evotec.com`` "Working hours" and "Contract type", ``jobs.rchsd.org``
#: "Schedule". The hours outrank the contract type: evotec states "Full time" and "Permanent",
#: and the first says more.
_EMPLOYMENT_LABELS = (
    "working hours",
    "time type",
    "schedule",
    "job type",
    "contract type",
)
_LABEL_SPAN = re.compile(
    r'<span\b[^>]*\bclass="[^"]*\bjob-info\b[^"]*"[^>]*>\s*<b>\s*([^<]*?)\s*:?\s*</b>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_LABEL_DT_DD = re.compile(
    r'<dt\b[^>]*\bclass="[^"]*\bjob-term[^"]*"[^>]*>\s*([^<]*?)\s*</dt>\s*<dd\b[^>]*>(.*?)</dd>',
    re.IGNORECASE | re.DOTALL,
)


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
    #: Akamai answers a refused runner IP with a bare 403 (module docstring, ADR-0346), on the
    #: sitemap and job pages alike. Retrying on the same IP cannot fix it; the same host serves
    #: 200 from another one.
    egress_fallback_on = frozenset({403})
    #: The name this Board's job pages agree on, read by `fetch_raw` (:func:`_agreed_company`).
    _pages_company: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The front's host, whichever of the row's two columns carries it."""
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def fetch_raw(self) -> Any:
        robots = self._read_robots()
        if not robots.can_fetch("*", self.url()):
            # www.intel-jobs.com: `Disallow: /` (2026-09-28, parked). Nothing on such a front may
            # be read, so it lists nothing to us.
            self.note_unreadable_board("robots.txt allowing the sitemap", "Disallow")
            return []
        response = self._fetch(
            "GET", self.url(), headers={"User-Agent": USER_AGENT}, timeout=60
        )
        response.raise_for_status()
        # `text/xml` with no charset and a UTF-8 BOM: decoding by header would read the BOM as
        # Latin-1 into the first <loc>'s prefix.
        xml = response.content.decode("utf-8-sig", "replace")
        rows = sitemap_rows(xml, self.slug)
        listed = [(job_id, url) for job_id, url in rows if robots.can_fetch("*", url)]
        self.note_unread_rows(
            len(rows) - len(listed), len(rows), "sat on a path robots.txt disallows"
        )
        stated = self._stated_total(robots)
        if not rows:
            # A front with nothing open lands here too (three measured, each stating
            # `data-total-job-results="0"`), so this names what the sitemap held; one that
            # states postings is short, not empty (ADR-0053).
            self.note_unreadable_board(
                "TalentBrew job URLs in the sitemap",
                f"{len(_SITEMAP_LOC.findall(xml))} <loc> entries, none of the job shape",
            )
            if stated:
                self.mark_truncated(
                    f"the sitemap lists 0 of the {stated} postings the front states"
                )
            return []
        if stated is not None and stated > len(rows):
            why = f"the sitemap lists {len(rows)} of the {stated} postings the front states"
            if len(rows) in _SITEMAP_CAPS:
                # A hard cap is the same unreachable remainder every run, however small a
                # share it is, so it truncates outright (ADR-0053).
                self.mark_truncated(why)
            else:
                # A posting added between the two reads: a measured, usually negligible
                # shortfall, left to ADR-0083's grace period when it is (ADR-0121).
                self.mark_truncated_unless_negligible(len(rows), stated, why)
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

    def _read_robots(self) -> urllib.robotparser.RobotFileParser:
        """The front's robots.txt. A 4xx means no rule applies (RFC 9309); a 5xx or a failed
        fetch fails the Board for the run rather than reading a front it may not read."""
        response = self._fetch(
            "GET",
            f"https://{self.slug}/robots.txt",
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if response.status_code >= 500:
            raise http.RequestsError(
                f"{self.slug}/robots.txt answered {response.status_code}"
            )
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        return parser

    def _stated_total(self, robots: urllib.robotparser.RobotFileParser) -> int | None:
        """The count the front's own ``/search-jobs`` page states, or None where it states none
        on this host or robots.txt disallows the page (3 of 188 fronts, ``jobs.bd.com`` among
        them, 2026-09-28). That page is not the ``/search-jobs/`` results endpoint most fronts'
        robots.txt disallows; a page that lands on another host is another front's filtered
        view."""
        if not robots.can_fetch("*", f"https://{self.slug}/search-jobs"):
            return None
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
        """**Front duplication** (CONTEXT.md) over the pages read, measured every run and never
        acted on, by the owner's decision of 2026-09-26 (:func:`front_duplication.report`)."""
        front_duplication.report(
            self,
            (item["fields"].get("apply_url") for item in items if item.get("fields")),
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
                    requisition=fields.get("requisition"),
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
    return {
        # Its `location` keeps every place, "; "-joined: 126 of 990 pages name more than one.
        **job_posting_fields(node),
        "employment_type": _jsonld_employment_type(node.get("employmentType"))
        or _visible_employment_type(page),
        "department": _meta(page, "gtm_tbcn_jobcategory"),
        "posted_at": _iso_date(node.get("datePosted")),
        "salary": _salary(node.get("baseSalary")),
        "company": hiring_organization(node.get("hiringOrganization")),
        "title_company": _title_company(title.group(1)) if title else None,
        "apply_url": _apply_url(page),
        "requisition": _requisition(node.get("identifier"))
        or requisition_of(_meta(page, "gtm_reqid")),
    }


def _gated_employment_type(value: Any) -> str | None:
    """``value`` if it is an employment type (:data:`_EMPLOYMENT_TYPE_WORDS`), else None."""
    text = value.replace("\xa0", " ").strip() if isinstance(value, str) else ""
    words = set(_LETTER_RUN.findall(text.lower()))
    if text == "F" or (
        words
        and words <= _EMPLOYMENT_TYPE_WORDS | _FILLER_WORDS
        and not words <= _FILLER_WORDS
    ):
        return text
    return None


def _jsonld_employment_type(value: Any) -> str | None:
    """JSON-LD ``employmentType``, one string or a list, each element gated on its own and the
    survivors ", "-joined."""
    ones = value if isinstance(value, list) else [value]
    kept = (_gated_employment_type(one) for one in ones)
    return ", ".join(one for one in kept if one) or None


def _visible_employment_type(page: str) -> str | None:
    """The type a job page states in a labelled span or definition list
    (:data:`_EMPLOYMENT_LABELS`), best label first and gated like JSON-LD's; None when none."""
    stated: dict[str, str] = {}
    for label, value in (*_LABEL_SPAN.findall(page), *_LABEL_DT_DD.findall(page)):
        kept = _gated_employment_type(html.unescape(_TAG.sub(" ", value)))
        if kept:
            stated.setdefault(label.lower(), " ".join(kept.split()))
    return next((stated[name] for name in _EMPLOYMENT_LABELS if name in stated), None)


def _requisition(identifier: Any) -> str | None:
    """JSON-LD ``identifier``: a bare string on jobs.takeda.com ("R0178385"), a
    ``PropertyValue`` on other templates."""
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    return requisition_of(identifier) if isinstance(identifier, str | int) else None


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
        "employment_type": _visible_employment_type(page),
        "department": _meta(page, "gtm_tbcn_jobcategory"),
        "posted_at": _us_date(_meta(page, "gtm_firstindex")),
        "salary": None,
        "company": None,
        "title_company": _title_company(page_title.group(1)) if page_title else None,
        "apply_url": _apply_url(page),
        "requisition": requisition_of(_meta(page, "gtm_reqid")),
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
    return _checked_date(year, month, day)


def _iso_date(value: Any) -> str | None:
    """``datePosted`` as ISO-8601. TalentBrew writes it unpadded ("2026-9-3", 224 of 990) as
    often as padded; a date-only value, so the two-fetch fabrication test has nothing to move."""
    match = _DATE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return _checked_date(year, month, day)


def _checked_date(year: int, month: int, day: int) -> str | None:
    """The date as ISO-8601, or None where it names no real day."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


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
