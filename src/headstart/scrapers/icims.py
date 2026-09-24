"""iCIMS: one sitemap listing, then a JSON-LD detail page per Job.

Everything here rests on measurements taken live on 2026-09-07 against 380 boards; the captures
and the sweep scripts are in `experiment/icims-scraper/`. Three of those measurements shape the
module more than the rest, and each is a trap this code is arranged to make hard to reintroduce.

**The company name** is the only thing read off another page: the listing's title, "Job Listings
at {Name}", one GET per Board (:meth:`ICIMSScraper.board_page`), else the ``hiringOrganization``
the job pages already fetched agree on (ADR-0217).

**One listing surface, not a cascade.** iCIMS boards expose two: `/sitemap.xml`, and the paginated
`/jobs/search?ss=1&in_iframe=1&pr=N` HTML. Only the sitemap is used. On 380 boards, `robots.txt`
predicted sitemap availability perfectly — every board declaring a `Sitemap:` line served one
(273/320 in the larger sweep), every silent board returned 403, and **the set of 403 boards was
exactly the set serving `Disallow: /`**. So the HTML walk exists only to crawl tenants that asked
not to be crawled; declining to build it costs no Board we are entitled to read, and deletes
pagination, per-tenant page size (20/50/1/2/5/6 all observed) and the unreliable "Page N of M"
string in one go. The sitemap is also never the shorter list: across 46 fully-paged boards it
missed nothing the listing had, and on `hmi-esp-harvard` it held 109 where the listing advertised
no pagination at all and looked like 20.

**`datePosted` is fabricated on some boards and real on most.** On 22% of boards it is generated
at render time as `now - 2 years` — the same posting fetched twice, 3.5s apart, returns values
3.5s apart — and `validThrough` is `now + 1 year` on every board measured. But 42 of 54 randomly
sampled hiring boards publish a **real, stable** date, and the two are separable in a single
fetch by the millisecond field alone (:data:`_ANCHORED_MS`): 42/42 real end `.000Z`, 0/12
fabricated do. So `posted_at` prefers the board's own date through :func:`_stated_date` and falls
back to the sitemap's `<lastmod>` only where the board fabricates.

The fallback is not equivalent, which is why it is second: `lastmod` is a last-*modified* stamp,
and where both a real `datePosted` and a `lastmod` existed it ran 0-2,437 days later
(`careers-goaheadlondon`: really posted 2020-01-02, last modified 2026-09-04). An earlier version
of this scraper discarded `datePosted` outright on the strength of a single board — celanese, one
of the fabricating minority — and served every Job a `lastmod` in its place.

**A detail fetch without `in_iframe=1` returns the branded wrapper**: HTTP 200, ~80 KB, no JSON-LD
whatsoever. It fails as "no data", not as an error, so :func:`_ld_fields` returning None is treated
as a gap and marks the Board truncated (ADR-0053) rather than letting a wrapper page read as a
delisting. :func:`_detail_url` is the only place `in_iframe=1` is added.

Two smaller findings are wired in rather than documented. `baseSalary` puts `minValue`/`maxValue`
**directly on the node**, not under `value` as a QuantitativeValue — a spec-correct parser reads
every one as null, which happened during recon — and `unitText` is never present (0/56) while
values span 14 to 215,800, so the period has to come from magnitude. And iCIMS is UA-agnostic
(bare `headstart/0.1`, curl's default, and no User-Agent all return 200), so unlike successfactors
and zwayam there is no UA constraint to respect beyond repo policy.

There is no JSON API. A browser HAR of a job page shows six XHR calls, all third-party (Google
Analytics, altrulabs, Facebook, Snowplow) and none first-party.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from headstart import company_name, log
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailLost, DetailRequest
from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    job_location_text,
    job_posting_fields,
)

_log = log.get(__name__)

_DETAIL_WORKERS = 16

#: The JSON-LD keys this scraper is willing to see. An allowlist rather than a blocklist, so a
#: field nobody vetted cannot reach `parse` by appearing in a future response. `validThrough` is
#: excluded because it is fabricated on every board measured; `datePosted` is admitted but passed
#: through :func:`_stated_date`, which is where the fabrication is filtered. `directApply` is
#: omitted for a different reason: it was True on 207 of 207 sampled postings, so it says nothing.
_LD_KEEP = frozenset(
    {
        "title",
        "description",
        "jobLocation",
        "employmentType",
        "occupationalCategory",
        "baseSalary",
        "jobLocationType",
        "datePosted",
        "hiringOrganization",
    }
)

#: What an unset JSON-LD field holds on this ATS (`job_location_text`'s placeholder too). As a
#: `hiringOrganization` name it states nothing, so it counts on neither side of the agreement.
_UNSET = "UNAVAILABLE"

#: How much of a Board's postings must state one `hiringOrganization` before it names the Board.
#: Measured over 52 Boards 2026-09-24: 42 stated one name on every sampled posting, 4 varied
#: (subsidiaries — `careers-emcorgroup`, `careers-commonspirit`) and 6 stated only `UNAVAILABLE`.
#: The census of 2026-09-25 (six pages from each of the 1,308 Boards serving a host) has a gap
#: to put the floor in: of 1,168 Boards with two or more pages stating a name, 1,118 agreed on
#: every page and the rest on at most five of six (83%).
_AGREEMENT = 0.9

#: A name naming an office or a hiring team rather than the employer — "Headquarters" (alone, on
#: `careers-kdsda`), "Abile Headquarters", "RS&H Talent Acquisition", "T-Solutions Recruiting
#: Team", "MACNY's Job Board", all served on 2026-09-25. Refused rather than trimmed, so a doubtful
#: title falls through to the pages' `hiringOrganization` ("Abile Group, Inc.") or the curated map.
_TEAM_LABEL = re.compile(
    r"\b(?:headquarters|talent acquisition|recruiting team|career search agents|job board)\b",
    re.IGNORECASE,
)

#: A fabricated `datePosted` carries sub-second milliseconds; a real one is midnight- or
#: hour-anchored and ends `.000Z`. Measured over 54 random hiring boards, two fetches 3.5s apart:
#: 12 boards fabricate (the value moves by the elapsed time and sits at `now - 2 years`) and 42
#: state a real, stable date — and the split is exactly the millisecond field, 42/42 real ending
#: `.000Z` against 0/12 fabricated. A fabricated value landing on `.000` by chance is possible
#: (~1 in 1,000) and costs one Job a wrong date; the alternative, discarding every board's real
#: date, cost 78% of them one — measured up to 2,437 days off on `careers-goaheadlondon`.
_ANCHORED_MS = re.compile(r"\.000Z$", re.IGNORECASE)

_SITEMAP_URL = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.IGNORECASE
)
_JOB_PATH = re.compile(r"/jobs/(\d+)/[^/]*/job\b", re.IGNORECASE)
_CLASSIC_TITLE = re.compile(
    r'<h1[^>]*class=["\'][^"\']*\biCIMS_Header\b[^"\']*["\'][^>]*>(.*?)</h1>',
    re.DOTALL | re.IGNORECASE,
)
_CLASSIC_INFO = re.compile(
    r'<div[^>]*class=["\'][^"\']*\biCIMS_InfoMsg_Job\b[^"\']*["\'][^>]*>\s*'
    r'<div[^>]*class=["\'][^"\']*\biCIMS_Expandable_Container\b[^"\']*["\'][^>]*>\s*'
    r'<div[^>]*class=["\'][^"\']*\biCIMS_Expandable_Text\b[^"\']*["\'][^>]*>'
    r"(.*?)</div>\s*</div>\s*</div>",
    re.DOTALL | re.IGNORECASE,
)
_CLASSIC_FIELD = re.compile(
    r'<dt[^>]*class=["\'][^"\']*\biCIMS_JobHeaderField\b[^"\']*["\'][^>]*>'
    r'(.*?)</dt>\s*<dd[^>]*class=["\'][^"\']*\biCIMS_JobHeaderData\b[^"\']*["\'][^>]*>'
    r"(.*?)</dd>",
    re.DOTALL | re.IGNORECASE,
)
_CLASSIC_LOCATION = re.compile(
    r'<div[^>]*class=["\'][^"\']*\bheader\s+left\b[^"\']*["\'][^>]*>.*?'
    r"<span[^>]*>\s*Location\s*</span>\s*<span[^>]*>(.*?)</span>",
    re.DOTALL | re.IGNORECASE,
)

# Above this, a figure is an annual salary; at or below it, an hourly rate. iCIMS never states the
# period (`unitText` absent on all 56 sampled `baseSalary` nodes) while the values themselves span
# 14 to 215,800 — 35 of 55 hourly, 20 annual — so magnitude is the only signal there is. 1,000 sits
# in the empty band between the two clusters: the largest hourly figure measured is 215 and the
# smallest annual one is 27,040.
_HOURLY_CEILING = 1_000


class ICIMSScraper(BaseScraper):
    """One iCIMS tenant's board, keyed by its host (e.g. ``career-celanese.icims.com``)."""

    ats = "icims"
    # icims.py ships the sitemap's own <loc>, query stripped: /jobs/{id}/{title-slug}/job.
    # Host-agnostic on purpose. All 35 sampled boards keep job URLs on their own *.icims.com
    # host (0 exceptions, 2026-09-07), but that sample cannot settle the question: the tenant
    # roster was enumerated by a Wayback CDX sweep OF icims.com, so a vanity-hosted tenant is
    # invisible to it by construction. Anchoring on the vendor domain is what flagged real
    # eightfold rows, so the path — which is fixed by iCIMS' own routing — carries the check.
    # The trailing anchor matters: an `in_iframe=1` link would be a serving bug, not a variant.
    url_shape = r"https://[^/]+/jobs/\d+/[^/]+/job$"
    has_detail_pass = True  # every indexable field lives on the job page (ADR-0050)
    #: The name this Board's job pages agree on, read by `fetch_raw` (:func:`_agreed_company`).
    _pages_company: str | None = None
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Host only — the same normalisation zwayam, zoho and personio each override for.

        Discovery stores the raw capture for host-shaped ATSes, so a ledger row can carry a job
        deep link rather than the board root. Left to the default, `url()` would append
        `/sitemap.xml` to a path or query and fetch something that is not the sitemap. Personio's
        version of this bug cost 678 ParseErrors over 19 runs before it was found.
        """
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def fetch_raw(self) -> Any:
        response = self._fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        # 403 is iCIMS enforcing the tenant's own `Disallow: /`, measured identical on 47/47
        # opt-out boards. It is a settled answer, not a transient one, so it raises like any other
        # non-200 and the Board earns a liveness verdict rather than looking empty.
        response.raise_for_status()
        listed = _sitemap_rows(response.text)
        _log.info(f"{self.slug}: sitemap -> {len(listed)} job pages to fetch")
        if not listed:
            return []

        pages = self.run_detail_pass(
            listed, key_of=lambda row: row[0], what="detail fields"
        )
        lost = pages.missing
        if lost:
            # Every field but `posted_at` comes from the job page, so `parse` drops a Job whose
            # page did not arrive — which makes this list knowingly short, and an unmarked short
            # list is what `index sync` reads as a delisting (ADR-0053). This is also where a
            # dropped `in_iframe=1` would surface: the wrapper page parses to None, so the whole
            # Board reports 100% gaps instead of silently returning nothing.
            # The sitemap states the Board's whole set, so the loss is measured: a negligible one
            # is left to ADR-0083's grace period (ADR-0121), while a total failure like the
            # wrapper trap clears no threshold and still truncates. Measured 2026-09-24:
            # `securitycareers-alliedbarton` lost its eviction scope over 1/9199 pages.
            self.mark_truncated_unless_negligible(
                len(listed) - lost,
                len(listed),
                f"{lost}/{len(listed)} job pages unreadable — those Jobs are listed but unbuilt",
            )
        items = [
            {
                "id": job_id,
                "url": url,
                "posted_at": lastmod,
                "fields": pages.get(job_id),
            }
            for job_id, url, lastmod in listed
        ]
        self._pages_company = _agreed_company(items)
        return items

    def board_page(self) -> str:
        """The listing page, titled "Job Listings at {Name}" (the name iCIMS's own template
        writes). The brand, which the user chose before the legal name the job pages' JSON-LD
        states ("UWM" against "United Wholesale Mortgage"); :meth:`resolve_company` falls back
        to that where the title yields nothing. One GET per Board."""
        return f"https://{self.slug}/jobs/search?ss=1&in_iframe=1"

    def company_from_page(self, page: str | None) -> str | None:
        """The listing title's name, unless it names an office or team (:data:`_TEAM_LABEL`)."""
        name = super().company_from_page(page)
        return None if name and _TEAM_LABEL.search(name) else name

    def resolve_company(self) -> None:
        """The listing title's name, else the ``hiringOrganization`` the job pages agree on
        (ADR-0217) — both stated during the fetch, before the Board's company is settled. The
        second costs nothing: `fetch_raw` read it off the pages its Detail pass already fetched."""
        super().resolve_company()
        if company_name.looks_like_slug(self.company) and self._pages_company:
            self.company = self._pages_company

    def detail_request(self, row: tuple[str, str, str | None]) -> DetailRequest:
        return DetailRequest(_detail_url(row[1]), headers={"User-Agent": USER_AGENT})

    def read_detail(
        self, row: tuple[str, str, str | None], response: Any
    ) -> dict[str, Any]:
        """One job page's JSON-LD fields, or a loss named for what the page lacked — a refusal
        and a body that arrived unreadable must not read as one count (ADR-0088's discipline,
        on the pass that needed it: that is how a User-Agent denylist stayed invisible for five
        runs across 102 Boards)."""
        fields = _ld_fields(response.text)
        if fields is None:
            # The `in_iframe=1` trap as well as a genuine JSON-LD outage — the branded wrapper
            # is a well-formed 200 carrying no JobPosting at all.
            raise DetailLost("no JSON-LD on a 200")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                continue  # page unreadable or a wrapper — nothing to keep the Job by
            location = fields.get("location")
            remote = fields.get("remote")
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=fields.get("department"),
                    url=self.job_url(item["url"]),
                    # The board's own `datePosted` when it states a real one, else the
                    # sitemap's `<lastmod>`. Neither alone is right: 22% of boards fabricate
                    # the JSON-LD date, and `lastmod` is a last-*modified* stamp that ran up
                    # to 2,437 days later than the real posting date where both existed.
                    posted_at=fields.get("posted_at") or item.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                    salary=fields.get("salary"),
                )
            )
        return jobs

    def job_url(self, job_url: str) -> str:
        """Delegates to the module-level :func:`_public_url` — the sitemap parsing that builds
        each row (:func:`_sitemap_rows`) runs ahead of any per-job ``self``, the same reason
        :meth:`_salary_field` below delegates to a free function."""
        return _public_url(job_url)

    def _salary_field(self, raw: Any) -> str | None:
        """Delegates to the module-level :func:`_salary`, which does the real formatting.

        The JSON-LD walk (:func:`_ld_fields`) that finds the ``baseSalary`` node runs ahead of
        any per-job ``self`` — it is a free function every test in this module calls directly,
        with no scraper instance in hand — so the formatting logic lives at module scope too,
        and this method exists only to satisfy :meth:`~BaseScraper._salary_field`'s contract.
        """
        return _salary(raw)


def _sitemap_rows(xml: str) -> list[tuple[str, str, str | None]]:
    """``(job_id, public_url, lastmod)`` per posting, deduped, in sitemap order.

    Non-job entries are skipped: all 35 sitemaps sampled carry at least one non-posting URL
    alongside the jobs — `/jobs/intro` on 15 of them, `/jobs/search` on the rest — and neither
    has an id, so `_JOB_PATH` is what separates them rather than a name-based exclusion.
    """
    rows: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for loc, lastmod in _SITEMAP_URL.findall(xml):
        match = _JOB_PATH.search(loc)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        rows.append((job_id, _public_url(loc.strip()), (lastmod or "").strip() or None))
    return rows


def _public_url(job_url: str) -> str:
    """The user-facing job URL: no query string, so never `in_iframe=1`.

    This is what ships as ``Job.url`` and what a person opens — it renders the tenant's branded
    page carrying the apply button (`?mode=apply&apply=yes&in_iframe=1&hashed=N`, which is
    per-link hashed and cannot be constructed here).
    """
    parts = urlsplit(job_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _detail_url(job_url: str) -> str:
    """The fetchable job URL. The only place `in_iframe=1` is added.

    Without it iCIMS serves the branded wrapper: 200, ~80 KB, and no JSON-LD at all.
    """
    parts = urlsplit(job_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "in_iframe=1", ""))


def _ld_fields(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a job page's JSON-LD, or None if it carries none.

    None is the wrapper-page signature as well as a genuine JSON-LD outage (3% of pages measured),
    and both are counted as detail gaps by the caller.
    """
    node = find_job_posting(page)
    if node is None:
        return _classic_fields(page)
    kept = {key: value for key, value in node.items() if key in _LD_KEEP}
    return {
        **job_posting_fields(kept),
        # Only the first of a multi-location posting is used (4 of 207 sampled carry more than
        # one), and the literal `UNAVAILABLE` iCIMS writes into unset address parts is dropped
        # rather than shown.
        "location": job_location_text(
            kept.get("jobLocation"), placeholders={"UNAVAILABLE"}
        ),
        "department": kept.get("occupationalCategory"),
        "salary": _salary(kept.get("baseSalary")),
        "posted_at": _stated_date(kept.get("datePosted")),
        "company": _org_name(kept.get("hiringOrganization")),
    }


def _agreed_company(items: list[dict[str, Any]]) -> str | None:
    """The ``hiringOrganization`` name :data:`_AGREEMENT` of these job pages state, through the
    `company_name` guards, or None. `UNAVAILABLE` states nothing and counts on neither side."""
    agreed = company_name.agreed_name(
        (
            name
            for name in ((item.get("fields") or {}).get("company") for item in items)
            if (name or "").upper() != _UNSET
        ),
        _AGREEMENT,
    )
    name = company_name.from_field("icims", agreed)
    return None if name and _TEAM_LABEL.search(name) else name


def _org_name(node: Any) -> str | None:
    """``hiringOrganization.name``, stripped, or None where the node states none."""
    name = node.get("name") if isinstance(node, dict) else None
    return (name.strip() or None) if isinstance(name, str) else None


def _classic_fields(page: str) -> dict[str, Any] | None:
    """Fields from iCIMS's classic server-rendered job template when JSON-LD is absent.

    Gated on both the job header and at least one expandable content block. The branded wrapper
    has neither combination, so losing ``in_iframe=1`` still reports a detail gap instead of
    fabricating a Job from navigation chrome.
    """
    title_match = _CLASSIC_TITLE.search(page)
    blocks = _CLASSIC_INFO.findall(page)
    if not title_match or not blocks:
        return None
    fields = {
        html_to_text(label) or "": html_to_text(value)
        for label, value in _CLASSIC_FIELD.findall(page)
    }
    location_match = _CLASSIC_LOCATION.search(page)
    location_type = (fields.get("Job Location Type") or "").strip().lower()
    remote_field = (
        (fields.get("Remote (Google for Jobs Only Field)") or "").strip().lower()
    )
    remote = (
        True
        if location_type == "remote" or remote_field == "yes"
        else False
        if location_type in {"onsite", "on-site"} or remote_field == "no"
        else None
    )
    return {
        "title": html_to_text(title_match.group(1)),
        "description": "\n".join(blocks),
        "location": html_to_text(location_match.group(1))
        if location_match
        else fields.get("Location : Location")
        or fields.get("Location Name")
        or fields.get("Job Locations")
        or fields.get("Job Posting Location : Location"),
        "department": fields.get("Category") or fields.get("Department"),
        "employment_type": fields.get("Type") or fields.get("Position Type"),
        "salary": None,
        "posted_at": None,
        "remote": remote,
    }


def _stated_date(value: Any) -> str | None:
    """``datePosted`` when the board states a real one, else None.

    22% of boards generate it per request as ``now - 2 years`` — the same posting fetched twice
    seconds apart moves by the elapsed time. Those are rejected here on the millisecond field
    (see :data:`_ANCHORED_MS`) so a fabricated date can never reach ``Job.posted_at``, while the
    78% of boards that publish a genuine date keep it.

    Returning None is not a loss: :meth:`ICIMSScraper.parse` falls back to the sitemap's
    ``<lastmod>``, which is what every Job used before this filter existed.
    """
    if not isinstance(value, str) or not _ANCHORED_MS.search(value.strip()):
        return None
    return value.strip()


def _salary(node: Any) -> str | None:
    """``baseSalary`` as a string ``headstart.salary`` can actually read, or None.

    iCIMS puts `minValue`/`maxValue` **directly on the node** rather than under `value` as
    schema.org specifies, so both shapes are read — a spec-correct parser sees null on every real
    iCIMS posting. `unitText` is never present, so the period comes from magnitude
    (:data:`_HOURLY_CEILING`).

    The spelling is load-bearing: `salary.extract()` reads `"USD 30 hourly"` and
    `"USD 95000-125000 yearly"` but returns None for `"USD 30.00/hour"`, and 35 of the 55 measured
    figures are hourly — the slash spelling would null the majority case while annual salaries
    landed. So the period word is appended, never a slash.

    Three shapes are refused rather than guessed at, each found in the 56 captured nodes:

    * **A maximum with no minimum** (2/56). No spelling makes `extract` read a lone figure as a
      ceiling — `"up to X"`, `"max X"` and `"0-X"` all land in `min_annual` or parse to None — so
      emitting one would serve a job's ceiling as its floor and match a `min_salary` filter it
      should fail. Losing 3.6% beats that.
    * **Figures straddling the period boundary** (`{"minValue": 16.9, "maxValue": 39520}`, 1/56).
      No single period fits both, so the node states no coherent range.
    * **Neither bound present** — a currency-only node (the remainder of the 27% carrying
      `baseSalary`) states no amount at all.
    """
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    source = value if isinstance(value, dict) else node
    lo = _number(source.get("minValue"))
    hi = _number(source.get("maxValue"))
    if lo is None and hi is None:
        lo = _number(source.get("value"))
    if lo is None:
        return None  # a ceiling alone would be served as a floor
    if hi is not None and (lo <= _HOURLY_CEILING) != (hi <= _HOURLY_CEILING):
        return None  # one bound hourly, the other annual — no period fits
    period = "hourly" if lo <= _HOURLY_CEILING else "yearly"
    currency = node.get("currency") or node.get("salaryCurrency")
    amount = f"{_fmt(lo)}-{_fmt(hi)}" if hi is not None and hi != lo else _fmt(lo)
    return f"{currency} {amount} {period}" if currency else f"{amount} {period}"


def _fmt(n: float) -> str:
    """A figure written without rounding it.

    `f"{n:g}"` collapses to 6 significant digits, turning 149780.8 into 149781 — small, but it is
    a silent edit of a number a company published.
    """
    return str(int(n)) if n == int(n) else f"{n:.2f}".rstrip("0").rstrip(".")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None
