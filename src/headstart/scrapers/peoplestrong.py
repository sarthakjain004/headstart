"""PeopleStrong candidate-portal scraper (``{label}.peoplestrong.com``).

Every tenant's candidate portal is one subdomain label on a wildcard DNS zone, and that label is
this scraper's ``slug``: the portal's Angular app calls its own host's API
(``/api/cp/rest/altone/cp/``), so the label is the URL, the API key and the discovery key at
once. It is case-insensitive — ``Careers-BMWTechWorks`` reads the same Board and the portal's own
``urlinfo`` echoes it lowercased — so :meth:`slug_from` lowercases it.

Everything below was measured 2026-09-25 against the live API — a census of 423 pool labels and
a full read of all 56 hiring Boards, every listing row and every detail (35,732 postings) — and
is written up in ``docs/peoplestrong/2026-09-25_candidate-portal-measurement.md``.

**The listing is public.** ``POST jobs/v1?offset=&limit=`` with an empty JSON body (the page's
body carries only band and grade filters) answers with no session, cookie or token, on HDFC ERGO's
1,923-posting Board as on the rest. An earlier note in this repo called the portals login-walled;
the hosts it named (``abfrl``, ``aavashrms``) are PeopleStrong HRMS logins, not candidate
portals. ``limit`` clamps silently at 99 whatever is asked (100 through 5,000 all returned 99)
and ``offset`` counts rows, so the walk steps by the rows read and ends on a short page. The
stated ``totalRecords`` equalled the rows read on 56 of 56 Boards, so a shortfall is reported.

**The detail carries the description.** ``GET job/{code}/v2`` with the part list the job page
itself asks for (``descriprion`` is the API's own spelling) returned ``jobDescription`` on 99.8%
of postings. With ``part=basic`` alone it does not, which is why one public implementation found
the text "incomplete" and rendered pages instead. A code the endpoint does not hold answers a 200
skeleton with no title — a silent empty, counted as a lost detail. The detail is also the only
source of ``employment_type``, so ADR-0048's skip of an already-held description is not taken:
it would blank that field.

**The tech gate is exact.** Title and ``organizationUnit`` (the department, on 100% of listing
rows; the detail's ``departmentHierarchy`` was null on every posting) both come off the listing,
and the detail changed the title on 0 of 35,732 postings. 1,964 of those were tech (5.5%), so
the gate spares ~94% of detail requests.

**One rate limit spans the platform.** Kong meters 5,000 requests per calendar minute per client
IP across every tenant and endpoint (``X-RateLimit-Remaining-minute`` fell across four different
hosts) and refuses the rest with a bare 429, no Retry-After. Every request therefore waits on one
process-wide :class:`~headstart.scrapers.pacer.Pacer` and a 429 rests the whole process to the
window's end, as ADR-0180 does for ADP — here in the transport itself (:meth:`_fetch`), so the
base class's Detail pass runs unchanged on either transport. Measured: 16 threads ran 10,555 requests
in 130 s with no refusal (4,268 in the one full minute, 71 req/s); 32 threads spent the 5,000 in
~33 s and drew the 429.

Mapped as measured:
  - ``location``: ``locationHierarchyComplete``, one place per posting, as a comma list in the
    provider's order with repeats dropped. The root is the tenant's own — a country on most
    Boards, a sales territory on Muthoot's ("TERRITORY-II>NORTH-1>…", 16,100 rows).
  - ``remote``: no surface states it (the detail's ``Onsite`` was null on every posting), so it
    is read off the location text.
  - ``experience``: the listing's ``expRange`` ("8-14 years", 97.5%), which equals the detail's
    min–max and is already the shape ``experience.from_field`` reads.
  - ``posted_at``: ``jobPostedDate``, equal to the detail's ``CandidatePortalStartDate`` on every
    posting, and never past its own closing date on a listed posting (0 of 35,732).

Not mapped, on purpose:
  - ``salary``: the page shows ``minSalary``–``maxSalary`` only where the entity's display config
    sets ``ctcMaxRendered`` (365 of 35,732 postings), bare, with no currency or period and in
    mixed units ("800000-1000000" beside "23-37"). A figure the employer does not publish stays
    unpublished; the description's own figures still reach Tier 2.
  - ``company``: nothing names the employer — ``urlinfo.title`` is empty on 100 of 104 live
    portals — so the label, which is readable ("hdfcergocareers"), stays the name.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import (
    DEFAULT_REQUEST_HEADERS,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)
from headstart.scrapers.pacer import Pacer

_HOST_SUFFIX = ".peoplestrong.com"
_API = "/api/cp/rest/altone/cp"
#: The page size the API serves, whatever is asked (module docstring).
_PAGE_SIZE = 99
#: Our ceiling on the walk: 49,500 postings. The largest Board measured was 16,315.
_MAX_PAGES = 500
#: The part list the job page itself asks for; `descriprion` is the API's spelling.
_DETAIL_PARTS = "basic,organisational,descriprion,workflow,skill,qualification,certification,language,applied"

#: Request starts spaced process-wide at 16 ms — 3,750 a minute, 25% under the 5,000 budget, the
#: same headroom ADP's pacer keeps. A 429 anyway (another process on the same IP) rests every
#: request for the window and retries, `_TRIES` attempts in all.
_SPACING_S = 0.016
_WINDOW_S = 60.0
_TRIES = 3
#: The fetch seam's own retry ladder, minus 429: its seconds of backoff cannot outlast a minute's
#: window, and quick retries would spend the budget the rest of the process is pacing against.
_RETRY_ON = http.TRANSIENT - {429}
_PACER = Pacer(_SPACING_S)

#: Indian payroll words for a permanent hire that `employment_type_filter.flags` reads as nothing:
#: "On Roll" (397 postings), "Employee" (320), "Regular" (45). Labelled full-time with the
#: provider's word kept; every other value (Permanent 31,105, Full Time 291, Contract 102, …)
#: passes through as stated.
_PAYROLL_FULL_TIME = frozenset({"on roll", "employee", "regular"})


def _employment_type(detail: dict) -> str | None:
    stated = (detail.get("employmentType") or "").strip()
    if stated.lower() in _PAYROLL_FULL_TIME:
        return f"Full Time ({stated})"
    return stated or None


def _native_id(row: dict) -> str:
    """The job code in its URL form: each "/" written "_", as the API's own `jobDetailUrl` does
    (35,732 of 35,732). Codes carry only "/" and "-" otherwise, never "_" or ":", so the form is
    reversible and splits cleanly off a Job id."""
    return row["jobCode"].replace("/", "_")


def _location(row: dict) -> str | None:
    """The place path, "India>Maharashtra>Pune>Pune", as "India, Maharashtra, Pune": one place
    per posting (a "," inside a segment belongs to a place name, 1,724 rows), provider's order,
    a repeated segment dropped."""
    places: list[str] = []
    for place in (row.get("locationHierarchyComplete") or "").split(">"):
        place = place.strip()
        if place and place.lower() not in (p.lower() for p in places):
            places.append(place)
    return ", ".join(places) or None


class PeopleStrongScraper(BaseScraper):
    ats = "peoplestrong"
    # scraper: f"https://{label}.peoplestrong.com/job/detail/{code with / as _}", equal to the
    # API's own `jobDetailUrl` on 35,732 of 35,732 postings; a real one answers a browser 200.
    url_shape = r"https://[a-z0-9-]+\.peoplestrong\.com/job/detail/[A-Za-z0-9_-]+"
    # The per-Job fetch fills `description` and `employment_type` (ADR-0050).
    has_detail_pass = True
    # The edge speaks HTTP/1.1 and answers `Connection: close` (60 of 60), so every request opens
    # a new connection, and 18 of 60 connection attempts took ~2 s (a dropped SYN, retransmitted)
    # against a 25 ms median. The time goes to waiting on connects, so width buys rate: L&T's
    # 1,394 details ran at 20.7/s at 16 threads and 39.3/s at 48, clean. The pacer, not this,
    # caps request starts at 3,750 a minute.
    detail_workers = 48
    # Multiplexing needs a reused connection and this edge allows none: the shared AsyncSession's
    # 10-connection cap held the multiplexed path to 13.0/s at width 16 and 13.5/s at 48 (ADR-0167).
    async_fanout = False
    pacer = _PACER

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The portal label, lowercased; a host or URL in the ledger reduces to its label."""
        return host_of(tenant).lower().removesuffix(_HOST_SUFFIX)

    def alias_key(self) -> str | None:
        """The label the portal names itself in `urlinfo` (ADR-0111): a key in this ATS's own slug
        space, which the inherited redirect-following default is not (it would GET the POST-only
        listing and return a host). It equalled the label on 104 of 104 live Boards; None when
        the portal answers no `url` (an unregistered host) or does not answer."""
        try:
            response = self._fetch(
                "GET",
                f"{self._base}/urlinfo",
                headers=dict(DEFAULT_REQUEST_HEADERS),
                timeout=30,
            )
            stated = (json.loads(response.text).get("response") or {}).get("url")
        except Exception:  # noqa: BLE001 - an unreachable Board has earned no verdict
            return None
        return (
            self.slug_from(stated, "")
            if response.status_code == 200 and stated
            else None
        )

    @property
    def _base(self) -> str:
        return f"https://{self.slug}{_HOST_SUFFIX}{_API}"

    def url(self, offset: int = 0, limit: int = _PAGE_SIZE) -> str:
        """The listing request; the liveness probe asks `limit=1`, since `totalRecords` states
        the whole Board's count whatever the page size."""
        return f"{self._base}/jobs/v1?offset={offset}&limit={limit}"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}{_HOST_SUFFIX}/job/detail/{native_id}"

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        """Every request waits on the shared pacer; a 429 rests the whole process through the
        window and asks again, `_TRIES` attempts in all. The last 429 is returned, not raised:
        the listing marks its Board truncated on it and the Detail pass labels it a loss."""
        kwargs.setdefault("retry_on", _RETRY_ON)
        response = None
        for _ in range(_TRIES):
            self.pacer.wait()
            response = super()._fetch(method, url, **kwargs)
            if response.status_code != 429:
                return response
            self.pacer.rest(_WINDOW_S)
        return response

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        kwargs.setdefault("retry_on", _RETRY_ON)
        response = None
        for _ in range(_TRIES):
            await self.pacer.wait_async()
            response = await super()._fetch_async(session, method, url, **kwargs)
            if response.status_code != 429:
                return response
            self.pacer.rest(_WINDOW_S)
        return response

    def _walk(self) -> list[dict]:
        """Every listing row, in pages of 99 by row offset until a short page."""
        rows: list[dict] = []
        total: int | None = None
        for _ in range(_MAX_PAGES):
            response = self._fetch(
                "POST",
                self.url(len(rows)),
                json={},
                headers=dict(DEFAULT_REQUEST_HEADERS),
                timeout=30,
            )
            if response.status_code == 429:
                self.mark_truncated(
                    f"still rate-limited after {_TRIES} windows at {len(rows)} of "
                    f"{total if total is not None else 'unknown'} postings — the rest unread"
                )
                return rows
            response.raise_for_status()
            body = json.loads(response.text)
            if "totalRecords" not in body:
                # A host that is not a registered portal answers 200 with `response: null` and
                # code 201 "Inside getTpUrl(...)" — measured on 192 pool labels, never on a
                # registered portal — so this is a departed tenant, not an empty Board.
                message = body.get("messageCode") or {}
                self.note_unreadable_board(
                    "a totalRecords envelope",
                    f"code {message.get('code')}: {str(message.get('messages'))[:120]}",
                )
                if rows:
                    self.mark_truncated(
                        f"lost the listing at {len(rows)} of {total} postings"
                    )
                return rows
            page = body.get("response") or []
            total = body.get("totalRecords", total)
            rows.extend(page)
            if len(page) < _PAGE_SIZE:
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(rows)} of {total} postings "
                "— the rest unread"
            )
            return rows
        read = len({row.get("jobCode") for row in rows})
        if total and read < total:
            self.mark_truncated_unless_negligible(
                read,
                total,
                f"read {read} of {total} postings — the rest is unread, not absent",
            )
        return rows

    def fetch_raw(self) -> Any:
        rows = self._walk()
        # Reported, not marked truncated: a lost detail costs its Job the description and the
        # employment type, but the Job is still listed and emitted, so the list is whole.
        details = self.run_detail_pass(
            rows,
            key_of=_native_id,
            what="detail payloads",
            title_of=lambda row: row.get("jobTitle"),
            department_of=lambda row: row.get("organizationUnit"),
        )
        return {"rows": rows, "details": details}

    def detail_request(self, row: dict) -> DetailRequest:
        return DetailRequest(
            f"{self._base}/job/{_native_id(row)}/v2?part={_DETAIL_PARTS}&isReqId=false"
        )

    def read_detail(self, row: dict, response: Any) -> Any:
        detail = json.loads(response.text).get("response") or {}
        if not detail.get("jobTitle"):
            raise DetailLost("no jobTitle on a 200")
        if not detail.get("jobDescription"):
            return DetailWithoutDescription(detail, "no jobDescription")
        if not html_to_text(detail["jobDescription"]):
            # Measured: a description that is one embedded image and no text (2 of 35,732).
            return DetailWithoutDescription(detail, "no text in jobDescription")
        return detail

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in raw.get("rows") or []:
            native_id = _native_id(row)
            detail = details.get(native_id) or {}
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("jobTitle") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=row.get("organizationUnit") or None,
                    url=self.job_url(native_id),
                    posted_at=row.get("jobPostedDate") or None,
                    scraped_at=scraped_at,
                    description=html_to_text(detail.get("jobDescription")),
                    experience=row.get("expRange") or None,
                    employment_type=_employment_type(detail),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """None: the stated figure is published on 365 of 35,732 postings, bare and in mixed
        units (module docstring); the description's own figures still reach Tier 2."""
        return None
