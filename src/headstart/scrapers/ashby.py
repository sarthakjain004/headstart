"""Ashby job-board scraper (api.ashbyhq.com).

The company name is the public board's ``<title>``, "{Name} Jobs". A Board that hides its job
page titles it "Jobs" alone (82 of 221 affected Boards, 2026-09-24), and for those the
hosted-page app's own GraphQL lookup, ``organizationFromHostedJobsPageName``, names the
organization: 24 of the 25 such Boards still listing postings. It agreed with the title on 60 of
60 control Boards. That endpoint rate-limits a burst with 429 and ``Retry-After``, so lookups are
spaced one second apart process-wide and a refusal rests them all.
"""

from __future__ import annotations

import re
from typing import Any

from headstart import company_name, http, salary
from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.pacer import Pacer

_GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName"
#: The hosted-page app's own query, with the optional ``searchContext`` left out.
_ORGANIZATION_QUERY = (
    "query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!) {"
    " organization: organizationFromHostedJobsPageName("
    "organizationHostedJobsPageName: $organizationHostedJobsPageName) { name } }"
)
_GRAPHQL_PACER = Pacer(1.0)
#: A 429 is waited out and asked again this many times in all.
_GRAPHQL_TRIES = 3
_GRAPHQL_REST_S = 5.0


def _remote(job: dict) -> bool | None:
    """Whether the posting is remote — from ``workplaceType``, not ``isRemote``.

    ``isRemote`` is exactly ``workplaceType != "OnSite"``, so it reports **Hybrid as remote**.
    Measured 2026-08-25 over 16,138 live Jobs: OnSite/false 4,902, Remote/true 4,753,
    **Hybrid/true 4,183** — a quarter of ashby's Jobs served ``remote=True`` when ashby itself
    says Hybrid, which a user filtering for remote work sees as a wrong result.

    ``Job.remote`` is tri-state on purpose and hybrid is the case ``None`` exists for: it is
    neither remote nor on-site, and claiming either is a guess. That is already how
    ``workday._remote_from`` answers it (``if "hybrid" in norm: return None``); this brings
    ashby into line rather than inventing a convention.
    """
    workplace = job.get("workplaceType")
    if isinstance(workplace, str) and workplace.strip():
        norm = workplace.strip().lower()
        if norm == "remote":
            return True
        if norm == "onsite":
            return False
        return None  # Hybrid, and anything else ashby adds later
    flag = job.get("isRemote")
    return flag if isinstance(flag, bool) else None


def _place_names(entry: dict) -> list[str]:
    """Every distinct place name one location entry carries, headline string first.

    Ashby gives each entry a human ``location`` *and* a structured
    ``address.postalAddress``; both are worth having, because the human one is the employer's
    own wording ("SpotDraft HQ, Bengaluru") and the structured one is the part a place filter
    can actually match.
    """
    names = []
    headline = (entry.get("location") or "").strip()
    if headline:
        names.append(headline)
    postal = (entry.get("address") or {}).get("postalAddress") or {}
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = (
            postal.get(key) or ""
        ).strip()  # some tenants ship "Guatemala " with the space
        if value:
            names.append(value)
    return names


def _already_kept(name_lower: str, kept: list[str]) -> bool:
    """Whether ``name_lower`` is already named, as a whole word/phrase, somewhere in ``kept``.

    A raw substring check is unsound: ``"ca"`` (the ``addressRegion`` code for California) is a
    literal substring of ``"Vacaville"``, so ``"ca" in "vacaville"`` reads the code as already
    present and silently drops it — found live, code review round 1:
    ``_location({"location": "Vacaville", "address": {"postalAddress": {"addressRegion": "CA",
    ...}}})`` composed ``"Vacaville, United States"`` with the state dropped. Same fix as
    lever's ``_already_names_country`` (PR #299): require non-letter boundaries around the
    match, not just containment anywhere.
    """
    boundary = r"(?<![a-z]){}(?![a-z])"
    pattern = boundary.format(re.escape(name_lower))
    return any(re.search(pattern, k.lower()) for k in kept)


def _location(job: dict) -> str | None:
    """Every place the posting names, not just its headline string.

    The served location *is* the filter substrate — ``geo.where()`` matches substrings of it
    (ADR-0024) — so a place absent from this string is
    unfilterable, however well the record knows it. Measured 2026-08-25 over 884 live Boards /
    16,138 Jobs: **79.43% ship a location omitting a populated component of their own
    ``address.postalAddress``, and 69.55% carry no country at all** — every one of the 1,057
    ``"San Francisco"`` rows has ``addressCountry: "United States"`` that never reached the
    served row. 15.83% omit the *city*, which is the worse direction (``"Israel"`` where the
    record says Tel Aviv). Separately 17.50% carry a ``secondaryLocations[]`` nothing ever
    opened — 1,295 of those name a *different country* than the primary, so a genuinely
    multi-country posting is served as a single-country row and cannot be found by its second
    country at all.

    Additive rather than replacing: a component is appended only when it is not already named,
    as a whole word, in a string kept so far — so the employer's own wording survives and a
    substring filter that worked before still works. That whole-word test is the right one
    precisely because the filter is a substring match — "Panama" needs no separate entry beside
    "Panama City", but "India" does beside "Bengaluru", and "CA" does beside "Vacaville" (a bare
    substring check would wrongly treat "ca" as already present inside "vacaville").
    """
    names: list[str] = []
    for entry in (job, *(job.get("secondaryLocations") or [])):
        for name in _place_names(entry):
            if not _already_kept(name.lower(), names):
                names.append(name)
    return ", ".join(names) or None


class AshbyScraper(BaseScraper):
    ats = "ashby"
    url_shape = r"https://jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{36}"

    def url(self) -> str:
        # includeCompensation adds the structured compensation block to each posting
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}?includeCompensation=true"

    def job_url(self, url: str) -> str:
        """Ashby's posting API states the job's own link directly (``jobUrl``); nothing to
        build, so this simply names that as the declared source (ADR-0153)."""
        return url

    def board_page(self) -> str:
        """The public board, whose ``<title>`` is ``"{Name} Jobs"``.

        The posting API returns only ``apiVersion`` and ``jobs``, and a job carries no company
        field either; a Board that hides this page is named by the GraphQL lookup instead
        (:meth:`company_from_page`)."""
        return f"https://jobs.ashbyhq.com/{self.slug}"

    #: Process-wide, shared by every instance (module docstring).
    graphql_pacer = _GRAPHQL_PACER

    def company_from_page(self, page: str | None) -> str | None:
        """The board title, else the organization the GraphQL lookup names (module docstring).

        ``ashby:graphql`` has no vendor alias on purpose: this is the organization record, not a
        page that falls back to the vendor's branding, and Ashby hires on Ashby (`ashby:ashby`,
        whose title "Ashby Jobs" the title guard refuses)."""
        return super().company_from_page(page) or company_name.from_field(
            "ashby:graphql", self._graphql_organization()
        )

    def _graphql_organization(self) -> str | None:
        body = {
            "operationName": "ApiOrganizationFromHostedJobsPageName",
            "variables": {"organizationHostedJobsPageName": self.slug},
            "query": _ORGANIZATION_QUERY,
        }
        for _ in range(_GRAPHQL_TRIES):
            self.graphql_pacer.wait()
            try:
                response = self._fetch_once(
                    "POST", _GRAPHQL, accept="application/json", json=body
                )
            except http.RequestsError:
                return None
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after") or ""
                rest_s = (
                    float(retry_after) if retry_after.isdigit() else _GRAPHQL_REST_S
                )
                self._log.info(
                    f"{self.board_key()}: 429 on {_GRAPHQL} — resting every Ashby GraphQL "
                    f"request {rest_s:.0f}s"
                )
                self.graphql_pacer.rest(rest_s)
                continue
            if response.status_code != 200:
                return None
            try:
                organization = response.json()["data"]["organization"]["name"]
            except (ValueError, KeyError, TypeError):  # not the answer's shape: no name
                return None
            return organization if isinstance(organization, str) else None
        return None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        listed = raw.get("jobs")
        if listed is None and "jobs" not in raw:
            # Only an *absent* container answers here. A ``jobs`` that is present but not a
            # list falls through and raises, as it did before this guard existed: a loud
            # Board error keeps the Board out of ADR-0053's eviction scope, where a quiet
            # `[]` would land it in `boards_ok` and evict its rows two runs later — the
            # failure this line exists to report, arriving by the path that reports it.
            # A board with nothing open still answers `{"jobs": []}`, so a payload carrying no
            # `jobs` at all was not *read* — and downstream that is the same zero as an empty
            # board, which is what makes it worth a line (`note_unreadable_board`). Deliberately
            # not marked truncated: what a container-less payload means on this API has not been
            # measured, and ADR-0053's exclusion has no drain, so guessing wrong holds a departed
            # tenant's rows in the index for as long as its slug stays in the ledger.
            self.note_unreadable_board("a payload with a `jobs` list", "no `jobs` key")
            return []
        jobs: list[Job] = []
        for j in listed:
            if not j.get("isListed", True):
                continue  # skip postings the company has unlisted
            jobs.append(
                Job(
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=_location(j),
                    remote=_remote(j),
                    department=j.get("department"),
                    url=self.job_url(j.get("jobUrl", "")),
                    posted_at=j.get("publishedAt"),
                    scraped_at=scraped_at,
                    description=html_to_text(
                        j.get("descriptionPlain") or j.get("descriptionHtml")
                    ),
                    employment_type=j.get("employmentType"),
                    salary=self._salary_field(j.get("compensation")),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """A real, structured Salary-typed component from ``compensationTiers[].components[]``,
        formatted as "50000-70000 USD 1 YEAR" — the same RANGE + CODE + interval shape lever/
        recruitee/teamtailor already produce (``_field_range_currency_interval`` in salary.py) —
        not ``compensationTierSummary`` (the human-formatted "$80K – $100K • Offers Bonus" string
        this scraper used before). Found via direct API inspection (2026-08-22): 34% of jobs have
        a populated Salary component, close to 4x teamtailor's field-presence rate, and fixing
        this at the source avoids re-parsing a summary string that mixes in bonus/equity language
        the structured data already keeps separate (``compensationType`` "Bonus"/"Commission"/
        "EquityPercentage"/"EquityCashValue", all skipped here).

        A "1 TIME" interval (confirmed real: "Compensation per finished project", an onboarding
        rate) is a one-off payment, not a recurring salary — deliberately excluded rather than
        guessed at as if it were annual, the same no-fabrication principle every other Tier-1
        field parser follows. Zero real tiers had more than one Salary component when checked; a
        job with several compensation tiers (13/1,972 in the same sample) takes the first tier
        with a usable one.

        ``lo`` and ``hi`` are checked with ``is not None``, not truthiness — Ramp's own board has
        a real job with ``minValue=0, maxValue=250000`` (a code-review catch, live-reconfirmed
        2026-08-22); a truthy check drops the 0 and silently inverts the disclosure into "$250k+,
        no ceiling" instead of the true "$0-$250k". Fixed, the pair now correctly reaches
        ``_bounded`` as (0, 250000), which declines it (0 is below the $10k USD floor) rather than
        reporting either wrong value — a correct decline, not a corrected extraction. The mirror
        shape, ``hi`` set and ``lo`` unset (a ceiling-only "up to $X" tier with no stated floor),
        was checked directly against live data alongside this fix — 0/820 real Salary components
        across 4 boards — and is deliberately left on the existing bare-single-value path (which
        reads as floor-only) rather than special-cased for a shape not yet observed in practice."""
        for tier in (raw or {}).get("compensationTiers") or []:
            for c in tier.get("components") or []:
                if c.get("compensationType") != "Salary":
                    continue
                lo, hi = c.get("minValue"), c.get("maxValue")
                if lo is None and hi is None:
                    continue
                interval = c.get("interval")
                if interval == "1 TIME":
                    continue
                return salary.to_field(
                    lo if lo is not None else hi,
                    hi if lo is not None else None,
                    c.get("currencyCode"),
                    interval,
                )
        return None
