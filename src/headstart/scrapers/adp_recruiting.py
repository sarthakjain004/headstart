"""ADP Recruiting Management career-site scraper (``myjobs.adp.com/{slug}/cx``).

ADP Recruiting Management and ADP Workforce Now (``adp.py``) are two separate ADP products, not a
product and its sub-product: each has its own SPA host, its own API host and its own Board
identity. A Board is one **career site**, addressed by the path word of its public URL,
``myjobs.adp.com/{slug}/cx``. That slug keys the site's public record,
``myjobs.adp.com/public/staffing/v1/career-site/{slug}``, which answers case-insensitively (the
record's own ``domain`` is lowercase on 681 of 681 sites), so the slug is lowercased.

Everything below was measured 2026-09-24 against the live hosts and is written up in
``docs/adp_recruiting/2026-09-24_myjobs-measurement.md`` (ADR-0202). The request flow is the one
the open-source clients ``amikai/openings-mcp`` and ``Masterjx9/OpenPostings`` use; each step was
re-measured rather than adopted.

**The site record carries a token, and the token is the address.** The record answers
``myJobsToken``, ``orgoid`` and ``clientName``. The listing
(``my.adp.com/.../job-requisitions/apply-custom-filters``) wants the token as a ``myjobstoken``
header: without it, or with another site's, it answers 400 ``postingChannelId not found``. The
token binds the career site's posting channel — an org's plain ``job-requisitions`` listing with
only ``orgoid`` answered 284 postings where the site lists 109 — so it is the only request that
reads exactly what the site shows. ``orgoid`` and ``rolecode`` are not needed (both dropped, 200
with the same count); a token 62 minutes old still answered. One token is fetched per Board scrape.

**`Accept-Language` is a filter.** The SPA sends ``en-US`` whatever the browser's locale; absent or
``en-US``, the count is the same on 150 of 150 sites, and curl_cffi's own Chrome default,
``en-US,en;q=0.9``, answers ``count: 0`` on 7 of 7 — which is why every request here states it.
A site offering another language lists some postings only under that one (236 on 15 of 150
sites, 1.2% of the 150 sites' postings; the 186 read were English). A visitor sees those only after
switching the site's language, and this scraper reads the default view the SPA shows (ADR-0202).

**The listing carries the description.** With ``$select`` it returns ``jobDescription`` (99.8%
of 77,242 rows), ``jobQualifications`` (47.7%, a separate "Requirements" block, contained in the
description on only 1,851 rows), ``postingDate``, ``workLevelCode``, ``organizationalUnits`` and
every ``requisitionLocations`` entry. Without ``$select`` it omits all of those.

**The walk.** ``$skip`` is 0-based; ``count`` equalled the rows served on 606 of 606 Boards. A page
past about 1 MB answers 502 (a 1,009,319 B page passed; 100-row pages of 11-12 KB rows failed), so
pages are 50 rows (the largest Board-mean row is 14.2 KB) and a 502 halves the page, down to 5.
Pages are asked in ``reqId`` order and progress is counted in unique ``reqId`` s: unordered, 3 of
606 Boards served one posting twice across pages mid-walk and so lost another (`listing_url`).

**The detail** (``…/job-requisitions/search-meta/{reqId}``, same token) adds pay: the tenant's
``compensationDetails`` string or the pay-transparency min/max amounts. Over 300 tech postings on
~100 Boards, 56 carried one and 39 parse to a salary; 33 of those had no salary the description
yields. Its title equals the listing's on 300 of 300 and its description never adds text, so the
tech gate is exact and the description comes from the listing. The detail is the only source of
salary, so ADR-0048's skip is not taken (it would blank that field). A closed id answers 400.

``postingDate`` is present exactly where the site does not set ``hideDaysPosted`` (44,643 of
44,647 rows; 0 of 32,595 where it is set) and did not move between two reads 40 minutes apart
(348 of 348), so it is served as stated and a hidden date stays hidden. No rate limit was found:
2,500 listing requests at 128-wide concurrency against one site and 1,500 at 64-wide across 30
sites all answered 200, bar 2 read timeouts. The User-Agent does not matter.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlencode

from headstart import employment_type_filter, http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
)

_SITE_HOST = "https://myjobs.adp.com"
_SITE = f"{_SITE_HOST}/public/staffing/v1/career-site"
_API = "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions"
_LISTING = f"{_API}/apply-custom-filters"
_DETAIL = f"{_API}/search-meta"

#: The listing fields `parse` reads. Without `$select` the API omits every one of them but the
#: title and the locations.
_SELECT = (
    "reqId,jobTitle,jobDescription,jobQualifications,workLevelCode,postingDate,"
    "requisitionLocations,organizationalUnits"
)
#: Rows asked per page: under the ~1 MB page at which the API answers 502 for the largest
#: Board-mean row measured (14.2 KB). A 502 halves it, down to `_MIN_PAGE`.
_PAGE = 50
_MIN_PAGE = 5
#: Our ceiling on one Board's walk; the largest Board measured was 5,777 rows (116 pages).
_MAX_PAGES = 1000
#: A 502 here is the page's size, not a flaky origin (module docstring), so it is answered by a
#: smaller page rather than by the fetch seam's retries of the same one.
_RETRY_ON = http.TRANSIENT - {502}
#: Every request's timeout: the site record, the listing pages and the detail.
_TIMEOUT = 60

#: The customFieldGroup codes carrying pay, on the detail only.
_COMPENSATION = "RTiReqExtended_compensationDetails"
_PAY_MIN = "RTiReqExtended_payTransparencyMinSalary"
_PAY_MAX = "RTiReqExtended_payTransparencyMaxSalary"
#: A whole-word "FT"/"PT" (or "F/T", "P/T") in a `workLevelCode` (see `_employment_type`).
_FULL = re.compile(r"(?<![A-Za-z])(?:FT|F/T)(?![A-Za-z])")
_PART = re.compile(r"(?<![A-Za-z])(?:PT|P/T)(?![A-Za-z])")


#: A career site's slug as discovery reads it and `url_shape` states it: the record's own
#: `domain` matched this on 681 of 681 sites, and the ledger's tenant on 990 of 990 live rows. It
#: never ends in a dot, so a URL at the end of a sentence ("…/cx/pathgroup.") does not carry one.
SLUG = r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9_-])?"


def site_url(slug: str) -> str:
    """The career site's public record: its token, `orgoid` and `clientName`."""
    return f"{_SITE}/{quote(slug)}"


def listing_url(skip: int, top: int) -> str:
    """One page of the listing the token names, with the fields `parse` reads, in `reqId` order.

    The order is what keeps a walk whole. Over 6 walks each way across three Boards whose
    postings move mid-walk (`lifewisecareers`, `burgerking`, `stevemaddenretailcareers`), the
    unordered walk lost 9 postings and the `reqId`-ordered one lost 1."""
    query = urlencode(
        {"$select": _SELECT, "$orderby": "reqId", "$top": top, "$skip": skip},
        quote_via=quote,
    )
    return f"{_LISTING}?{query}"


def request_headers(token: str | None = None) -> dict[str, str]:
    """Every request's headers. ``Accept-Language`` is a filter, not a preference: the SPA sends
    ``en-US`` whatever the browser's locale, and so do we. curl_cffi's Chrome default,
    ``en-US,en;q=0.9``, answers ``count: 0`` on every site tried — a silent empty Board."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US",
    }
    if token:
        headers["myjobstoken"] = token
    return headers


def _location_name(loc: dict) -> str:
    """A location's own name ("Remote", "Flowerama Store #432"), which the site does not render."""
    return ((loc.get("nameCode") or {}).get("longName") or "").strip()


def _address(loc: dict) -> str:
    """One location as the site renders it: "City, State, Country", blanks dropped — or the
    location's own name where the address is empty (24 of 83,190 locations)."""
    addr = loc.get("address") or {}
    parts = [
        (addr.get("cityName") or "").strip(),
        ((addr.get("countrySubdivisionLevel1") or {}).get("longName") or "").strip(),
        ((addr.get("country") or {}).get("longName") or "").strip(),
    ]
    text = ", ".join(p for p in parts if p)
    return text or _location_name(loc)


def _location(row: dict) -> str | None:
    """Every location, "; "-joined without repeats (3.4% of rows name more than one)."""
    places: list[str] = []
    for loc in row.get("requisitionLocations") or []:
        place = _address(loc)
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


def _remote(row: dict, location: str | None) -> bool | None:
    """The location guess, over the address and each location's own name: a tenant's "Remote"
    location can carry a real address ("Work from home, Virginia, United States"
    under the name "Remote"). There is no remote field."""
    names = [_location_name(loc) for loc in row.get("requisitionLocations") or []]
    return is_remote("; ".join(p for p in [location, *names] if p) or None)


def _department(row: dict) -> str | None:
    """`organizationalUnits` names (92.6% of rows): "Information Technology", "Retail". The
    listing's `departmentName` is a payroll code ("GC Retail-GC-RETAIL") and is not read."""
    names = [
        (u.get("name") or "").strip() for u in row.get("organizationalUnits") or []
    ]
    return "; ".join(n for n in names if n) or None


def _description(row: dict) -> str | None:
    """`jobDescription` plus the separate `jobQualifications` block the site shows under
    "Requirements", unless the description already contains it."""
    desc = html_to_text(row.get("jobDescription")) or ""
    quals = html_to_text(row.get("jobQualifications")) or ""
    if quals and quals not in desc:
        desc = f"{desc}\n\n{quals}" if desc else quals
    return desc or None


def _employment_type(row: dict) -> str | None:
    """`workLevelCode` as stated, labelled where it only abbreviates. "FT" and "PT" reach no
    `employment_type` filter (it matches "full", "part", ...), so a value that reaches none and
    names one of them whole gets the label in front: "Part-time (PT 129 or Less Hours)". That is
    645 of 77,242 rows ("PT 129 or Less Hours" 340, "FT" 95, ...). Everything else — "Variable",
    "PRN", "Seasonal", "Temporary" — stays as stated, as it does on every other scraper."""
    value = (row.get("workLevelCode") or "").strip()
    if not value or any(employment_type_filter.flags(value).values()):
        return value or None
    if _FULL.search(value):
        return f"Full-time ({value})"
    if _PART.search(value):
        return f"Part-time ({value})"
    return value


def _custom(detail: dict, kind: str) -> dict[str, dict]:
    group = detail.get("customFieldGroup") or {}
    return {
        (f.get("categoryCode") or {}).get("codeValue"): f for f in group.get(kind) or []
    }


def _digits(value: float) -> str:
    """A float as digits, never `:g` (which writes 1,200,000 as `1.2e+06`)."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


class ADPRecruitingScraper(BaseScraper):
    ats = "adp_recruiting"
    has_detail_pass = True  # per-Job fetch fills `salary` (ADR-0050)
    #: No rate limit was found up to 128-wide; 16 is what icims, zwayam, oracle and pyjamahr run.
    detail_workers = 16
    # scraper: f"https://myjobs.adp.com/{slug}/cx/job-details?reqId={reqId}". The SPA's own
    # route for a `reqId`; rendered in Chromium 2026-09-24 it shows the posting on 3 of 3 sites
    # (the page fetches `search-meta/{reqId}` itself). `/cx/job/{reqId}` rendered the board
    # chrome with no posting.
    # The slug is `SLUG`; `reqId` is 13 digits on 77,242 of 77,242.
    url_shape = rf"https://myjobs\.adp\.com/{SLUG}/cx/job-details\?reqId=\d+"
    #: The career site's token, which `read_site` keeps off the site record for the Detail pass
    #: that follows; every detail request carries it, as every listing page does.
    _site_token: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The site record answers any casing (681 of 681 `domain`s are lowercase), and
        discovery emits a trailing dot now and then (`pathgroup.`)."""
        return tenant.strip().rstrip(".").lower()

    def url(self) -> str:
        return site_url(self.slug)

    def job_url(self, req_id: str) -> str:
        return f"{_SITE_HOST}/{self.slug}/cx/job-details?reqId={req_id}"

    def _json(self, url: str, token: str | None = None, **kwargs: Any) -> Any:
        response = self._fetch(
            "GET", url, headers=request_headers(token), timeout=_TIMEOUT, **kwargs
        )
        response.raise_for_status()
        return json.loads(response.text)

    def _page(self, token: str, skip: int, top: int) -> dict | None:
        """One listing page, or None when the host answered 502 for its size."""
        try:
            return self._json(listing_url(skip, top), token, retry_on=_RETRY_ON)
        except http.RequestsError as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 502:
                return None
            raise

    def read_site(self) -> tuple[dict, list[dict], int]:
        """The site record, every listing row, and the count the listing states.

        The record's `clientName` also becomes this Board's company here (`adopt_company`),
        since it arrives with the token and costs no request of its own; the token is kept for
        the Detail pass (`_site_token`). A record with no token reads as no rows and a count of
        0, noted as an unreadable Board."""
        site = self._json(self.url())
        self.adopt_company(site.get("clientName"))
        token = self._site_token = site.get("myJobsToken")
        if not token:
            self.note_unreadable_board("a site record with a myJobsToken", "none")
            return site, [], 0
        rows, total = self._walk(token)
        return site, rows, total

    def _walk(self, token: str) -> tuple[list[dict], int]:
        """Every listing row and the stated count: 0-based `$skip` until `count` unique `reqId`s
        are read.

        A 502 halves the page, down to `_MIN_PAGE`; the next page asks `_PAGE` again, because one
        oversized row should not slow the rest of the Board to a crawl."""
        rows: list[dict] = []
        seen: set[str] = set()
        total: int | None = None
        skip, top = 0, _PAGE
        for _ in range(_MAX_PAGES):
            data = self._page(token, skip, top)
            if data is None:
                if top > _MIN_PAGE:
                    top = max(top // 2, _MIN_PAGE)
                    continue
                self.mark_truncated(
                    f"a {top}-row page at $skip={skip} still answered 502 at {len(seen)} of "
                    f"{total if total is not None else 'unknown'} postings — the rest unread"
                )
                return rows, total or 0
            total = data.get("count") or 0
            page = data.get("jobRequisitions") or []
            for row in page:
                if row.get("reqId") and row["reqId"] not in seen:
                    seen.add(row["reqId"])
                    rows.append(row)
            if not page or len(seen) >= total:
                break
            skip += len(page)
            top = _PAGE
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(seen)} of {total} postings"
            )
            return rows, total or 0
        if total and len(seen) < total:
            self.mark_truncated_unless_negligible(
                len(seen),
                total,
                f"read {len(seen)} of {total} postings — the rest is unread, not absent",
            )
        return rows, total or 0

    def fetch_raw(self) -> Any:
        _, rows, _ = self.read_site()
        # Exact gate: `parse` reads the title and department off this same listing row and the
        # detail overrides neither (300 of 300 titles equal). The description store's skip is
        # declined: the detail is the only source of `salary` (module docstring).
        details = self.run_detail_pass(
            rows,
            key_of=lambda row: row["reqId"],
            what="detail payloads",
            title_of=lambda row: row.get("jobTitle"),
            department_of=_department,
        )
        return {"rows": rows, "details": details}

    def detail_request(self, row: dict) -> DetailRequest:
        # The listing's token and headers: the token names the site's posting channel, and
        # `Accept-Language` is a filter (module docstring).
        return DetailRequest(
            f"{_DETAIL}/{row['reqId']}",
            headers=request_headers(self._site_token),
            timeout=_TIMEOUT,
        )

    def read_detail(self, row: dict, response: Any) -> dict:
        """The one requisition a detail answers. A posting closed since the listing answers 400
        "Bad Request", which the pass labels ``HTTP 400`` before this is called."""
        found = json.loads(response.text).get("jobRequisitions") or []
        if not found:
            raise DetailLost("no jobRequisitions on a 200")
        return found[0]

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in raw.get("rows") or []:
            req_id = row["reqId"]
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(req_id),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("jobTitle") or "").strip(),
                    location=location,
                    remote=_remote(row, location),
                    department=_department(row),
                    url=self.job_url(req_id),
                    posted_at=row.get("postingDate") or None,
                    scraped_at=scraped_at,
                    description=_description(row),
                    employment_type=_employment_type(row),
                    salary=self._salary_field(details.get(req_id)),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` from the detail's pay-transparency amounts, else its
        ``compensationDetails`` string (module docstring).

        The amounts become ``"40000-141700 USD"`` with no period: they state none, and the
        parser's annual default with its plausibility floor refuses an hourly figure rather
        than serving it 2,080x too low. ``compensationDetails`` is the tenant's own free text
        ("$85,000 – $95,000/year", "$21.00-25.00 per hour") and is passed through as stated,
        which ``salary._field_generic`` reads.
        """
        if not raw:
            return None
        amounts = _custom(raw, "amountFields")
        lo = (amounts.get(_PAY_MIN) or {}).get("amountValue")
        hi = (amounts.get(_PAY_MAX) or {}).get("amountValue")
        if lo and hi:
            currency = (amounts[_PAY_MIN].get("currencyCode") or "").strip()
            return " ".join(p for p in (f"{_digits(lo)}-{_digits(hi)}", currency) if p)
        stated = (_custom(raw, "stringFields").get(_COMPENSATION) or {}).get(
            "stringValue"
        )
        return (stated or "").strip() or None
