"""Avature: every job portal's sitemap, then one job page per tech posting.

Measured live 2026-09-26 over 77 of the 140 seed tenants and 257 job pages from 55 portals; the method and
every number are in `docs/avature/2026-09-26_listing-measurement.md` (ADR-0245).

**A Board is a tenant host** (`bloomberg` for `bloomberg.avature.net`), not one of its portals.
A tenant runs several portals — `/careers`, `/internalcareers`, `/oldcareersportal`, template
leftovers like fonterra's `/examplePathName` — and its `robots.txt` names each one's sitemap
index. Job ids are tenant-wide: bloomberg's `careers` and `internalcareers` list 257 of the same
ids, each one posting under both. So the listing is the union of every portal's sitemap by id,
and a portal that duplicates another collapses into it for free. Sitemap URLs are read verbatim:
some tenants serve them from a vanity host (`bmcrecruit` → `jobs.bmc.com`, `fb` →
`careers.fbcareers.com`).

**The sitemap is the listing, not the HTML search.** `SearchJobs` pages 12 rows at a time and
ignores `jobRecordsPerPage` (500 asked, 12 served); its rows state a title and a location and no
department, which the sitemap's URL slug nearly matches. The RSS feed stops at 20. One sitemap
fetch per portal lists everything: bloomberg's 352 JobDetail URLs matched its search page's
"352 results". But a sitemap can keep closed postings — bupaanz's `careersau` lists 1,701 ids
against a search page reading "of 999", and 6 of 6 sampled old ids redirect to `/Error` — so a
job page that lands on `/Error` is a closed posting, not a lost detail (:data:`_NOT_PUBLIC`).
Internal portals land every job page on `/Login/` (12 of 12 on bloomberg and broadinstitute);
their postings are skipped the same way, which is why portals named like one are read last.

**The tech gate runs on the URL slug's title.** No listing surface states a department (sitemap,
RSS, search rows), so it is a title-only approximation. Over 244 titled pages: 52 tech, the gate
kept 47 (90.4% recall) and no non-tech page; 3 misses are rule 4's department promotion
("Engineering and CTO", "Hardware Engineering") and 2 are retitled postings whose slug keeps the
original title. It skips 79% of job pages, which matters more here than on any other ATS:

**The edge meters every tenant per client IP.** About a 350-request burst refilling at ~1.2
requests/s (paced at 2/s, the 893rd request drew the first refusal, 446 s in); past it every
request to every tenant answers `406 Not Acceptable` for 3-4 minutes, while WARP answered 200.
So every request waits on one process-wide :data:`_PACER` below that rate, and a 406 moves the
Board onto the spare egress (`egress_fallback_on`). Concurrency buys nothing past the pace:
c=2/4/8/16 measured 1.2/2.3/4.5/7.4 req/s at a flat ~1.7 s latency until the budget ran out.

**Job pages are fetched cookie-free** (`discard_cookies`). Avature serialises one session's
requests: eight concurrent fetches sharing the `ScustomPortal` cookie ran at 1.41/s against
2.41/s at four without it.

**The job page has no one layout.** Portals are templated per tenant: 9 of 26 readable portals
carry JSON-LD, 19 label/value rows in one of two class schemes, 2 neither. Labels are the
tenant's own words in its own language ("Business Area", "Región", "勤務地"). So the page is read
through the stable surfaces first — `og:title`, `og:site_name`, JSON-LD — and the label rows
only through a small vocabulary (:data:`_LOCATION`, :data:`_DEPARTMENT`, :data:`_EMPLOYMENT`).
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urljoin

from headstart.boards import company_name
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailRequest
from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    hiring_organization,
    job_posting_fields,
)
from headstart.scrapers.pacer import Pacer

#: 1 request/s process-wide: under the ~1.2/s refill measured, so a shard never spends its burst.
_PACER = Pacer(1.0)

#: Keeps the pace fed at ~1.7 s per job page; more only queues on the pacer.
_DETAIL_WORKERS = 4

_SITEMAP_LINE = re.compile(r"(?im)^Sitemap:\s*(\S+)")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_JOB_URL = re.compile(r"/JobDetail/([^/?#]+)/(\d+)/?$")
#: Portals whose pages redirect to a login, read last so a shared id keeps its public URL.
_PRIVATE_PORTAL = re.compile(r"internal|employee|referral", re.IGNORECASE)
#: Where a job page redirects when it is not public: `/Error` for a closed posting (a 404 once
#: followed), `/Login/` for a login-walled portal (then on to the tenant's SSO host).
_NOT_PUBLIC = re.compile(r"/(?:Error|Login)/?(?:[?#].*)?$")
_NOT_PUBLIC_LOSS = "not public (closed or login-walled)"

_META = re.compile(
    r'<meta\s+property="og:([a-z_]+)"\s+content="([^"]*)"', re.IGNORECASE
)
_LABEL_PAIRS = (
    re.compile(
        r'class="article__content__view__field__label[^"]*"[^>]*>(.*?)</div>\s*'
        r'<div[^>]*class="article__content__view__field__value[^"]*"[^>]*>(.*?)</div>',
        re.DOTALL,
    ),
    re.compile(
        r'class="article--details__label[^"]*"[^>]*>(.*?)</span>\s*'
        r'<span[^>]*class="article--details__value[^"]*"[^>]*>(.*?)</span>',
        re.DOTALL,
    ),
    re.compile(
        r'<p class="paragraph[^"]*">\s*<span>\s*<strong>(.*?)</strong>\s*</span>\s*'
        r"<span>(.*?)</span>\s*</p>",
        re.DOTALL,
    ),
)
_DETAILS_BLOCK = re.compile(
    r'<article class="article article--details[^"]*"[^>]*>(.*?)</article>', re.DOTALL
)
#: bmcrecruit keeps its body in a rich-text field row rather than a details block.
_RICH_TEXT = re.compile(
    r'class="article__content__view__field[^"]*--rich-text[^"]*"[^>]*>(.*?)</div>\s*</div>',
    re.DOTALL,
)
#: The last resort, for fully custom templates (frequentis, whose JSON-LD does not parse).
_MAIN = re.compile(r"<main\b[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)

#: Label vocabularies, most specific first: tenants name one idea several ways ("Advertising
#: location", "Job Posting Location - City, State", "Country/Region", "Región", "勤務地").
_LOCATION = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(?:job posting |work |primary |advertising )?locations?\b",
        r"^(?:city|site|office|勤務地)\b",
        r"^(?:region|región|country/region)\b",
        r"^(?:country|market)\b",
    )
)
_DEPARTMENT = (
    re.compile(
        r"department|function|business area|business unit|job family|career area|category"
        r"|discipline|division",
        re.IGNORECASE,
    ),
)
_EMPLOYMENT = (
    re.compile(
        r"employment type|type of employment|work type|time type|contract type|job type"
        r"|worker type|tipo de empleo",
        re.IGNORECASE,
    ),
)
#: A title stated as a label, where `og:title` is empty (bradyplus: "Name").
_TITLE = (re.compile(r"^(?:name|job name|job title|求人名)$", re.IGNORECASE),)
_REMOTE = (
    re.compile(r"remote|workplace|work model|work mode|home office", re.IGNORECASE),
)


class AvatureScraper(BaseScraper):
    """One Avature tenant, keyed by its host label (``bloomberg``)."""

    ats = "avature"
    # The sitemap's own <loc>, which is where a posting is served: `{host}/[{locale}/]{portal}/
    # JobDetail/{Title-Slug}/{id}`. Host-agnostic because vanity hosts serve it (jobs.bmc.com).
    url_shape = r"https://[^/]+/(?:[a-z]{2}_[A-Z]{2}/)?[^/]+/JobDetail/[^/?#]+/\d+$"
    has_detail_pass = True
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS
    egress_fallback_on = frozenset({406})
    pacer = _PACER
    #: The employer the fetched job pages agree on (`og:site_name`, else JSON-LD).
    _pages_company: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}.avature.net/robots.txt"

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        self.pacer.wait()
        response = super()._fetch(method, url, **kwargs)
        moved = _moved_job_page(url, response)
        if moved is None:
            return response
        self.pacer.wait()
        return super()._fetch(method, moved, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await self.pacer.wait_async()
        response = await super()._fetch_async(session, method, url, **kwargs)
        moved = _moved_job_page(url, response)
        if moved is None:
            return response
        await self.pacer.wait_async()
        return await super()._fetch_async(session, method, moved, **kwargs)

    def _get_text(self, url: str) -> str:
        response = self._fetch(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=60
        )
        response.raise_for_status()
        return response.text

    def fetch_raw(self) -> Any:
        robots = self._get_text(self.url())
        portals = sorted(
            _SITEMAP_LINE.findall(robots),
            key=lambda sitemap: bool(_PRIVATE_PORTAL.search(sitemap)),
        )
        if not portals:
            # Every live tenant measured names at least its root sitemap; a robots.txt naming
            # none is not a Board with nothing open, and reading it as one would evict the lot.
            self.note_unreadable_board(
                "Sitemap: lines in robots.txt", f"{len(robots)} bytes, none"
            )
            self.mark_truncated("robots.txt names no sitemap")
            return []
        listed: dict[str, dict[str, str]] = {}
        # L'Oréal's portals each redirect their index to one shared index, so its child
        # sitemaps would otherwise be read once per portal against a 1 request/s budget.
        read: set[str] = set()
        for index_url in portals:
            if not index_url.endswith("sitemap_index.xml"):
                continue  # the root `/sitemap.xml` lists only the favicon
            own: list[dict[str, str]] = []
            for sitemap in _LOC.findall(self._get_text(index_url)):
                if sitemap in read:
                    continue
                read.add(sitemap)
                for row in listing_rows(self._get_text(sitemap)):
                    if row["id"] not in listed:
                        listed[row["id"]] = row
                        own.append(row)
            if own and _PRIVATE_PORTAL.search(index_url) and self._login_walled(own[0]):
                # Bloomberg's `internalcareers` lists 193 ids no public portal does; fetching
                # each to learn it redirects to /Login/ cost 103 of 191 job pages a run.
                for row in own:
                    del listed[row["id"]]
        rows = list(listed.values())
        if not rows:
            self._log.info(
                f"{self.board_key()}: no job pages in {len(portals)} portal sitemaps"
            )
            return []
        pages = self.run_detail_pass(
            rows,
            key_of=lambda row: row["id"],
            what="job pages",
            title_of=lambda row: row["slug_title"],
        )
        # A posting whose page is not public is closed or login-walled, not lost: counting it
        # would truncate bupaanz, whose sitemap keeps ~41% closed ids, on every run (ADR-0053).
        lost = pages.missing - self.detail_losses[_NOT_PUBLIC_LOSS]
        if lost:
            self.mark_truncated_unless_negligible(
                len(pages), len(pages) + lost, f"{lost} job pages unreadable"
            )
        items = [
            {**row, "page": pages[row["id"]]} for row in rows if row["id"] in pages
        ]
        self._pages_company = company_name.from_field(
            self.ats,
            company_name.agreed_name(
                (item["page"] or {}).get("company") for item in items
            ),
        )
        return items

    def _login_walled(self, row: dict[str, str]) -> bool:
        """Whether a private-named portal's job page redirects to its login — one request that
        settles the portal. Any other answer keeps its postings, to be read one by one."""
        request = self.detail_request(row)
        try:
            response = self._fetch(
                "GET", request.url, headers=dict(request.headers), **request.options
            )
        except http.RequestsError:  # an unsettled portal is read, not dropped
            return False
        return "/Login" in _location(response)

    def resolve_company(self) -> None:
        """The employer the Board's own job pages name — no extra request."""
        if company_name.looks_like_slug(self.company) and self._pages_company:
            self.company = self._pages_company

    def detail_request(self, row: dict[str, str]) -> DetailRequest:
        return DetailRequest(
            row["url"],
            headers={"User-Agent": USER_AGENT},
            timeout=60,
            options={"discard_cookies": True, "allow_redirects": False},
        )

    def detail_status_loss(self, response: Any) -> str:
        if _NOT_PUBLIC.search(_location(response)):
            return _NOT_PUBLIC_LOSS
        return super().detail_status_loss(response)

    def read_detail(self, row: dict[str, str], response: Any) -> dict[str, Any]:
        return page_fields(response.text)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            page = item.get("page") or {}
            if not page.get("title"):
                continue
            location = page.get("location")
            remote = page.get("remote")
            jobs.append(
                Job(
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=page["title"],
                    location=location,
                    remote=remote if remote is not None else is_remote(location),
                    department=page.get("department"),
                    url=self.job_url(item["url"]),
                    posted_at=page.get("posted_at"),
                    scraped_at=scraped_at,
                    description=page.get("description"),
                    employment_type=page.get("employment_type"),
                    salary=None,
                )
            )
        return jobs

    def job_url(self, job_url: str) -> str:
        return job_url

    def _salary_field(self, raw: Any) -> str | None:
        # No structured salary field is common to tenants; where a page states one it is a
        # tenant's own label ("Pay Range", "Salary Banding MIN") and the description carries it
        # too, which the derivation cascade reads (ADR-0061).
        return None


def _location(response: Any) -> str:
    if not 300 <= response.status_code < 400:
        return ""
    return str(
        (response.headers or {}).get("location")
        or (response.headers or {}).get("Location")
        or ""
    )


def _moved_job_page(url: str, response: Any) -> str | None:
    """Where a job page moved to another job page — cyclecarriage's sitemap names `/en_US/…`
    URLs that 302 to the same path without the locale — else None. Redirects are not followed
    for job pages (`allow_redirects=False`), so that `/Error` and `/Login/` stay readable."""
    target = _location(response)
    return urljoin(url, target) if target and _JOB_URL.search(target) else None


def listing_rows(sitemap_xml: str) -> list[dict[str, str]]:
    """``{id, url, slug_title}`` per JobDetail URL in one sitemap, deduped by id."""
    rows: dict[str, dict[str, str]] = {}
    for url in _LOC.findall(sitemap_xml):
        match = _JOB_URL.search(url)
        if match and match.group(2) not in rows:
            rows[match.group(2)] = {
                "id": match.group(2),
                "url": url,
                "slug_title": match.group(1).replace("-", " "),
            }
    return list(rows.values())


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _labelled(
    labels: dict[str, str], patterns: tuple[re.Pattern[str], ...]
) -> str | None:
    """The first label's value matching the most specific pattern that matches any."""
    for pattern in patterns:
        for label, value in labels.items():
            if value and pattern.search(label):
                return value
    return None


def page_fields(page: str) -> dict[str, Any]:
    """Everything one job page states, read through its stable surfaces first."""
    og = {
        key.lower(): html.unescape(value).strip() for key, value in _META.findall(page)
    }
    node = find_job_posting(page) or {}
    ld = job_posting_fields(node) if node else {}
    labels: dict[str, str] = {}
    for pattern in _LABEL_PAIRS:
        for label, value in pattern.findall(page):
            labels.setdefault(_text(label).rstrip(":").strip(), _text(value))
    body = (
        ld.get("description")
        or "\n".join(_DETAILS_BLOCK.findall(page))
        or "\n".join(v for v in _RICH_TEXT.findall(page) if len(_text(v)) > 200)
        or next(iter(_MAIN.findall(page)), None)
    )
    remote_text = _labelled(labels, _REMOTE)
    return {
        "title": og.get("title")
        or _text(str(ld.get("title") or ""))
        or _labelled(labels, _TITLE),
        "company": og.get("site_name")
        or hiring_organization(node.get("hiringOrganization")),
        "location": ld.get("location") or _labelled(labels, _LOCATION),
        "department": _labelled(labels, _DEPARTMENT)
        or node.get("occupationalCategory")
        or None,
        "employment_type": ld.get("employment_type") or _labelled(labels, _EMPLOYMENT),
        "posted_at": ld.get("posted_at") or None,
        "remote": ld.get("remote") or (is_remote(remote_text) if remote_text else None),
        "description": html_to_text(body) if body else None,
    }
