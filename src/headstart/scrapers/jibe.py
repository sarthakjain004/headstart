"""Jibe career-site scraper (``{client}.jibeapply.com``).

Jibe is iCIMS's career-site layer: an employer's branded careers site (``careers.costco.com``) is a
Jibe site, and its postings come from the employer's ATS, usually an iCIMS tenant. Everything below
was measured 2026-09-24 and is written up in ``docs/jibe/2026-09-24_api-jobs-measurement.md``; the
decisions are ADR-0189.

**A Board is a Jibe client, and its id is the slug.** Every vanity site is one client's, and the
client answers at ``{client}.jibeapply.com`` with the same ``/api/jobs`` (276 of 276 walked vanity
hosts gave the same ``totalCount`` there). A client with several vanity sites (Rollins 11, MasTec
8) is one Board: each site's page adds a ``searchOverride`` filter, and without it the API returns
the client's whole public set. So no override is sent.

**robots.txt is honoured per host, strictly — a decision the user made (ADR-0189).** Every request
this module makes to a client host is at least :data:`CRAWL_DELAY` seconds after the previous one
(``crawl-delay: 5`` on 1,138 of 1,143 client hosts), retries included, which is why the shared
fetch's own retry ladder (backoff from 0.75 s) is switched off here. robots.txt is read once per
Board per run and, per RFC 9309, a 4xx means no restrictions and a 5xx or an unreachable host means
none may be assumed — so the Board fails for the run rather than reading as empty. A host that
disallows ``/api/jobs`` (``carrefour`` does) is not read at all. No request is ever made to the
backing ATS's own host, and never to a posting's ``apply_url``.

**iCIMS already covers a tenant we scrape, so its postings are dropped here (the user's option A,
ADR-0189, amended by ADR-0240).** Each row's ``apply_url`` names its backing Board, and Jibe's
``slug`` is that same requisition id, so serving a posting whose tenant the iCIMS scraper reads
would serve it twice. A posting is dropped only when its tenant is a Scrapable iCIMS Board
(:func:`_scraped_icims_tenants`, the committed ledger read the way ``scrapable_boards.load``
reads it); any other tenant keeps its postings here. The gate used to be the tenant's robots.txt,
and a 404 there reads as "no rule applies" — so the dead tenants of ascension (3,172 postings) and
conduent (1,243), which 404 robots.txt and sitemap.xml alike, were dropped with nothing else
serving them. No request reaches an iCIMS host.

**One listing, no detail pass.** ``GET /api/jobs?page=N&limit=100&internal=false``. ``limit`` above
100 answers 422. The listing carries the full description: ``html_to_text`` of it equals the
detail endpoint's on 77 of 90 postings and differs by 1-7 whitespace characters on the rest.
``internal=false`` is what every board sends; ``internal=true`` returns internal-only postings.

**Rows are per language.** ``totalCount`` counts one row per (requisition, language) — Publicis
serves 3,164 rows for 3,002 postings — so one Job is kept per ``slug``, its English row first.

**A 5,100-row window, split by facet.** Past row 5,100 some Boards repeat one fixed page instead of
paging on (Costco, UHS; PetSmart and JCPenney page to their totals). A Board whose ``totalCount``
exceeds :data:`WINDOW` is read as one query per ``state`` facet term, else per category, and the
slices are unioned by (slug, language). Costco's state terms sum to exactly its total. A slice
that is itself over the window is a hard cap (:meth:`mark_truncated`); any other shortfall against
``totalCount`` is measured (:meth:`mark_truncated_unless_negligible`). The walk ends at
``totalCount``, on an empty page, or on a page with no new (slug, language) pair — the window's
signature.

Field mappings, each on the measured distribution (151,619 rows, 277 hosts):
  - ``company`` is the board page ``<title>`` via ``company_name`` (887 of 1,116 live clients
    yield a name), else the ``hiring_organization`` :data:`_AGREEMENT` of the Board's rows state,
    else the slug (ADR-0217). That field varies within 68 of 277 Boards (subsidiaries, brands),
    which is why it needs the agreement and comes second.
  - ``location`` is ``full_location``, which already joins every place with "; " (part count =
    1 + ``additional_locations`` on 8,501 of 8,501 multi-location rows) — de-duplicated, since it
    repeats places. ``remote`` falls back to the location text: there is no remote field, and
    ``location_type: ANY`` (2,966 rows) marks a vague place, not a remote one.
  - ``department`` is ``categories[0].name`` (95.9%); ``department`` itself is set on 0.5%.
  - ``posted_at`` is ``posted_date``: stable across refetches (9,410 of 9,410) and equal to iCIMS's
    own posted date (9,166 of 9,166). ISO with ``+0000`` on 147,276 rows, "Month D, YYYY" on one
    client's 3,541.
  - ``employment_type`` is the schema.org enum, relabelled (:data:`_TYPE_LABELS`).
  - ``salary`` is structured on three non-iCIMS feeds only (:meth:`_salary_field`).
"""

from __future__ import annotations

import time
import urllib.robotparser
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from headstart.boards import company_name
from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, is_remote, requisition_of
from headstart.network import http
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import USER_AGENT, BaseScraper

#: Seconds between two requests to one client host: `crawl-delay: 5` in the robots.txt of 1,138 of
#: 1,143 resolving client hosts (the rest are not Boards). Honoured, never measured past (ADR-0189).
CRAWL_DELAY = 5.0
#: The listing's page size ceiling: 100 answers 200, 101/150/200/250/500/1000 answer 422.
PAGE_SIZE = 100
#: Rows a single query can reach: Costco repeats one fixed page from page 51 on (at limit 100 and
#: at limit 50), and UHS stops at 5,092 unique ids of 6,056.
WINDOW = 5100
#: curl's CURLE_COULDNT_RESOLVE_HOST: the client label has no A record, a departed client. Never
#: retried.
_NO_SUCH_HOST = 6
#: Attempts per request, each paced by :data:`CRAWL_DELAY`. The shared ladder's backoff starts at
#: 0.75 s, which would break the crawl delay on a retry, so this module retries on its own.
_ATTEMPTS = 3
#: The path the listing lives on, which a client's robots.txt must allow.
API_PATH = "/api/jobs"

#: `employment_type` -> label. The eight schema.org values seen on 114,809 rows; each label reads
#: correctly through `employment_type_filter.flags` (TEMPORARY, PER_DIEM and OTHER set no flag, which is
#: right: none of them is one of the four filters).
_TYPE_LABELS: dict[str, str] = {
    "FULL_TIME": "Full-Time",
    "PART_TIME": "Part-Time",
    "TEMPORARY": "Temporary",
    "PER_DIEM": "Per Diem",
    "INTERN": "Intern",
    "CONTRACTOR": "Contractor",
    "CONTRACT_TO_HIRE": "Contract to Hire",
    "OTHER_EMPLOYMENT_TYPE": "Other",
}

#: How much of a client's rows must state one `hiring_organization` before it names the Board —
#: below the title, which is the brand. Measured on the 28 Boards serving their slug on 2026-09-24:
#: 22 state one name on every row, and the ones just under it are one company each (`fm` "FM" on
#: 90%, `boafit` 91.7%); below them `kmbskonicaminolta` read 89.8% then 78.8% a day later
#: (2026-09-25, left to the fallback), and `reyesholdings` 43%, five subsidiaries.
_AGREEMENT = 0.85

#: `salary_frequency` -> the bare unit `salary.from_field` annualises for jibe.
_PERIODS: dict[str, str] = {"HOURLY": "HOUR", "WEEKLY": "WEEK", "YEARLY": "YEAR"}

ALLOW, DISALLOW, UNREACHABLE = "allow", "disallow", "unreachable"


def robots_verdict(status: int | None, text: str, path: str, agent: str) -> str:
    """Whether ``agent`` may fetch ``path`` under a robots.txt that answered ``status``.

    RFC 9309 §2.3.1: a 2xx is parsed; a 4xx means the file is unavailable and no rule applies;
    a 5xx, or no answer at all, means the file is unreachable and nothing may be assumed — which is
    returned as :data:`UNREACHABLE` so each caller decides what "nothing assumed" means for it. A
    3xx arriving here is a redirect :meth:`JibeScraper._fetch` declined to follow off-host, so the
    file was not read either.
    """
    if status is None or status >= 500 or 300 <= status < 400:
        return UNREACHABLE
    if 400 <= status < 500:
        return ALLOW
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    return ALLOW if parser.can_fetch(agent, path) else DISALLOW


@cache
def _scraped_icims_tenants() -> frozenset[str]:
    """The iCIMS tenant hosts the iCIMS scraper reads: every Scrapable iCIMS Board, lowercased, read
    once per process from the committed ledger."""
    # Imported here: `scrapable_boards` reaches the scraper registry, which imports this module.
    from headstart.boards import liveness_ledger, scrapable_boards

    ledger = liveness_ledger.dir_for(Path(__file__).resolve().parents[3])
    return frozenset(
        board.lowercase_identity.removeprefix("icims:")
        for board in scrapable_boards.load(ledger, min_jobs=0)
        if board.ats == "icims"
    )


def _pair(row: dict) -> tuple[str, str]:
    """A row's identity within one listing: `totalCount` counts (requisition, language) pairs."""
    return str(row.get("slug")), str(row.get("language"))


def _apply_host(row: dict) -> str:
    return (urlsplit(row.get("apply_url") or "").hostname or "").lower()


def _iso(value: str | None) -> str | None:
    """`posted_date` as ISO-8601: "2026-09-18T12:07:00+0000" or "September 22, 2026". A year
    before 2000 is no date: smoothieking states all 793 of its dates in year 0026."""
    text = (value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(text, fmt)  # noqa: DTZ007 - the date-only form has no zone
        except ValueError:
            continue
        if parsed.year < 2000:
            return None
        return parsed.isoformat() if parsed.tzinfo else parsed.date().isoformat()
    return None


def _location(row: dict) -> str | None:
    """`full_location`'s places, "; "-joined without repeats, else the structured parts."""
    places: list[str] = []
    for place in (row.get("full_location") or "").split(";"):
        place = place.strip().strip(",").strip()
        if place and place not in places:
            places.append(place)
    if places:
        return "; ".join(places)
    parts = [row.get(k) for k in ("city", "state", "country")]
    return ", ".join(p.strip() for p in parts if p and p.strip()) or None


def _department(row: dict) -> str | None:
    for category in row.get("categories") or []:
        name = (category or {}).get("name") if isinstance(category, dict) else None
        if name and name.strip():
            return name.strip()
    return (row.get("department") or "").strip() or None


def _english_first(rows: list[dict]) -> list[dict]:
    """One row per `slug`, keeping its English row where one exists, in first-seen order."""
    chosen: dict[str, dict] = {}
    for row in rows:
        slug = str(row.get("slug") or "")
        if not slug:
            continue
        held = chosen.get(slug)
        if held is None or (
            str(row.get("language", "")).startswith("en")
            and not str(held.get("language", "")).startswith("en")
        ):
            chosen[slug] = row
    return list(chosen.values())


def _figure(value: Any) -> str | None:
    """A positive salary figure as text, or None for the 0 that means "not stated"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return f"{value:g}" if isinstance(value, float) else str(value)
    return None


class JibeScraper(BaseScraper):
    ats = "jibe"
    # scraper: `job_url` below. `slug` is the backing ATS's requisition id: digits on 138,304 of
    # 151,619 rows, else digits and hyphens or letters (Oracle, Cadient ids).
    url_shape = r"https://[a-z0-9-]+\.jibeapply\.com/jobs/[A-Za-z0-9-]+"

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        self._last_request: float | None = None
        # This Board's robots.txt answer (status, body), read once, before any other request.
        self._robots: tuple[int | None, str] | None = None
        # The name the listing's rows agree on, read by `fetch_raw` (`_agreed_company`).
        self._rows_company: str | None = None

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return tenant.strip().lower().removesuffix(".jibeapply.com")

    @property
    def host(self) -> str:
        return f"{self.slug}.jibeapply.com"

    def url(self, page: int = 1, **filters: str) -> str:
        query = {"page": page, "limit": PAGE_SIZE, "internal": "false", **filters}
        return f"https://{self.host}{API_PATH}?{urlencode(query)}"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.host}/jobs/{native_id}"

    def board_page(self) -> str:
        return f"https://{self.host}/jobs"

    def company_from_page(self, page: str | None) -> str | None:
        """The title's name, unless it reads as an identifier: a client can title its page with
        another client's id (`primowater` serves "primobrands"), which is no better than its own."""
        name = super().company_from_page(page)
        return None if company_name.looks_like_slug(name) else name

    def resolve_company(self) -> None:
        """The board page title's name, else the ``hiring_organization`` the rows `fetch_raw`
        read agree on (ADR-0217): the brand first, both stated before the company is settled."""
        super().resolve_company()
        if company_name.looks_like_slug(self.company) and self._rows_company:
            self.company = self._rows_company

    def _agreed_company(self, rows: list[dict]) -> str | None:
        """The ``hiring_organization`` :data:`_AGREEMENT` of the Board's rows state, or None.
        Every row the listing read counts, the ones dropped for a scraped iCIMS Board too: they
        are this client's postings all the same."""
        agreed = company_name.agreed_name(
            (row.get("hiring_organization") for row in rows), _AGREEMENT
        )
        # A field, so `from_field`'s guards: "iCIMS Talent Acquisition" (`customer0`, 20 of 20
        # rows) is iCIMS hiring on its own client, not the vendor's branding standing in.
        return company_name.from_field(self.ats, agreed)

    def robots_verdict_for(self, path: str) -> str:
        """This client host's robots.txt verdict on `path`, the file read on first use."""
        if self._robots is None:
            try:
                response = self._send("GET", f"https://{self.host}/robots.txt")
                self._robots = (response.status_code, response.text)
            except http.RequestsError as exc:
                if (
                    getattr(exc, "code", None) == _NO_SUCH_HOST
                ):  # no such client: fail as the departed Board
                    raise
                self._robots = (None, "")
        return robots_verdict(*self._robots, path, USER_AGENT)

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        """Every request to the client host passes its robots.txt and is paced.

        A path robots.txt does not allow raises before any request is made, so this is the one
        place the policy is enforced — the listing, the board page and any redirect alike. Other
        hosts pass straight through.
        """
        if urlsplit(url).hostname != self.host:
            return super()._fetch(method, url, **kwargs)
        return self._send(method, url, **kwargs)

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        """Paced: :data:`CRAWL_DELAY` after the last request to the client host, retries too —
        which is why the shared ladder (backoff from 0.75 s) is switched off. A redirect is followed
        only while it stays on the client host, so no request reaches a host whose robots.txt this
        Board has not read: 6 client board pages redirect off-host (an employer site, an SSO
        login). robots.txt itself is the exception, as RFC 9309 asks: its redirects are followed
        up to five hops, onto any host and any path, and none of them is gated on robots.txt."""
        kwargs = {"timeout": 30, **kwargs, "attempts": 1, "allow_redirects": False}
        kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
        robots = urlsplit(url).path == "/robots.txt"
        response = None
        for attempt in range(_ATTEMPTS):
            for _hop in range(5):
                path = urlsplit(url).path
                if not robots and self.robots_verdict_for(path) != ALLOW:
                    raise PermissionError(
                        f"{self.host}{path} is not allowed by robots.txt"
                    )
                self._pace()
                try:
                    response = super()._fetch(method, url, **kwargs)
                except http.RequestsError as exc:
                    if (
                        getattr(exc, "code", None) == _NO_SUCH_HOST
                        or attempt == _ATTEMPTS - 1
                    ):
                        raise
                    response = None
                    break
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urljoin(url, response.headers.get("location") or "")
                    if urlsplit(target).hostname != self.host and not robots:
                        return response
                    url = target
                    continue
                break
            if response is not None and response.status_code not in http.TRANSIENT:
                return response
        return response

    def _get(self, url: str | None = None) -> str:
        response = self._fetch("GET", url or self.url())
        response.raise_for_status()
        return response.text

    async def _get_async(self, session: Any, url: str | None = None) -> str:
        raise NotImplementedError(
            "jibe makes no async requests: each one is robots-gated and paced"
        )

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        raise NotImplementedError(
            "jibe makes no async requests: each one is robots-gated and paced"
        )

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self._last_request + CRAWL_DELAY - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()

    def _json(self, url: str) -> dict:
        response = self._fetch("GET", url)
        response.raise_for_status()
        return response.json()

    def _walk(
        self, first: dict | None = None, **filters: str
    ) -> tuple[list[dict], int, bool]:
        """Every row one query reaches: (rows, its totalCount, whether the window cut it)."""
        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()
        page, data = 1, first
        while True:
            if data is None:
                data = self._json(self.url(page, **filters))
            total = int(data.get("totalCount") or 0)
            batch = [j.get("data") or {} for j in data.get("jobs") or []]
            fresh = [r for r in batch if _pair(r) not in seen]
            if not batch:
                return rows, total, False
            if not fresh:
                return rows, total, True
            rows += fresh
            seen.update(map(_pair, fresh))
            if len(seen) >= total:
                return rows, total, False
            page, data = page + 1, None

    def _facets(self, first: dict) -> tuple[str, list[str]]:
        """The query parameter and terms that split a Board over the window: `state` terms when
        the Board exposes them (Costco's sum to its total), else its categories."""
        found = first.get("filter") or {}
        states = [
            t.get("term") for t in (found.get("facetList") or {}).get("state") or []
        ]
        if any(states):
            return "state", [s for s in states if s]
        categories = [
            c.get("category") for c in (found.get("categories") or {}).get("all") or []
        ]
        return "categories", [c for c in categories if c]

    def _read_board(self) -> list[dict]:
        first = self._json(self.url(1))
        if "jobs" not in first or "totalCount" not in first:
            # An empty Board still states both (3 of 3 live, 2026-09-25).
            self.note_unreadable_board(
                "an /api/jobs envelope with jobs and totalCount",
                f"keys {sorted(first)}",
            )
        total = int(first.get("totalCount") or 0)
        if total <= WINDOW:
            rows, total, windowed = self._walk(first)
            if windowed:
                self.mark_truncated(f"window hit at {len(rows)} of {total} rows")
        else:
            key, terms = self._facets(first)
            rows, seen = [], set()
            for term in terms:
                part, _part_total, windowed = self._walk(**{key: term})
                if windowed:
                    self.mark_truncated(
                        f"{key}={term!r} slice over the {WINDOW}-row window"
                    )
                for row in part:
                    pair = _pair(row)
                    if pair not in seen:
                        seen.add(pair)
                        rows.append(row)
        pairs = set(map(_pair, rows))
        if len(pairs) < total:
            self.mark_truncated_unless_negligible(
                len(pairs), total, f"read {len(pairs)} of {total} rows"
            )
        return rows

    def fetch_raw(self) -> dict:
        verdict = self.robots_verdict_for(API_PATH)
        if verdict == UNREACHABLE:
            # RFC 9309: assume nothing. Failing the Board keeps its rows for the next run rather
            # than reading it as empty, which would evict them.
            raise RuntimeError(
                f"{self.host}/robots.txt unreachable ({self._robots[0]})"
            )
        if verdict == DISALLOW:
            self.note_unreadable_board(f"robots.txt allowing {API_PATH}", "Disallow")
            return {"rows": [], "icims_scraped": {}}
        rows = self._read_board()
        self._rows_company = self._agreed_company(rows)
        tenants = sorted(
            {h for h in map(_apply_host, rows) if h.endswith(".icims.com")}
        )
        return {
            "rows": rows,
            "icims_scraped": {h: h in _scraped_icims_tenants() for h in tenants},
        }

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        scraped = raw.get("icims_scraped") or {}
        jobs: list[Job] = []
        dropped = 0
        rows = raw.get("rows") or []
        self.note_unread_rows(
            sum(1 for row in rows if not row.get("slug")), len(rows), "carried no slug"
        )
        for row in _english_first(rows):
            if scraped.get(_apply_host(row)):
                dropped += 1
                continue
            location = _location(row)
            native_id = str(row["slug"])
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=(row.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=_department(row),
                    url=self.job_url(native_id),
                    posted_at=_iso(row.get("posted_date")),
                    scraped_at=scraped_at,
                    description=html_to_text(row.get("description")),
                    employment_type=_TYPE_LABELS.get(
                        row.get("employment_type"), row.get("employment_type") or None
                    ),
                    salary=self._salary_field(row),
                    # What an Eightfold site in front of this Board states as `atsJobId`
                    # (ADR-0210).
                    requisition=requisition_of(native_id),
                )
            )
        if dropped:
            self.telemetry["icims_covered"] = dropped
            # INFO, so a Board that scrapes slowly for 0 Jobs says why in the log.
            self._log.info(
                f"{self.board_key()}: dropped {dropped} posting(s) a scraped iCIMS Board "
                f"already serves (ADR-0240)"
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` as RANGE CODE UNIT ("13-19.5 HOUR", "1036.44-1036.44 USD WEEK"), built
        by `salary.to_field` for `salary.from_field`.

        Stated on 12,035 rows, all on three non-iCIMS feeds (petsmart, pepsicojobs, smoothieking).
        Bounds are 0 when unstated. A lone ceiling (194 rows) yields None rather than being read as a
        floor, and so does a figure with no `salary_frequency` (27 rows) — the period is unknown.
        `salary_currency` is "USD" on 1,300 rows and empty on the rest, PetSmart's Canadian rows
        included, so no currency is inferred.
        """
        row = raw or {}
        period = _PERIODS.get(row.get("salary_frequency") or "")
        low, high = (
            _figure(row.get("salary_min_value")),
            _figure(row.get("salary_max_value")),
        )
        exact = _figure(row.get("salary_value"))
        if not period:
            return None
        if low and high:
            bounds = (low, high)
        elif low:
            bounds = (low, None)
        elif exact:
            bounds = (exact, exact)
        else:
            return None
        currency = (row.get("salary_currency") or "").strip().upper()
        return salary.to_field(*bounds, currency, period)
