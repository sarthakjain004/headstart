"""Darwinbox careers scraper ({tenant}.darwinbox.{in,com} candidate career site).

Darwinbox is an Angular SPA whose public board loads from one unauthenticated JSON
endpoint (the documented Bulk-Candidates API needs Basic Auth + an API key, but the
careers SPA itself does not):

  POST /ms/candidateapi/job/alljobs?companyId=main
       body {"companyId":"main","page":N,"sort_option":"new","limit":100}
       -> {"status":"success","data":[ ...jobs... ]}

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
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_PAGE_SIZE = 100  # server caps each page at 100 regardless of the requested limit
_MAX_PAGES = (
    99  # our own ceiling, not the server's — reaching it means the board went unread
)
_TLDS = ("in", "com")


def _iso_date(raw: str | float | None) -> str | None:
    """Darwinbox posts dates as '21-Apr-2026' — normalize to ISO. Non-ISO strings sort
    lexicographically ABOVE ISO date cutoffs, so left raw they leak through every
    posted-within filter; unparseable values pass through unchanged.

    Some tenants (e.g. orangehealth) send ``posted_on`` as an epoch int instead of the string —
    that raised an uncaught TypeError in ``strptime`` and dropped the *entire* board. Read an int
    as an epoch (ms if it looks like ms, else seconds); anything unreadable yields None (unknown
    date — excluded from posted-within windows) rather than crashing or leaking a garbage value."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            seconds = raw / 1000 if raw > 1e11 else raw
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

    def url(self) -> str:
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        return f"{host}/ms/candidate/careers"

    def _alljobs(self, host: str, page: int) -> list[dict]:
        """POST one page of the board (retry — incl. the Cloudflare 403 blip — lives in fetch)."""
        api = f"{host}/ms/candidateapi/job/alljobs?companyId=main"
        body = {
            "companyId": "main",
            "page": page,
            "sort_option": "new",
            "limit": _PAGE_SIZE,
        }
        response = http.fetch(
            "POST",
            api,
            json=body,
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json().get("data") or []

    def _portal_is_v2(self, host: str) -> bool:
        """Whether the tenant runs the candidatev2 careers portal (companyinfo.new_careers).

        Every tenant surveyed (60/60 across the corpus, 2026-07-06) is on v2, so failures
        default to True; the flag exists so a legacy tenant still gets working links."""
        try:
            response = http.fetch(
                "GET",
                f"{host}/ms/candidateapi/companyinfo?companyId=main",
                timeout=20,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            company = (response.json().get("message") or {}).get("company") or {}
            return bool(company.get("new_careers", True))
        except Exception:  # noqa: BLE001 - portal detection must never sink the board
            return True

    def _fetch_raw_browser(self, host: str) -> list[dict]:
        """The walled board through a real Chrome: navigate once, then in-page fetches.

        The wall admits a genuine browser and nothing else, and clearance is per-origin —
        every tenant is its own subdomain — so each board pays exactly one navigation, then
        pages the same JSON API the curl path uses. `parse` never knows the difference.
        """
        from headstart import (
            browser_http,
        )  # lazy: pydoll is only needed when a wall is hit

        api = "/ms/candidateapi/job/alljobs?companyId=main"
        body = {"companyId": "main", "sort_option": "new", "limit": _PAGE_SIZE}
        with browser_http.origin(f"{host}/ms/candidate/careers") as page_ctx:
            batch = page_ctx.post_json(api, {**body, "page": 1}).get("data") or []
            jobs = list(batch)
            page = 1
            while len(batch) == _PAGE_SIZE and page < _MAX_PAGES:
                page += 1
                batch = (
                    page_ctx.post_json(api, {**body, "page": page}).get("data") or []
                )
                jobs.extend(batch)
            if len(batch) == _PAGE_SIZE:
                self.mark_truncated(  # same cap as `fetch_raw`'s curl loop below (ADR-0053)
                    f"hit the {_MAX_PAGES}-page cap at {len(jobs)} jobs — the rest unread"
                )
            try:
                info = page_ctx.get_json("/ms/candidateapi/companyinfo?companyId=main")
                company = (info.get("message") or {}).get("company") or {}
                self._new_careers = bool(company.get("new_careers", True))
            except Exception:  # noqa: BLE001 - portal detection must never sink the board
                self._new_careers = True
        self._host = host
        return jobs

    def fetch_raw(self) -> Any:
        # data-center TLD varies per tenant; resolve it on the first page, then paginate.
        errors: list[tuple[str, Exception]] = []
        host = batch = None
        for tld in _TLDS:
            candidate = f"https://{self.slug}.darwinbox.{tld}"
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
                return self._fetch_raw_browser(walled)
            raise errors[-1][1]
        self._host = host
        self._new_careers = self._portal_is_v2(host)
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
        return jobs

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        new_careers = getattr(self, "_new_careers", True)
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
            # `salary_range` already carries its own "(Annual)"/"(Monthly)" suffix whenever one
            # exists — confirmed against every job in a 290-board sample (salary-extraction pass,
            # 2026-08-22): 1,874/1,874 suffixed values showed the identical suffix twice when
            # `salary_timeframe` was also appended, and `salary_timeframe` was null in every case
            # where `salary_range` had no suffix. Appending it was pure duplication ("INR 3 - 5
            # (Annual) (Annual)", ADR-0019's own documented example), never a source of new info.
            salary = (j.get("salary_range") or "").strip() or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or j.get("designation_name") or "").strip(),
                    location=location,
                    remote=bool(j.get("is_remote")) or is_remote(location),
                    department=j.get("department_name"),
                    # v2 portal (the norm): browser-verified jobDetails route. On v2
                    # tenants the old /ms/candidate/ app is a 2.4KB stub that redirects
                    # to the v2 careers HOME, dropping the job — hence the branch. The
                    # legacy fallback is the old app's careers/:id router entry.
                    url=(
                        f"{host}/ms/candidatev2/main/careers/jobDetails/{j['id']}"
                        if new_careers
                        else f"{host}/ms/candidate/careers/{j['id']}"
                    ),
                    posted_at=_iso_date(j.get("posted_on")),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("jd")),
                    experience=j.get("experience"),
                    employment_type=j.get("emp_type_name"),
                    salary=salary,
                )
            )
        return jobs
