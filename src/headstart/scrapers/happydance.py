"""Happydance career fronts: the front's sitemap, then a JSON-LD job page per Job, paced.

**Happydance is not an ATS.** It is Ph.Creative's career-site platform (it also runs "Gem Career
Sites"), and a Happydance site is a **Career front** (CONTEXT.md): a branded job site mirroring a
Board on the company's real ATS, whose Apply button hands off to that ATS. For some companies it is
the only public listing — Cognizant's Taleo search redirects to ``careers.cognizant.com``, the
front — and for most it mirrors a Board this repo already scrapes (Caterpillar's
``workday:cat/caterpillarcareers``). It is scraped as a Board keyed under the vendor by its host,
``happydance:careers.cognizant.com``, exactly as Radancy is (ADR-0246, ADR-0264).

Everything below was measured live on 2026-09-28; the write-up, with every sample size, is
``docs/happydance/2026-09-28_sitemap-and-job-page-measurement.md``.

**The Board is the front's host**, which CNAMEs to ``careers.happydance.website`` or a
``phcreative-*.azurefd.net`` Front Door (35 of 35 candidate hosts). The req id in a job URL is the
native id: one posting is listed once per locale (Cognizant's 2,042 postings in 18 locales), and
every locale measured lists the same reqs, so the first URL listed per req is the one read — an
English locale's on every front measured.

**The listing is the sitemap.** ``/sitemap.xml`` is a ``<urlset>`` on the classic template (27
of 35 fronts) and a ``<sitemapindex>`` on the Next.js template (8), whose children on the front's
own host are read one level down. A job URL is recognised by its shape, since the path word is
localised (``empleos``, ``offres-d-emploi``, ``职位``): ``/[{prefix}/[{prefix}/]]{word}/{req}/
{slug}/`` with a digit in the req and none in the word (:data:`JOB_PATH`). On the 7 fronts whose
listing page states a count (``data-results``) the sitemap listed exactly that many (Cognizant
2,042 of 2,042) or one fewer. Fronts on other shapes (``/jobs/job/{slug}/`` on assurant, gartner
and tipico; intuitive's two-id path; uber's bare id) read none; none of them is landed.

**Every field is on the job page**, as JSON-LD ``JobPosting`` on every open posting of the classic
template (442 of 442 answered pages on 22 fronts), its type written ``application/ld&#x2B;json``
on 13 of them (``job_posting_jsonld`` reads both). ``industry`` is the department on every posting
that has one. The Next.js template writes JSON-LD on some fronts' open pages (Warburtons, Prisma
Health, Republic Airways, SAP) and on none of Aristocrat's, National Grid's or Verizon's, whose
data sits only in the page's React payload — those fronts are not landed. The sitemap states only
the URL, so a held description does not spare the page (``skip_held`` stays off, as on Radancy).

**A closed posting is not a lost one.** The Next.js sitemap keeps closed postings, whose pages
answer 200 with the site's shell: no JSON-LD and no ``JobIdentifier`` meta tag (17 of 108 such
pages; Warburtons' own listing names 49 postings and not those). Every other job page sampled
carries the tag, so a page without a posting but with the tag is a real loss and one without
either is closed (:data:`_CLOSED`), which does not mark the Board short.

**No pre-detail tech gate.** The URL's title slug is the only pre-page signal, and over 442 pages
``is_tech`` on it missed 30 of 117 tech postings (25.6%) that ``is_tech(title, industry)`` keeps —
past Avature's accepted 9.6%.

**Every front is paced together.** Cloudflare meters job pages across every front at once: 16
concurrent requests to one front (33.6 req/s) drew ``429`` challenges after ~225 pages, and the
wall then held on the pages of fronts never requested (``jobs.gartner.com``) for over 38 minutes,
while 8 (15 req/s) ran clean and ``/sitemap.xml`` stayed readable throughout. So every request
waits on one process-wide :data:`_PACER` at 2 req/s, the pace 504 pages across 29 fronts ran
clean at, and a ``429`` moves the Board to the spare egress, which answered 200 during the wall.

**Front duplication is measured, not gated**, as for Radancy (:mod:`front_duplication`); which
fronts are landed is the ledger's call (ADR-0264).
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from headstart.boards import company_name
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.jobs.salary import to_field
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
    job_location_text,
    job_posting_fields,
)
from headstart.scrapers.pacer import Pacer

#: 2 requests/s across every front in the process: the pace a 661-page sample over 29 fronts ran
#: at with no refusal, well under the ~15-33 req/s the shared meter tripped between.
_PACER = Pacer(0.5)

#: Keeps the pacer fed (a page answered in ~0.5 s); more only queues on it.
_DETAIL_WORKERS = 4

#: ``/[{prefix}/[{prefix}/]]{word}/{req}/{slug}/``, matched on the unquoted path. The word holds
#: no digit and the req at least one, which is what separates a job from a content page such as
#: ``/en/blog/authors/fiona-desenberg/``.
JOB_PATH = re.compile(r"^/(?:[^/]+/){0,2}?[^/0-9]+/([^/]*\d[^/]*)/[^/]+/?$")
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_APPLY_ANCHOR = re.compile(r"<a\b[^>]*\bjs-apply[^>]*>", re.IGNORECASE)
_HREF = re.compile(r'\bhref="([^"]*)"', re.IGNORECASE)
_JOB_IDENTIFIER = re.compile(r'<meta[^>]*name="JobIdentifier"', re.IGNORECASE)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

#: A job page answering 200 with the site's shell: a posting the sitemap still lists after it
#: closed, which :meth:`HappydanceScraper.fetch_raw` does not count as a loss.
_CLOSED = "closed (a shell page with no posting)"

#: How much of a Board's pages must agree on ``hiringOrganization`` — iCIMS's and Radancy's 0.9.
#: Every page of each of 27 fronts stating one agreed.
_AGREEMENT = 0.9

#: ``baseSalary``'s ``unitText`` as a period ``salary.from_field`` reads. Only YEAR was seen (3
#: of 504 pages state an amount); the others are the schema.org words, and anything else is left
#: unread rather than read as annual.
_PERIODS = {"YEAR": "yearly", "MONTH": "monthly", "HOUR": "hourly"}


class HappydanceScraper(BaseScraper):
    """One Happydance career front, keyed by its host (e.g. ``careers.cognizant.com``)."""

    ats = "happydance"
    # The sitemap's own <loc>, the page every posting is read from. Each of 504 fetched from 29
    # fronts answered 200 with that posting (or, closed, the site's shell).
    url_shape = r"https://[^/]+/(?:[^/]+/){0,2}?[^/0-9]+/[^/]*\d[^/]*/[^/]+/?"
    has_detail_pass = True  # every field lives on the job page (ADR-0050)
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS
    egress_fallback_on = frozenset({429})
    pacer = _PACER
    #: The employer this Board's job pages agree on, read by `fetch_raw`.
    _pages_company: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The front's host, whichever of the row's two columns carries it."""
        return (urlsplit(url).hostname or tenant).strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        self.pacer.wait()
        return super()._fetch(method, url, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await self.pacer.wait_async()
        return await super()._fetch_async(session, method, url, **kwargs)

    def _sitemap(self, url: str) -> str:
        response = self._fetch(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=60
        )
        response.raise_for_status()
        # The UTF-8 BOM some fronts' sitemaps open with would otherwise prefix the first <loc>.
        return response.content.decode("utf-8-sig", "replace")

    def fetch_raw(self) -> Any:
        xml = self._sitemap(self.url())
        if "<sitemapindex" in xml[:2000]:
            xml = "".join(
                self._sitemap(child)
                for child in _SITEMAP_LOC.findall(xml)
                if (urlsplit(child).hostname or "").lower() == self.slug
            )
        listed = sitemap_rows(xml, self.slug)
        if not listed:
            self.note_unreadable_board(
                "Happydance job URLs in the sitemap",
                f"{len(_SITEMAP_LOC.findall(xml))} <loc> entries, none of the job shape",
            )
            return []
        pages = self.run_detail_pass(
            listed, key_of=lambda row: row[0], what="job pages"
        )
        lost = pages.missing - self.detail_losses[_CLOSED]
        if lost:
            # Every field comes from the page, and the sitemap states the Board's whole set, so
            # the loss is measured (ADR-0053/0121).
            self.mark_truncated_unless_negligible(
                len(listed) - lost,
                len(listed),
                f"{lost}/{len(listed)} job pages unreadable",
            )
        items = [
            {"id": req, "url": url, "fields": pages[req]}
            for req, url in listed
            if pages.get(req)
        ]
        self._pages_company = company_name.from_field(
            self.ats,
            company_name.agreed_name(
                (item["fields"]["company"] for item in items), _AGREEMENT
            ),
        )
        front_duplication.report(self, (item["fields"]["apply_url"] for item in items))
        return items

    def resolve_company(self) -> None:
        """The ``hiringOrganization`` the Board's job pages agree on, read during the fetch at no
        extra request; the ledger's name is the front's host."""
        super().resolve_company()
        if company_name.looks_like_slug(self.company) and self._pages_company:
            self.company = self._pages_company

    def detail_request(self, row: tuple[str, str]) -> DetailRequest:
        return DetailRequest(row[1], headers={"User-Agent": USER_AGENT})

    def read_detail(self, row: tuple[str, str], response: Any) -> dict[str, Any]:
        page = response.text
        node = find_job_posting(page)
        if node is None:
            if has_unparseable_jsonld(page):
                raise DetailLost("unparseable JSON-LD on a 200")
            if not _JOB_IDENTIFIER.search(page):
                raise DetailLost(_CLOSED)
            raise DetailLost("job page without JobPosting JSON-LD")
        fields = job_posting_fields(node)
        fields["location"] = job_location_text(
            _one_address_per_place(node.get("jobLocation"))
        )
        industry = node.get("industry")
        if isinstance(industry, list):
            industry = ", ".join(str(part) for part in industry if part)
        return {
            **fields,
            "title": html.unescape(fields["title"] or "").strip() or None,
            "department": html.unescape(industry).strip() or None if industry else None,
            "posted_at": _iso_date(node.get("datePosted")),
            "salary": self._salary_field(node.get("baseSalary")),
            "company": hiring_organization(node.get("hiringOrganization")),
            "apply_url": _apply_url(page, row[1]),
        }

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item["fields"]
            if not fields.get("title"):
                continue
            location = fields.get("location")
            remote = fields.get("remote")
            jobs.append(
                Job(
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=fields["title"],
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
        self.note_unread_rows(
            sum(not item["fields"].get("title") for item in raw),
            len(raw),
            "had a JobPosting with no title",
        )
        return jobs

    def job_url(self, job_url: str) -> str:
        """The sitemap's own <loc>: the page every posting was read from, and what it serves."""
        return job_url

    def _salary_field(self, raw: Any) -> str | None:
        """``baseSalary`` as ``"MIN-MAX CUR period"``, or None where it states no amount (a
        ``name`` alone, as on Coupa) or a unit :data:`_PERIODS` does not name."""
        value = raw.get("value") if isinstance(raw, dict) else None
        if not isinstance(value, dict):
            return None
        low, high = value.get("minValue"), value.get("maxValue")
        period = _PERIODS.get(str(value.get("unitText") or "").upper())
        if not isinstance(low, int | float) or low <= 0 or period is None:
            return None
        return to_field(
            _figure(low),
            _figure(high) if isinstance(high, int | float) and high > low else None,
            raw.get("currency") or None,
            period,
        )


def sitemap_rows(xml: str, host: str) -> list[tuple[str, str]]:
    """``(req, url)`` per posting on ``host``, the first URL listed per req, in sitemap order.

    Only the front's own host counts: another host's job URL is another Board's.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for loc in _SITEMAP_LOC.findall(xml):
        parts = urlsplit(loc)
        match = JOB_PATH.match(unquote(parts.path))
        if (
            not match
            or (parts.hostname or "").lower() != host.lower()
            or match.group(1) in seen
        ):
            continue
        seen.add(match.group(1))
        rows.append((match.group(1), loc))
    return rows


def _apply_url(page: str, page_url: str) -> str | None:
    """The first Apply anchor's target (a ``js-apply`` class or id: ``js-apply-external`` on most
    fronts, ``js-apply-internal-1`` on Coupa's, ``js-apply-now`` on Flutter UKI's), resolved
    against the page; None where the only Apply is an in-page form (``#apply-now``)."""
    for anchor in _APPLY_ANCHOR.findall(page):
        href = _HREF.search(anchor)
        if href and not href.group(1).startswith("#"):
            return urljoin(page_url, html.unescape(href.group(1)))
    return None


def _one_address_per_place(job_location: Any) -> list[Any]:
    """``jobLocation`` with each Place holding one ``address``. The template that writes a plain
    ``ld+json`` type (6 fronts: Equifax, Hilti, Thrivent, Flutter's three) gives every Place a
    *list* of addresses — 105 of 105 pages sampled — which ``job_location_text`` reads as none."""
    places = job_location if isinstance(job_location, list) else [job_location]
    flat: list[Any] = []
    for place in places:
        addresses = place.get("address") if isinstance(place, dict) else None
        if isinstance(addresses, list):
            flat += [{**place, "address": address} for address in addresses]
        else:
            flat.append(place)
    return flat


def _iso_date(value: Any) -> str | None:
    """``datePosted``'s day — a date on most fronts, a timestamp on 7 — or None."""
    match = _DATE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def _figure(value: float) -> str:
    return f"{value:g}" if value != int(value) else str(int(value))
