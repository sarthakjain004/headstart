"""Uber careers scraper (jobs.uber.com) — a Single source scraper, not a multi-tenant platform.

Uber runs its own in-house careers system: one board, forever, never a second tenant. ADR-0139
names that shape a Single source scraper and models it as its own ``ats`` value with a fixed,
non-discovered ``slug`` rather than a slug under some shared platform — this scraper is the first
of the eight boards that ADR names.

**The public site is a Cloudflare-challenged SPA, but the JSON it calls is not.** ``GET
https://jobs.uber.com/`` 301s to ``/en/jobs/``; a bare request to the listing API
(``/api/jobs/search/``) answers Cloudflare's own "Just a moment..." challenge page (HTTP 403,
``cf-mitigated: challenge``) to a stock ``curl``/``python-requests`` client. It does **not** answer
that way to this repo's own ``headstart.http.fetch`` — that module already runs every request
through a ``curl_cffi`` session with ``impersonate="chrome"`` (its own module docstring: "Chrome
impersonation lets the same client handle both plain JSON APIs and the TLS-fingerprinted boards").
Verified live 2026-09-11 with the plain :meth:`BaseScraper._get` (``headstart/0.1`` UA, no Referer,
no cookie jar): 200 every time. So this scraper needs no bespoke transport, unlike the sibling
``kalil0321/ats-scrapers`` project's Uber scraper, which reaches for a dedicated TLS-impersonating
"httpcloak" client to clear the same wall — this repo's shared HTTP layer already clears it for
free. ``robots.txt`` allows everything (``User-Agent: * / Allow: /``) and even names a
``Sitemap:``, so the API is not something the site is trying to keep out.

**One JSON call, no detail pass.** ``GET /api/jobs/search/?page={n}&pagesize={m}`` returns
``{jobs: [...], totalJobs, totalPages, page, pageSize}``; every field this scraper uses — title,
location, department, dates, employment type, and the **full HTML description** — is on the
listing row. Verified 2026-09-11 on 3 of 3 sampled postings: the listing's ``Description`` is
byte-identical to the ``description`` in the per-job page's own JSON-LD ``JobPosting`` block, so a
second fetch per job would buy nothing. Hence ``has_detail_pass = False``.

**Pagination is clean and the API states no cap.** A full sweep found 517 open postings, paging
through matched ``totalJobs`` exactly with zero duplicate ids (measured twice, once at
``pagesize=100`` and once at ``pagesize=200``). ``pagesize`` was tried up to 100,000 with no
server-side clamp (it just echoes back what was asked and returns everything there is), and a
page past the end (``page=999``) answers ``{"jobs": [], "totalJobs": 517, ...}`` — an empty batch,
not a zeroed envelope (contrast Oracle's `_OFFSET_CEILING`, which blanks the whole response past
its cap) — so ``totalJobs`` is a safe, stable terminator and a fixed page size is used rather than
one giant request that would silently break if the API ever does start clamping.

**No rate limit found.** 18 requests at ~13 req/s, all HTTP 200 — a small sample (CLAUDE.md's own
bar), not a guarantee, but nothing here suggests pacing is needed.

**Three fields are worth flagging rather than silently trusting.** ``Remote`` is a real boolean on
every row (never null) but reads ``False`` on **all 517** sampled postings — plausible given Uber's
stated hybrid/RTO policy (several descriptions state a 50%-in-office minimum) rather than a broken
field, so it is read as stated rather than overridden by a location-text guess.
``Salary.MinValue``/``MaxValue``/``Currency``/``Period`` are null on all 517 rows; ``Salary
.Description`` (present on 250/517) is prose that duplicates text already inside the main
``Description`` on the samples checked, so no structured salary is extracted here — Job.salary
stays None and the downstream ``salary.extract()`` pass reads it from the description text like
every other ATS. ``DisplayDate`` looked suspicious enough to check directly (ADR text elsewhere in
this repo has caught fabricated dates before): held stable across a 4-second re-fetch and spans
2026-06-19 to 2026-09-11 across the 517 rows sampled, so it reads as a genuine stored timestamp,
not a request-time computation.

A fourth field, ``employment_type``, needed both its source columns rather than one: an Intern
posting states ``ContractType="Full time"`` (hours) **and** ``WorkPattern="Intern"`` (arrangement)
at once, so picking either alone silently drops the other's signal — see ``_employment_type``.

Full measurement notes: ``docs/uber/2026-09-11_api-measurement.md``.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper

_JOBS_ORIGIN = "https://jobs.uber.com"
_API = f"{_JOBS_ORIGIN}/api/jobs/search/"
#: Measured with no server-side clamp up to 100,000 (module docstring); kept modest and paginated
#: rather than requested as one giant page, so a future clamp degrades to more pages instead of a
#: silent partial read.
_PAGE_SIZE = 200
#: Our own ceiling. 517 measured postings need 3 pages at this size; reaching this means the board
#: went unread, not that it ended.
_MAX_PAGES = 100


class UberScraper(BaseScraper):
    """Uber careers scraper — ``slug`` is the board's own host, fixed by ADR-0139, never derived."""

    ats = "uber"

    def __init__(self, slug: str, company: str | None = None) -> None:
        # `company` is ignored on purpose: there is exactly one board and its name is always
        # "Uber", so there is nothing to resolve the way every multi-tenant ATS's slug-as-name
        # placeholder needs `resolve_company` for.
        super().__init__(slug, "Uber")

    def alias_key(self) -> str | None:
        """No sibling board exists to alias against — a Single source scraper's Board resolves to itself
        (ADR-0139's per-scraper judgment call; the base default's redirect-following assumes a
        platform with vanity-hostname proliferation, which does not apply here)."""
        return self.slug

    def url(self) -> str:
        return f"{_API}?page=1&pagesize={_PAGE_SIZE}"

    def fetch_raw(self) -> Any:
        seen: dict[str, dict] = {}
        total = 0
        page = 1
        for _ in range(_MAX_PAGES):
            data = json.loads(self._get(f"{_API}?page={page}&pagesize={_PAGE_SIZE}"))
            total = data.get("totalJobs") or total
            batch = data.get("jobs") or []
            if not batch:
                break
            for item in batch:
                native_id = item.get("Id")
                if native_id:
                    seen.setdefault(str(native_id), item)
            page += 1
            if total and len(seen) >= total:
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(seen)} of {total or 'unknown'} "
                "jobs — the rest unread"
            )
        if total and len(seen) < total:
            self.mark_truncated_unless_negligible(
                len(seen),
                total,
                f"read {len(seen)} of {total} jobs — the rest is unread, not absent",
            )
        return list(seen.values())

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            native_id = str(item.get("Id") or "").strip()
            title = (item.get("Title") or "").strip()
            if not native_id or not title:
                continue
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{native_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=_location(item.get("Locations")),
                    # A real, always-present boolean (module docstring) — read as stated rather
                    # than guessed from location text.
                    remote=bool(item.get("Remote")),
                    department=_department(item.get("Teams")),
                    url=_job_url(item.get("Urls")),
                    posted_at=item.get("DisplayDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(item.get("Description")),
                    employment_type=_employment_type(item),
                )
            )
        return jobs


def _location(locations: Any) -> str | None:
    """ "City, Region, Country" of the first entry — a job with several offices lists them all,
    and the first is a representative one, the same tier `eightfold`/`oracle` take."""
    if not isinstance(locations, list) or not locations:
        return None
    first = locations[0]
    if not isinstance(first, dict):
        return None
    parts: list[str] = []
    for key in ("City", "Region", "Country"):
        value = str(first.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or None


def _department(teams: Any) -> str | None:
    if isinstance(teams, list) and teams:
        value = str(teams[0]).strip()
        return value or None
    return None


def _job_url(urls: Any) -> str:
    """The posting's own page, from the ``IsDefault`` entry in ``Urls`` (falling back to the
    first entry) — every sampled row carries at least one, always relative to `_JOBS_ORIGIN`."""
    if isinstance(urls, list):
        default = next(
            (
                u
                for u in urls
                if isinstance(u, dict) and u.get("IsDefault") and u.get("Url")
            ),
            None,
        )
        entry = default or next(
            (u for u in urls if isinstance(u, dict) and u.get("Url")), None
        )
        if entry:
            return urllib.parse.urljoin(_JOBS_ORIGIN, entry["Url"])
    return _JOBS_ORIGIN


def _employment_type(item: dict) -> str | None:
    """``ContractType`` ("Full time" on 504/517 sampled) states hours; ``WorkPattern``
    ("Regular"/"Intern"/"Fixed Term") states the arrangement — and an Intern posting states
    **both** (measured: id 300864 carries ``ContractType="Full time"``, ``WorkPattern="Intern"``
    at once), so preferring either alone drops the other's signal. Joined the same way
    ``personio.py`` combines its own two employment fields, not picked one-or-the-other."""
    contract = str(item.get("ContractType") or "").strip()
    pattern = str(item.get("WorkPattern") or "").strip()
    if contract and pattern and contract != pattern:
        return f"{contract} / {pattern}"
    return contract or pattern or None
