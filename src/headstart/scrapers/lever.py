"""Lever job-board scraper (api.lever.co, with EU-instance fallback).

Lever runs a global instance (api.lever.co) and a separate EU instance (api.eu.lever.co,
behind jobs.eu.lever.co). The company slug alone doesn't say which, so we try global first
and fall back to EU when the slug isn't found there.

The company name is the public board's ``<title>``, on the instance that answered: an EU Board's
page lives on ``jobs.eu.lever.co``, and asking ``jobs.lever.co`` for it 404s — which left every EU
Board on its slug (57 Boards, 841 rows among the affected ones, 2026-09-24). Where the board page
itself is disabled but a posting page still answers, that page's JSON-LD ``hiringOrganization``
names the company instead (`veeva`, 157 rows).

A Board can have both disabled while its API still lists postings: every link it would serve 404s,
and so does each posting's ``applyUrl``. :meth:`LeverScraper.fetch_raw` checks for that on every
scrape and serves nothing from such a Board, so its rows evict (ADR-0281; 53 Boards, 312 served rows
on 2026-09-29).
"""

from __future__ import annotations

import re
from typing import Any

from headstart.boards import company_name
from headstart.jobs import salary
from headstart.jobs.job import (
    Job,
    epoch_ms_to_iso,
    html_to_text,
    remote_from_workplace,
    requisition_of,
)
from headstart.network import http
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import BaseScraper, classify_exception
from headstart.scrapers.country_codes import ISO_ALPHA2_NAMES
from headstart.scrapers.job_posting_jsonld import find_job_posting, hiring_organization

#: Lever's two instances, global first — the order a scrape asks them in. Public: the liveness
#: probe asks the same two, starting from whichever the row's URL hints at (ADR-0203).
GLOBAL_API_HOST = "api.lever.co"
EU_API_HOST = "api.eu.lever.co"
API_HOSTS = (GLOBAL_API_HOST, EU_API_HOST)


def _already_names_country(composed_lower: str, code: str, name: str | None) -> bool:
    """Whether ``composed_lower`` already spells out this country — as a whole word, not a
    substring landing inside an unrelated one.

    A bare-substring check on a 2-letter code is unsound: ``"in"`` occurs inside ``"Beijing"``,
    so a raw ``code.lower() in composed_lower`` reads a Chennai/Beijing posting as already
    naming India and skips the append — the exact case this function exists to append *for*.
    Found live, review round 1: ``_location({"allLocations": ["Chennai", "Beijing"]}, "IN")``
    returned ``"Chennai, Beijing"`` with India never named. A full country name is safer as a
    substring (multi-word names rarely land inside another word by accident) but is checked the
    same way here for one rule rather than two.

    Known residual gap, found review round 2, not fixed: several ISO alpha-2 codes double as
    US state postal abbreviations this whole-word check can't distinguish from (CA/California,
    CO/Colorado, DE/Delaware, GA/Georgia, IN/Indiana, LA/Louisiana, MA/Massachusetts,
    MD/Maryland, PA/Pennsylvania, SC/South Carolina, SD/South Dakota, VA/Virginia, among
    others) — a posting whose ``country`` is e.g. "CO" (Colombia) with an unrelated
    "Denver, CO" entry elsewhere in ``allLocations`` would read the state tag as the country
    already being named and skip the append. Live-probed 2026-08-26 across 143 boards / 2,535
    postings for exactly this shape (country code present only inside a longer non-standalone
    entry): 0 hits. Left undocumented-but-live rather than restructured, given zero confirmed
    occurrences — a per-entry-exact-match rewrite would close it but is more invasive than this
    round's evidence justifies.
    """
    boundary = r"(?<![a-z]){}(?![a-z])"
    if re.search(boundary.format(re.escape(code.lower())), composed_lower):
        return True
    if name and re.search(boundary.format(re.escape(name.lower())), composed_lower):
        return True
    # "USA" is a common colloquial short form Lever locations use in place of the full
    # "United States" name; without this, e.g. "Select USA Remote Locations" (real freedompay
    # shape, live 2026-08-26) reads as not-yet-naming the US and gets a redundant ", US"
    # appended, defeating the no-duplicate-append purpose this function exists for.
    return code.upper() == "US" and bool(
        re.search(boundary.format("usa"), composed_lower)
    )


def _location(categories: dict, country: str | None) -> str | None:
    """Join every ``allLocations`` entry, then append the unread top-level ``country``.

    location-audit-2026-08-25/lever.md: ``categories.location`` is only ``allLocations[0]``
    (0 mismatches confirmed across 36,565 live records) — reading it alone silently drops
    every other entry, 7.96% of postings across 213 Boards, including 34 that hide an India
    location behind an unrelated kept location. ``country`` (ISO-2, 88.60% populated) is read
    nowhere despite being orthogonal to the string: 71.8% of the time its code doesn't appear
    in the composed location at all. Appended only when neither the code nor its full name is
    already present as a whole word, so it can't duplicate what's already spelled out.
    """
    parts = [p for p in (categories.get("allLocations") or []) if p]
    if not parts:
        loc = categories.get("location")
        parts = [loc] if loc else []
    composed = ", ".join(parts)
    if country:
        # the name only tells whether the location already spells this country out
        name = ISO_ALPHA2_NAMES.get(country.upper())
        if not _already_names_country(composed.lower(), country, name):
            composed = f"{composed}, {country}" if composed else country
    return composed or None


def _description(j: dict) -> str | None:
    """The full posting text: intro + the lists sections (Requirements etc.) + closing.

    ``descriptionPlain`` alone is just the intro — the years-of-experience requirements
    almost always live in ``lists``, so dropping them starves experience extraction and
    the embedding.
    """
    parts = [j.get("descriptionPlain") or j.get("description")]
    for lst in j.get("lists") or []:
        section = "\n".join(s for s in (lst.get("text"), lst.get("content")) if s)
        if section:
            parts.append(section)
    parts.append(j.get("additionalPlain") or j.get("additional"))
    return html_to_text("\n".join(p for p in parts if p))


class LeverScraper(BaseScraper):
    ats = "lever"
    url_shape = r"https://jobs(\.eu)?\.lever\.co/[^/]+/[0-9a-f-]{36}"
    #: The API host that answered `fetch_raw`, which says which instance the Board lives on.
    _api_host = GLOBAL_API_HOST
    #: One posting's hosted page, for a Board whose board page is disabled (module docstring).
    _first_posting: str | None = None

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        # The hosted pages `_fetch_once` has answered this scrape, by URL.
        self._pages: dict[str, Any] = {}

    def url(self) -> str:
        return self.listing_url_on(GLOBAL_API_HOST)

    def listing_url_on(self, api_host: str) -> str:
        """This Board's postings on one of :data:`API_HOSTS`; the slug does not say which."""
        return f"https://{api_host}/v0/postings/{self.slug}?mode=json"

    def job_url(self, url: str) -> str:
        """Lever's postings API states the job's own link directly (``hostedUrl``); nothing to
        build, so this simply names that as the declared source (ADR-0153)."""
        return url

    def board_page(self) -> str:
        """The public board, whose ``<title>`` is the company name with no wrapper at all.

        The postings API carries no company name — its keys are the posting's own fields and
        nothing else — so this is the only place Lever states it (`headstart.boards.company_name`).
        On the instance the listing answered from: an EU Board's page is on ``jobs.eu.lever.co``."""
        host = "jobs.eu.lever.co" if self._api_host == EU_API_HOST else "jobs.lever.co"
        return f"https://{host}/{self.slug}"

    def company_from_page(self, page: str | None) -> str | None:
        """The board title; else, when the board page did not answer at all, the
        ``hiringOrganization`` of one posting page (module docstring). A board page that answered
        with a title the guards refuse is not second-guessed from a posting."""
        if page is not None or not self._first_posting:
            return super().company_from_page(page)
        try:
            response = self._fetch_once("GET", self._first_posting)
        except http.RequestsError as exc:
            # Said here: base's own line after this names only the board page's answer.
            self._log.info(
                f"{self.board_key()}: no company name — posting fallback raised "
                f"{classify_exception(exc)}"
            )
            return None
        posting = (
            find_job_posting(response.text) if response.status_code == 200 else None
        )
        return company_name.from_field(
            self.ats, hiring_organization((posting or {}).get("hiringOrganization"))
        )

    def _fetch_once(self, method: str, url: str, **kwargs: Any) -> Any:
        """Base's one-attempt fetch, asked once per URL: :meth:`hosted_pages_disabled` and the
        company-name read ask for the same board page and posting page, so the second asker reads
        the first one's answer rather than spending a request. A request that raised is not kept."""
        if url not in self._pages:
            self._pages[url] = super()._fetch_once(method, url, **kwargs)
        return self._pages[url]

    def hosted_pages_disabled(self) -> bool:
        """Whether this Board's hosted pages are switched off: its board page answers 404, and so
        does one listed posting's ``hostedUrl`` (ADR-0281).

        The posting is asked only when the board page 404s, because `veeva`'s board page 404s while
        its postings answer. Only a 404 counts: a 5xx, a 429 or a request that raises reads as
        enabled, so a transient failure never empties the Board.
        """
        for page in (self.board_page(), self._first_posting):
            if not page:
                return False
            try:
                if self._fetch_once("GET", page).status_code != 404:
                    return False
            except http.RequestsError:
                return False
        return True

    def fetch_raw(self) -> Any:
        # try the global instance, then EU; a 404 on both means the company isn't on Lever —
        # which must RAISE, not read as an empty board: swallowing it left dead boards
        # "alive with zero jobs" forever, invisible to the ADR-0058 quarantine.
        for api_host in API_HOSTS:
            response = self._fetch("GET", self.listing_url_on(api_host))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            self._api_host = api_host
            postings = response.json()
            self._first_posting = next(
                (p.get("hostedUrl") for p in postings if p.get("hostedUrl")), None
            )
            if postings and self.hosted_pages_disabled():
                # `[]`, not a raise: the listing answered, so the Board stays in the eviction
                # scope and its rows evict after ADR-0083's two consecutive absences (ADR-0200).
                # Checked every scrape, so a Board that turns its pages back on is served again.
                self._log.info(
                    f"{self.board_key()}: hosted pages disabled — {self.board_page()} and "
                    f"{self._first_posting} both answered 404, so none of its "
                    f"{len(postings)} listed posting(s) has a working link; serving none "
                    "(ADR-0281)"
                )
                return []
            return postings
        # Both instances 404: the company is not on Lever. Raised in the shape
        # `board_failures.is_gone` matches, rather than left to curl_cffi's message wording.
        raise http.RequestsError(f"HTTP Error 404: no Lever board for {self.slug}")

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            categories = j.get("categories") or {}
            location = _location(categories, j.get("country"))
            # Hybrid is None (`remote_from_workplace`). Measured live 2026-09-28: 40 random
            # Boards, 1,081 postings, 420 hybrid, all served False before.
            remote = remote_from_workplace(j.get("workplaceType"), location)
            jobs.append(
                Job(
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("text") or "").strip(),
                    location=location,
                    remote=remote,
                    department=categories.get("department") or categories.get("team"),
                    url=self.job_url(j.get("hostedUrl", "")),
                    posted_at=epoch_ms_to_iso(j.get("createdAt")),
                    scraped_at=scraped_at,
                    description=_description(j),
                    employment_type=categories.get("commitment"),
                    salary=self._salary_field(j.get("salaryRange")),
                    # What an Eightfold site in front of this Board states as `atsJobId`
                    # (ADR-0210).
                    requisition=requisition_of(j["id"]),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format Lever's structured salaryRange, e.g. '50000-70000 USD per-year-salary'."""
        raw = raw or {}
        lo, hi = raw.get("min"), raw.get("max")
        if not lo and not hi:
            return None
        return salary.to_field(
            lo or hi,
            hi if lo and hi else None,
            raw.get("currency"),
            raw.get("interval"),
        )
