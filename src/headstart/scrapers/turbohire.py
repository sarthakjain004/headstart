"""TurboHire career-page scraper (``{label}.turbohire.co``).

A Board is a career page's **subdomain label** — ``flipkart`` in ``flipkart.turbohire.co`` — and
that label is this scraper's ``slug``. It is what discovery finds, the page's host and the job
link's host; the API keys everything on an organization GUID instead, which the label resolves to
(below). ``CareerPageSubdomain`` equalled the label on 65 of 65 organizations, the 74 live
labels of the landing census resolve to 74 distinct organizations, and the lookup is
case-insensitive, so the lowercase label is the identity.

Everything below was measured 2026-09-30 against the live API and is written up in
``docs/turbohire/2026-09-30_career-api-measurement.md``, over two censuses: the first read the
104 labels then in the pool (65 organizations, 1,268 postings, a detail for each), the landing
census the 114 known after this build's sweeps (74 organizations, 1,341 postings and details).
Figures below are the first census's unless they say otherwise.

**Four calls, all to ``api.turbohire.co``.** The career page is a client-rendered SPA; these are
its own calls:

1. ``GET /api/token/noauth`` — an anonymous Bearer token, not tenant-bound (``client_id``
   RO.Client, an all-zero OrgId), good for 3,600 s. It answers 403 without a ``Referer`` on a
   ``*.turbohire.co`` host (any label, even an unknown one, passes; ``Origin`` alone does not),
   and 401 to an empty ``Authorization: Bearer`` header.
2. ``GET /api/publicorganizations?accountName={label}`` — the organization: ``OrgID`` and
   ``OrgName``, which equalled the page's ``<title>`` on 65 of 65 and names the Board's company.
   **404 with an empty body for a label no organization holds** (39 of 104 pool labels): every
   ``*.turbohire.co`` label serves the SPA with 200, so this is the only dead signal, and the
   scraper raises it as a gone Board.
3. ``POST /api/careerpagev2/filteredjobs?orgId={OrgID}&pageType=0`` with ``{}`` — the whole
   Board in one response: the SPA pages client-side, ``Total`` equalled the rows served on 65 of
   65, and the internal page's 7,556 Flipkart rows came in one response too. ``pageType=0`` is
   the public career page; ``1`` (internal job page) and ``2`` (referral page) list postings no
   outsider can see and are never asked.
4. ``GET /api/publicjobs?jobId={JobId}&fieldVisibility=CareerPage`` — the detail.

**The detail is needed, and the gate before it is exact.** The listing cuts ``JobDescV2`` at 500
characters (877 of 1,268), blanks ``CTCInfo`` on every row and states ``Type`` UNSPECIFIED on
1,266; the detail carries the description, the salary, the type and — on the 441 rows with no
career-page publish date — the only date. So, like pyjamahr's and oracle's, this detail pass does
not take ADR-0048's skip of the already-described. The tech gate (ADR-0166) it does take: the
detail's ``JobTitle`` and ``Department`` equalled the listing's on 1,341 of 1,341 postings (landing
census). At 25.1% tech that saves three of four detail fetches.

Field notes, each measured on the census:
  - ``remote``: no field states it. TurboHire spells a remote place "Remote Job" (42 postings),
    which ``is_remote`` reads off the joined location.
  - ``description``: absent on 202 of 1,268 postings on both surfaces (27 attach a PDF or DOCX,
    not read). Such a Job still ships, counted as a detail gap.
  - ``posted_at``: the listing's ``PublishedDate`` (equal to the detail's career-page publish date
    on all 827 rows stating either), else the detail's ``CreatedDate``, which carries no zone but
    is UTC: it preceded the zoned publish dates on 2,888 of 2,888 pairs, 92 of them by under a
    minute.
  - ``company``: agency Boards (``act``, ``elementshrs``) name each posting's client in
    ``ClientName``; the Board is served under its organization's name all the same.
  - Not mapped: ``Skills`` (the post-hoc extractors read the description) and ``ExpiryDates``
    (463 of 1,268 state one).

No rate limit was found: 480 listing POSTs across organizations at up to 64 in flight ran at 45.7
req/s with no non-200; one organization's listing (Flipkart's, 1.6 s each) flattened at 4 req/s,
still with no refusal. ``api.turbohire.co`` serves no robots.txt (404), so under ADR-0189's
per-host rule nothing restricts it; nothing is requested from a tenant host, whose robots.txt
disallows every crawler but Googlebot.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
    gone_board_error,
)

_API = "https://api.turbohire.co"
#: Concurrent detail fetches. Across Boards the API ran clean to 45.7 req/s at concurrency 64
#: (480 listing requests, zero non-200s); 16 is what pyjamahr, icims and oracle run, because
#: `harvest` scrapes Boards concurrently and peak in-flight is Boards x workers.
_DETAIL_WORKERS = 16

#: The detail's `Type` code -> the label the employment-type filter reads. The codes observed on
#: 1,268 details (FTE 580, CONTRACT 12, INTERN 6, CONTRACT_TO_HIRE 1, OTHER 1, UNSPECIFIED 668).
#: UNSPECIFIED and OTHER state nothing, and fall back to the detail's `JobTypeV2` label.
_TYPE_LABELS: dict[str, str] = {
    "FTE": "Full Time",
    "CONTRACT": "Contract",
    "INTERN": "Internship",
    "CONTRACT_TO_HIRE": "Contract to Hire",
}

#: `CTCInfo.Type` -> the period spelling `salary.from_field` reads. ANNUAL (289 shown) and
#: MONTHLY (29 shown) are the two observed with figures; UNSPECIFIED (1) states no period, so it
#: yields no salary rather than one read at a guessed period.
_CTC_PERIODS: dict[str, str] = {"ANNUAL": "per-year", "MONTHLY": "per-month"}


def _location(row: dict) -> str | None:
    """Every place `Location` names, "; "-joined. It is a JSON *string* holding a list of
    `{Address, PlaceId, …}`; 4 of 1,268 carry a place with no `Address`, which is skipped."""
    try:
        places = json.loads(row.get("Location") or "[]")
    except ValueError:
        return None
    addresses = [
        p["Address"] for p in places if isinstance(p, dict) and p.get("Address")
    ]
    return "; ".join(addresses) or None


def _experience(row: dict) -> str | None:
    """The listing's `Experience` as the job page renders it: "Min - Max Years", "Min+ Years".
    A lone ceiling (1 of 1,268) is left out: the page's "Upto N Years" reads as no floor."""
    exp = row.get("Experience") or {}
    lo, hi = exp.get("MinExp"), exp.get("MaxExp")
    if lo is None:
        return None
    return f"{lo}+ years" if hi is None else f"{lo}-{hi} years"


def _description(detail: dict) -> str | None:
    """The detail's description as the job page composes it: `JobDescriptionV2` (else the V1
    field), and, when a posting states both Roles & Responsibilities and Eligibility (56 of
    1,268), those two after it under their headings — the page's own concatenation."""
    body = detail.get("JobDescriptionV2") or detail.get("JobDescription") or ""
    roles, eligibility = (
        detail.get("RolesAndResponsibilitiesV2"),
        detail.get("EligibilityV2"),
    )
    if roles and eligibility:
        body = (
            f"Job Description{body}<p></p>Roles & Responsibilities{roles}"
            f"<p></p>Eligibility{eligibility}"
        )
    return html_to_text(body) or None


def _employment_type(detail: dict) -> str | None:
    label = _TYPE_LABELS.get(detail.get("Type") or "")
    return label or (detail.get("JobTypeV2") or "").strip() or None


def _posted_at(row: dict, detail: dict) -> str | None:
    """The career-page publish date, else the creation date — which carries no zone but is UTC
    (module docstring), so it is written with one."""
    if row.get("PublishedDate"):
        return row["PublishedDate"]
    created = detail.get("CreatedDate")
    return f"{created}Z" if created else None


class TurboHireScraper(BaseScraper):
    ats = "turbohire"
    # scraper: f"https://{label}.turbohire.co/job/publicjobs/{JobIdObfuscated}", the link the
    # career page itself opens (`getPublicPageUrl`). Verified: the page server-renders
    # "[Hiring For]: {title}" for a real token and the vendor's generic title for a bogus one.
    # The token is `[A-Za-z0-9_-]` plus `%2F` escapes on 1,268 of 1,268 rows.
    url_shape = r"https://[a-z0-9-]+\.turbohire\.co/job/publicjobs/[\w%-]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    #: The Board's anonymous Bearer token, read first in `fetch_raw` and sent on every call after.
    _token: str = ""

    def url(self) -> str:
        """The organization lookup: the first request keyed on this Board's label, which
        settles whether the label is a Board at all (the listing needs the `OrgID` it returns)."""
        return f"{_API}/api/publicorganizations?accountName={self.slug}"

    def career_page(self) -> str:
        """The Board's career page — sent as the Referer on every call, as the page's own are."""
        return f"https://{self.slug}.turbohire.co/"

    def job_url(self, obfuscated_id: str) -> str:
        return f"https://{self.slug}.turbohire.co/job/publicjobs/{obfuscated_id}"

    def _headers(self, token: str | None = None) -> dict[str, str]:
        # The Referer is what the token endpoint checks (403 without one on a `*.turbohire.co`
        # host); every other call carries it too, as the page's own do. An empty Bearer is not
        # "no token": the token endpoint answers it 401, so the header is sent only with one.
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": self.career_page(),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _json(self, method: str, url: str, token: str | None, **kwargs: Any) -> Any:
        response = self._fetch(
            method, url, headers=self._headers(token), timeout=60, **kwargs
        )
        response.raise_for_status()
        return json.loads(response.text)

    def fetch_raw(self) -> Any:
        # The token: anonymous (`client_id` RO.Client, all-zero OrgId) and good for 3,600 s. A
        # Board's whole fetch — at most 162 details on the largest Board measured, at a p50 of
        # ~0.2 s — ends far inside that, so it is read once per Board and never refreshed.
        self._token = self._json("GET", f"{_API}/api/token/noauth", None)[
            "access_token"
        ]
        response = self._fetch(
            "GET",
            self.url(),
            headers=self._headers(self._token),
            timeout=60,
        )
        if response.status_code == 404:
            raise gone_board_error(
                f"no TurboHire organization holds the label {self.slug!r}"
            )
        response.raise_for_status()
        org = json.loads(response.text)
        self.adopt_company(org.get("OrgName"))
        listing = self._json(
            "POST",
            f"{_API}/api/careerpagev2/filteredjobs?orgId={org['OrgID']}&pageType=0",
            self._token,
            json={},
        )
        rows = listing.get("Result") if isinstance(listing, dict) else None
        if not isinstance(rows, list):
            got = (
                f"keys {sorted(listing)[:5]}"
                if isinstance(listing, dict)
                else "no object"
            )
            self.note_unreadable_board("a `Result` list", got)
            raise BoardUnreadable(
                f"{self.board_key()}: listing has no `Result` list ({got})"
            )
        total = listing.get("Total") or 0
        if total and len(rows) < total:
            self.mark_truncated_unless_negligible(
                len(rows),
                total,
                f"read {len(rows)} of {total} postings — the rest unread",
            )
        # The gate is exact: the detail's `JobTitle` and `Department` equalled the listing's on
        # 1,341 of 1,341 postings (module docstring). ADR-0048's skip of the already-described is
        # not taken: the detail is the only source of `employment_type`, `salary` and, on 441
        # rows, `posted_at`, and skipping it would blank them.
        details = self.run_detail_pass(
            rows,
            key_of=lambda row: row.get("JobId"),
            what="detail payloads",
            title_of=lambda row: row.get("JobTitle"),
            department_of=lambda row: row.get("Department"),
        )
        return {"listing": listing, "details": details}

    def detail_request(self, row: dict) -> DetailRequest:
        # `fieldVisibility=CareerPage` is what trims the payload to the page's own fields: 34 KB
        # for Flipkart's security-architect posting, against 2.3 MB for `0` and 8.4 MB for
        # `None`, whose difference is `AdditionalFields`.
        if not row.get("JobId"):
            raise DetailLost("no job id")
        return DetailRequest(
            f"{_API}/api/publicjobs?jobId={row['JobId']}&fieldVisibility=CareerPage",
            headers=self._headers(self._token),
            timeout=60,
        )

    def read_detail(self, row: dict, response: Any) -> dict | DetailWithoutDescription:
        detail = json.loads(response.text)
        if not _description(detail):
            # Kept for the type, salary and date it states; a gap for the description.
            return DetailWithoutDescription(detail, "200 without description")
        return detail

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in raw["listing"].get("Result") or []:
            detail = details.get(row["JobId"]) or {}
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(row["JobId"]),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("JobTitle") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=row.get("Department") or None,
                    url=self.job_url(row["JobIdObfuscated"]),
                    posted_at=_posted_at(row, detail),
                    scraped_at=scraped_at,
                    description=_description(detail),
                    experience=_experience(row),
                    employment_type=_employment_type(detail),
                    salary=self._salary_field(detail),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """The detail's `CTCInfo`, when the job page shows it: `getCTCString` renders it unless
        `HiddenFrom` names JobSeekers, whatever `IsHidden` says.

        Written as the page states it ("1000-1500 USD per-month"), figures ordered low to high:
        the page swaps an AED pair for display, but the one AED posting measured (20000/25000)
        arrives in order, so the order is read off the figures, not the currency. Many tenants type lakhs or
        placeholders into the rupee fields ("12"-"16", "01"-"4000000"); `salary.from_field`'s
        plausibility floor refuses those (73 of 319 shown), which is the reading wanted — the
        page itself states "₹ 12 - ₹ 16 Annual". A lone ceiling is not emitted: it would read as
        a floor.
        """
        ctc = (raw or {}).get("CTCInfo") or {}
        if "JobSeekers" in (ctc.get("HiddenFrom") or []):
            return None
        period = _CTC_PERIODS.get(ctc.get("Type") or "")
        try:
            figures = sorted(int(v) for v in (ctc.get("Min"), ctc.get("Max")) if v)
        except ValueError:
            return None
        if period is None or not ctc.get("Min") or not figures:
            return None
        low = figures[0]
        high = figures[1] if len(figures) == 2 else None
        return salary.to_field(
            low, high, (ctc.get("CurrencyCode") or "").strip(), period
        )
