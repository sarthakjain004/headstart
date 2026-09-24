"""Darwinbox careers scraper ({tenant}.darwinbox.{in,com} candidate career site).

Darwinbox is an Angular SPA whose public board loads from one unauthenticated JSON
endpoint (the documented Bulk-Candidates API needs Basic Auth + an API key, but the
careers SPA itself does not):

  POST /ms/candidateapi/job/alljobs?companyId=main
       body {"companyId":"main","page":N,"sort_option":"new","limit":100}
       -> {"status":"success","data":[ ...jobs... ],"job_counts":N}

Two wrinkles drive the shape of this scraper:
  * Cloudflare fingerprints the edge, so this is the one scraper that fetches via curl_cffi
    (impersonate="chrome"). Its 403 was originally an intermittent blip, hence its place in
    the retry set alongside 429/5xx — see below for what that 403 means now.
  * The server caps each page at 100 regardless of `limit`, so we page until a short batch.
    The data-center TLD varies (~77% .in, ~23% .com); we resolve it on the first page.

Since ~2026-08-09 that 403 is no longer a blip: Cloudflare blocks every non-browser client —
any TLS fingerprint, any IP — while admitting a real Chrome from the same address
(`docs/darwinbox/cloudflare-wall.md`, ADR-0056). So when the TLD probe finds *neither* host
serving page 1 and one of them answered 403, that 403 is the wall on the tenant's real host, and
the Board routes through `browser_http`: navigate the careers page once to clear the wall, then
call the same `alljobs` API via an in-page fetch on the warmed tab. Same JSON, same `parse`;
curl stays primary, so the browser costs nothing wherever (or whenever) the wall is down.

Scope worth knowing: only that first-page failure escalates. A 403 arriving mid-pagination — the
wall coming up between pages — still raises, so the Board reports a truncated read rather than
silently re-reading half of it through a second transport.

Only tenants with recruitment enabled return jobs; HR-only tenants (e.g. games24x7,
recruitment_enabled:false) return an empty list.

**`job_counts` is a real, stable board-wide total — verified 2026-09-22 through `browser_http`
(issue #549 was blocked on a direct-curl 403; the wall admits a browser, per the note above).**
10 Hiring Boards checked, ids and hosts in `docs/darwinbox/2026-09-22_job-counts-measurement.md`:
present and non-null on every one, from a 2-job board up to 280. On the two boards spanning more
than one page (271 and 280 jobs), it held the exact same value across every page fetched,
including the terminal short page that ends the loop naturally — so it is safe to check the
pagination loop's own final total against, not just a per-page count. `fetch_raw` and
`_fetch_raw_browser` both now call `mark_truncated_unless_negligible` when the loop ends short of
it (ADR-0121); a hard page-cap exit still calls the unconditional `mark_truncated`, since that
shortfall is unreachable rather than measured. **`experience_from_num` does not exist** on any of
the 10 boards — only `experience_from`/`experience_to` (both real, e.g. `"1"`/`"2"`) alongside the
`experience` string (`"1 - 2 Years"`) this scraper already reads; not wired, since the string
field is already well-formed on every sampled job and nothing here shows it losing information the
numeric pair would recover.

**The company is ``companyinfo``'s ``message.company.company_name``** — the record both paths already
fetch to tell the portal generation apart, so it costs nothing. Every one of the 193 affected
Boards names itself there (2026-09-24).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from headstart import log
from headstart.browser_http import BrowserFetcher
from headstart.fetcher import Fetcher
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)

_PAGE_SIZE = 100  # server caps each page at 100 regardless of the requested limit
_MAX_PAGES = (
    99  # our own ceiling, not the server's — reaching it means the board went unread
)
#: The data-centre TLDs a tenant's host sits on, in the order a scrape tries them. Public: the
#: liveness probe asks the same hosts (ADR-0203).
TLDS = ("in", "com")
_LISTING_PATH = "/ms/candidateapi/job/alljobs?companyId=main"


def _iso_date(raw: str | float | None) -> str | None:
    """Darwinbox posts dates as '21-Apr-2026' — normalize to ISO. Non-ISO strings sort
    lexicographically ABOVE ISO date cutoffs, so left raw they leak through every
    posted-within filter; unparseable values pass through unchanged.

    Some tenants (e.g. orangehealth) send ``posted_on`` as an epoch int instead of the string —
    that raised an uncaught TypeError in ``strptime`` and dropped the *entire* board. Read an int
    as an epoch (ms if it looks like ms, else seconds); anything unreadable yields None (unknown
    date — excluded from posted-within windows) rather than crashing or leaking a garbage value.

    The int is now the norm (8,244/8,244 jobs on 296 Hiring Boards, 2026-09-23) and names the
    posting's date at 00:00 in the *poster's* zone — 18:30Z for IST, 16:00Z for UTC+8, 04:00Z for
    EDT, mixed within one Board and unrelated to the job's own country — so its UTC date is a day
    early east of UTC. No field states that zone, so a value on a quarter hour (every real
    zone's midnight) is snapped to the nearest UTC midnight, right for UTC-11..UTC+12. The 10
    off-quarter values seen were real instants equal to ``created_on`` (spoc, 2020): kept as-is."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            seconds = raw / 1000 if raw > 1e11 else raw
            if seconds % 900 == 0:
                seconds += 12 * 3600
            return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return datetime.strptime(raw, "%d-%b-%Y").strftime("%Y-%m-%d")  # noqa: DTZ007
    except ValueError:
        return raw


def _is_wall(exc: Exception) -> bool:
    """Whether this failure is Cloudflare's 403 — read off ``exc.response``, never ``exc.code``
    (``curl_cffi`` raises ``HTTPError(msg, 0, response)``; that 0 is a curl errno)."""
    return getattr(getattr(exc, "response", None), "status_code", None) == 403


class DarwinboxScraper(BaseScraper):
    ats = "darwinbox"
    url_shape = r"https://[^/]+/ms/candidatev2/[^/]+/careers/jobDetails/[0-9a-f]+$"

    def __init__(
        self,
        slug: str,
        company: str | None = None,
        fetcher: Fetcher | None = None,
        browser_fetcher: Callable[[str], BrowserFetcher] = BrowserFetcher,
    ) -> None:
        """``browser_fetcher`` is the factory this scraper opens once it hits the Cloudflare
        wall — one :class:`~headstart.browser_http.BrowserFetcher` per Board, since clearance is
        per-origin (module docstring, ADR-0056). Defaults to the real adapter (ADR-0153); a test
        can inject a fake with the same ``(page_url) -> context manager`` shape without
        monkeypatching ``headstart.browser_http`` at all."""
        super().__init__(slug, company, fetcher=fetcher)
        self._browser_fetcher = browser_fetcher

    def url(self) -> str:
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        return f"{host}/ms/candidate/careers"

    def host_on_tld(self, tld: str) -> str:
        """This tenant's host on one of :data:`TLDS` — which one serves it is found by asking."""
        return f"https://{self.slug}.darwinbox.{tld}"

    def listing_url_on(self, tld: str) -> str:
        """The job listing this tenant would answer on :meth:`host_on_tld` ``tld``."""
        return f"{self.host_on_tld(tld)}{_LISTING_PATH}"

    def job_url(self, native_id: str) -> str:
        # v2 portal (the norm): browser-verified jobDetails route. On v2 tenants the old
        # /ms/candidate/ app is a 2.4KB stub that redirects to the v2 careers HOME, dropping
        # the job — hence the branch. The legacy fallback is the old app's careers/:id router
        # entry. `_host`/`_new_careers` are set by `fetch_raw` before `parse` ever runs.
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        new_careers = getattr(self, "_new_careers", True)
        return (
            f"{host}/ms/candidatev2/main/careers/jobDetails/{native_id}"
            if new_careers
            else f"{host}/ms/candidate/careers/{native_id}"
        )

    def _alljobs(self, host: str, page: int) -> list[dict]:
        """POST one page of the board (retry — incl. the Cloudflare 403 blip — lives in fetch).

        Also stashes the envelope's ``job_counts`` on ``self._job_counts`` for `fetch_raw` to
        check pagination against once the loop ends — same ad hoc instance-attribute pattern as
        ``_host``/``_new_careers`` below, set here and read back after the call returns.
        """
        api = f"{host}{_LISTING_PATH}"
        body = {
            "companyId": "main",
            "page": page,
            "sort_option": "new",
            "limit": _PAGE_SIZE,
        }
        response = self._fetch(
            "POST",
            api,
            json=body,
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        self._job_counts = payload.get("job_counts")
        return payload.get("data") or []

    def _company_info(self, host: str) -> dict:
        """The tenant's ``companyinfo`` record, or ``{}`` when it cannot be read. It says whether
        the tenant runs the candidatev2 careers portal (``new_careers``) and names the company
        (``company_name``) — see :meth:`_read_company_info`."""
        try:
            response = self._fetch(
                "GET",
                f"{host}/ms/candidateapi/companyinfo?companyId=main",
                timeout=20,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            return (response.json().get("message") or {}).get("company") or {}
        except Exception:  # noqa: BLE001 - portal detection must never sink the board
            return {}

    def _read_company_info(self, company: dict) -> None:
        """Portal generation and company name from a ``companyinfo`` record. Every tenant surveyed
        (60/60 across the corpus, 2026-07-06) is on v2, so an unread record defaults to True; the
        flag exists so a legacy tenant still gets working links."""
        self._new_careers = bool(company.get("new_careers", True))
        self.adopt_company(company.get("company_name"))

    def _fetch_raw_browser(self, host: str) -> list[dict]:
        """The walled board through a real browser fetcher: navigate once, then in-page
        requests on the same warmed tab (ADR-0056, via the :attr:`_browser_fetcher` seam,
        ADR-0153).

        The wall admits a genuine browser and nothing else, and clearance is per-origin —
        every tenant is its own subdomain — so each board pays exactly one navigation, then
        pages the same JSON API the curl path uses. `parse` never knows the difference.
        """
        api = f"{host}{_LISTING_PATH}"
        body = {"companyId": "main", "sort_option": "new", "limit": _PAGE_SIZE}
        with self._browser_fetcher(f"{host}/ms/candidate/careers") as browser:
            response = browser.fetch("POST", api, json={**body, "page": 1})
            response.raise_for_status()
            payload = response.json()
            self._job_counts = payload.get("job_counts")
            batch = payload.get("data") or []
            jobs = list(batch)
            page = 1
            while len(batch) == _PAGE_SIZE and page < _MAX_PAGES:
                page += 1
                response = browser.fetch("POST", api, json={**body, "page": page})
                response.raise_for_status()
                batch = response.json().get("data") or []
                jobs.extend(batch)
            if len(batch) == _PAGE_SIZE:
                self.mark_truncated(  # same cap as `fetch_raw`'s curl loop below (ADR-0053)
                    f"hit the {_MAX_PAGES}-page cap at {len(jobs)} jobs — the rest unread"
                )
            elif self._job_counts and len(jobs) < self._job_counts:
                # Same `job_counts`-vs-collected check as the curl path's loop in `fetch_raw`
                # (see its docstring note there for the live evidence); here because a walled
                # Board never reaches that loop at all.
                self.mark_truncated_unless_negligible(
                    len(jobs),
                    self._job_counts,
                    f"job_counts={self._job_counts} but only {len(jobs)} read — the rest unread",
                )
            try:
                info = browser.fetch(
                    "GET", f"{host}/ms/candidateapi/companyinfo?companyId=main"
                )
                info.raise_for_status()
                company = (info.json().get("message") or {}).get("company") or {}
            except Exception:  # noqa: BLE001 - portal detection must never sink the board
                company = {}
            self._read_company_info(company)
        self._host = host
        return jobs

    def fetch_raw(self) -> Any:
        # data-center TLD varies per tenant; resolve it on the first page, then paginate.
        errors: list[tuple[str, Exception]] = []
        host = batch = None
        for tld in TLDS:
            candidate = self.host_on_tld(tld)
            try:
                batch = self._alljobs(candidate, 1)
                host = candidate
                break
            except Exception as exc:  # noqa: BLE001 - wrong-TLD host: try the other one
                errors.append((candidate, exc))
        if host is None:
            # A 403 is the wall on the tenant's real TLD — the wrong TLD answers 500 "Invalid
            # subdomain", never 403 — and the wall admits a real browser, so escalate rather
            # than report the board failed. (This is also why the 403 can never again be buried
            # by the wrong TLD's 500, the bug #137 fixed: it now routes before any reporting.)
            walled = next((h for h, e in errors if _is_wall(e)), None)
            if walled is not None:
                # The most expensive path any scraper takes — a real browser, for one Board —
                # and it was entered silently, so a run whose cost was dominated by escalations
                # looked identical to one where none fired.
                _log.info(
                    f"{self.board_key()}: walled on {walled}, escalating to a browser"
                )
                return self._fetch_raw_browser(walled)
            raise errors[-1][1]
        self._host = host
        self._read_company_info(self._company_info(host))
        jobs = list(batch)
        page = 1
        while len(batch) == _PAGE_SIZE and page < _MAX_PAGES:
            page += 1
            batch = self._alljobs(host, page)
            jobs.extend(batch)
        if len(batch) == _PAGE_SIZE:
            # Left on the page cap, not on a short page: there is more board behind it, so this
            # list is knowingly short and must say so or `index sync` evicts the rest (ADR-0053).
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(jobs)} jobs — the rest unread"
            )
        else:
            # A short PAGE ended the loop naturally, but the envelope's own `job_counts` says
            # the board isn't actually exhausted — live-confirmed real (2026-09-22, 10 Hiring
            # Boards through `browser_http`, module docstring update): stable across every page
            # of a walk including the terminal short one, and equal to the exact summed job
            # count on every board checked. `mark_truncated_unless_negligible` rather than a
            # flat `mark_truncated`: unlike the page cap above, this total is a real, measured
            # figure to tolerate a small gap against (ADR-0121), not an unreachable hard limit.
            # `getattr` because a test (or a curl call this pass didn't reach) may not have set
            # it, the same defensive read `_host`/`_new_careers` already use above.
            job_counts = getattr(self, "_job_counts", None)
            if job_counts and len(jobs) < job_counts:
                self.mark_truncated_unless_negligible(
                    len(jobs),
                    job_counts,
                    f"job_counts={job_counts} but only {len(jobs)} read — the rest unread",
                )
        return jobs

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            # `locations` is the board's display string, but it collapses to a generic
            # "Multiple Locations" when a job spans cities. In that case recover the real
            # city list from tool_tip_locations ("{office}, {city}, {state}, {country}").
            tips = j.get("tool_tip_locations") or []
            if len(tips) > 1:
                cities = []
                for tip in tips:
                    parts = [p.strip() for p in tip.split(",")]
                    city = parts[1] if len(parts) > 1 else parts[0]
                    if city and city not in cities:
                        cities.append(city)
                location = ", ".join(cities) or None
            else:
                # Comma-split and stripped, the same treatment the multi-location branch above
                # gives each tip: the raw `locations` string has shipped a literal embedded `\r`
                # right before its comma on some tenants ("...Maharashtra\r, India" — measured
                # 2026-08-24, 31/67 sampled jobs), which a bare `.strip()` on the whole string
                # would miss since it sits mid-string, not at an edge.
                raw_location = j.get("locations")
                location = (
                    ", ".join(
                        p for p in (s.strip() for s in raw_location.split(",")) if p
                    )
                    or None
                    if raw_location
                    else None
                )
            jobs.append(
                Job(
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or j.get("designation_name") or "").strip(),
                    location=location,
                    remote=bool(j.get("is_remote")) or is_remote(location),
                    department=j.get("department_name"),
                    url=self.job_url(j["id"]),
                    posted_at=_iso_date(j.get("posted_on")),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("jd")),
                    experience=j.get("experience"),
                    employment_type=j.get("emp_type_name"),
                    salary=self._salary_field(j),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """``Job.salary`` from Darwinbox's structured ``salary_min``/``salary_max``/
        ``salary_currency``/``salary_timeframe`` siblings, when populated, falling back to the
        pre-formatted ``salary_range`` otherwise.

        The structured fields are strictly more robust than re-parsing ``salary_range``: they are
        already-absolute numbers with no locale formatting to strip and no lakhs-vs-absolute
        magnitude ambiguity to resolve (unlike ``salary_range``'s free text, which is why
        ``salary.py``'s ``_field_darwinbox`` has to guess from magnitude at all) — and
        ``salary_currency`` names the tenant's real currency directly, where ``salary_range`` only
        ever carries "INR" (the field this scraper already reads is silently dropped for every
        other currency by ``_field_darwinbox``'s currency gate, a real gap this pairs with a
        ``salary.py`` fix for). Live sample, 75 tenants, 2026-09-15: 14 distinct currencies beyond
        INR (USD, EUR, GBP, CAD... down to CNY, MAD, KRW), and a real non-INR case
        (``transcarent``, "USD 20.00 - 20 (Hourly)") that the INR-only gate was dropping outright.

        ``salary_min``/``salary_max`` are sometimes both empty strings even when ``salary_range``
        and ``salary_currency`` are populated (e.g. an "INR 0+ (Annual)" placeholder on ``airtel``)
        — treated as no signal, matching how ``_field_darwinbox`` already declines that shape via
        ``salary_range``. A currency with no min/max at all falls back to ``salary_range`` too, in
        case that pre-formatted string still carries something (rare on live data, but the field
        this scraper already read is not switched off) — used as-is, without re-appending
        ``salary_timeframe``: ``salary_range`` already carries its own "(Annual)"/"(Monthly)" suffix
        whenever one exists (confirmed against every job in a 290-board sample, salary-extraction
        pass 2026-08-22: 1,874/1,874 suffixed values showed the identical suffix twice when
        ``salary_timeframe`` was also appended, and ``salary_timeframe`` was null in every case
        where ``salary_range`` had no suffix — "INR 3 - 5 (Annual) (Annual)", ADR-0019's own
        documented example)."""
        smin = (raw.get("salary_min") or "").strip()
        smax = (raw.get("salary_max") or "").strip()
        currency = (raw.get("salary_currency") or "").strip()
        if currency and (smin or smax):
            span = f"{smin}-{smax}" if smin and smax else (smin or smax)
            timeframe = (raw.get("salary_timeframe") or "").strip()
            return f"{currency} {span}" + (f" ({timeframe})" if timeframe else "")
        return (raw.get("salary_range") or "").strip() or None
