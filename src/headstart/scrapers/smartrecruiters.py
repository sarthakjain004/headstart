"""SmartRecruiters job-board scraper (api.smartrecruiters.com posting API).

Adapted from jobhive's SmartRecruiters scraper (kalil0321/ats-scrapers, MIT) to this
project's BaseScraper contract:
    https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100[&offset=N]

`limit` is our page size, not the provider's ceiling (ADR-0070) — the listing pages by
`offset`, and `totalFound` reports the board's true size. `_MAX_PAGES` bounds what one board
can cost a shard on its first, uncosted run — the run ADR-0064's tech-per-minute gate cannot
see, because it only judges a board that already has a measurement (ADR-0077) — but its
enforcement is commented out below for the initial rollout: shipping uncapped on purpose, to
measure real cost/impact across a few pipeline runs before deciding a cap from data rather
than from #202's projection a second time (#227). Trivially reversible — restore the two
commented-out conditions in `fetch_raw` (the loop's cap check, and the cap-naming branch of
its truncation message) to re-enable the 50-page cap.

The postings list has no description; a second pass fetches each posting's detail
(GET .../postings/{id} -> jobAd.sections.jobDescription.text) in a bounded thread pool to
fill it in. That same detail response also carries a native `compensation.{min,max,currency,
period}` block (10.48% of postings, previously unread — see `_salary()`), so one fetch now
feeds both `description` and `salary`. A failed detail fetch leaves both None — the job is
still kept.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_DETAIL_WORKERS = 8
_PAGE_SIZE = 100  # our page size, not the provider's ceiling (ADR-0070)
# Our own ceiling, sized by cost rather than by tech density — because density does not fall off
# down the list. Measured live: 14.1% tech at offset 500 across 40 random boards over 500 postings,
# and 6 of the 15 boards over 3,000 run 14-62% tech at *half* and *end* of board. 5,000 postings is
# the most this scraper can read and still stay under ADR-0064's 15-minute gate floor at the slow
# end of fleet throughput; what stays truncated above it is ~0%-tech retail the gate handles.
# NOT ENFORCED right now (#227) — kept defined so re-enabling is a two-line uncomment, not a
# re-derivation.
_MAX_PAGES = 50

# Some companies configure a free-text custom field for pay info (real, found via direct API
# inspection during the salary-extraction pass, 2026-08-22: "Enter salary or hourly pay range
# (+ pay grade, if known)" -> "$100K - $115K"; also "Target Salary Range Max" -> "$220,000"). Rare
# (~2% of boards sampled) but real data the standard sections never carry. Appended to the
# description as "{label}: {value}" so headstart.salary's existing description-mining cascade can
# parse whatever shape shows up — not a bespoke parser, since the field is company-configured and
# non-standardized (one company's free text, another's bare max-only figure).
_COMPENSATION_FIELD_LABEL = re.compile(
    r"salary|compensation|pay\s*range", re.IGNORECASE
)


def _compensation_custom_fields(custom_field: Any) -> str:
    parts = [
        f"{f.get('fieldLabel')}: {f.get('valueLabel')}"
        for f in custom_field or []
        if _COMPENSATION_FIELD_LABEL.search(f.get("fieldLabel") or "")
        and f.get("valueLabel")
    ]
    return " ".join(parts)


# SmartRecruiters' own adverb form ("YEARLY", "MONTHLY", ...) on the native `compensation.period`
# field. Mapped to the singular bare word (`_field_range_currency_interval`'s
# `_period_multiplier_structured` recognizes "1 YEAR"/"1 HOUR"/"1 MONTH"/"1 WEEK"/"1 DAY" via a
# `\bword\b` match, which "HOURLY" etc. does NOT satisfy — passing the raw adverb through would
# silently default every non-annual figure to the annual multiplier instead of annualizing it).
_STRUCTURED_PERIOD = {
    "YEARLY": "1 YEAR",
    "MONTHLY": "1 MONTH",
    "HOURLY": "1 HOUR",
    "WEEKLY": "1 WEEK",
    "DAILY": "1 DAY",
}


def _salary(compensation: dict | None) -> str | None:
    """Format the posting-detail's native ``compensation`` block, e.g. "70000-85000 EUR 1 YEAR"
    — the same RANGE + CODE + interval shape lever/recruitee/teamtailor/ashby/personio/rippling
    already produce (``_field_range_currency_interval`` in salary.py, registered for this ATS
    alongside this helper). Found via direct API inspection (2026-08-25,
    experiment/location-audit-2026-08-25/smartrecruiters.md): populated on 10.48% of postings,
    and description-mining independently misses 81.7% of those (134/164 in a 1,500-posting
    comparison) — reading this field roughly doubles smartrecruiters' salary coverage at zero
    extra request cost, since the detail fetch already happens for the description.

    ``lo``/``hi`` are checked with ``is not None``, not truthiness: real junk values observed in
    the same pass include ``{"max": 0, "currency": "GBP"}`` and ``{"min": 1, "max": 1, "currency":
    "GTQ"}`` — a truthy check on a 0 floor would misread a stated "$0-$85,000" as a bare ceiling
    figure (the same trap ashby's own ``_salary()`` docstring records from the Ramp
    ``minValue=0`` code-review catch). Passed through honestly instead, both reach ``_bounded``
    and are correctly declined there (0 sits below every currency's plausible floor)."""
    if not compensation:
        return None
    lo, hi = compensation.get("min"), compensation.get("max")
    if lo is None and hi is None:
        return None
    span = (
        f"{lo}-{hi}"
        if lo is not None and hi is not None
        else str(lo if lo is not None else hi)
    )
    period = _STRUCTURED_PERIOD.get((compensation.get("period") or "").upper())
    return " ".join(str(x) for x in (span, compensation.get("currency"), period) if x)


class SmartRecruitersScraper(BaseScraper):
    ats = "smartrecruiters"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?limit={_PAGE_SIZE}"

    def fetch_raw(self) -> Any:
        # First pass: page the listing by `offset` until a short page or the cap. Second pass: fill
        # each posting's detail concurrently (description + native compensation, one fetch for
        # both). The detail pass multiplexes over one HTTP/2 connection by default (ADR-0016); a
        # failed fetch leaves ``_detail`` empty.
        data = json.loads(self._get())
        batch = data.get("content") or []
        postings = list(batch)
        page = 1
        # while len(batch) == _PAGE_SIZE and page < _MAX_PAGES:  -- the cap, disabled below (#227)
        while len(batch) == _PAGE_SIZE:
            more = json.loads(self._get(f"{self.url()}&offset={page * _PAGE_SIZE}"))
            batch = more.get("content") or []
            postings.extend(batch)
            page += 1
        data["content"] = postings
        # The payload reports the board's true size, so a short list is knowingly short and must
        # say so or `index sync` evicts everything behind the page as a delisting (ADR-0053).
        # Measured 2026-08-20: dominos totalFound=24556 behind a 100-posting page.
        # `totalFound` is always present and always an int — verified live across 15 boards
        # 2026-08-20, a dead slug included: it answers {"totalFound": 0}.
        # `totalFound` is exact rather than a full-page guess (ADR-0070), so this still catches a
        # short read even with the cap disabled — a posting closing mid-crawl, or an inconsistent
        # page. The cap-naming half, disabled along with the cap itself (#227):
        #     capped = page == _MAX_PAGES and len(batch) == _PAGE_SIZE
        #     cap_note = f" — hit the {_MAX_PAGES}-page cap" if capped else ""
        # With no cap enforced, `page` reaching `_MAX_PAGES` can no longer be what stopped the
        # loop, so naming it would mislabel a genuine short read as a cap hit — `cap_note` below
        # is `""` rather than the commented-out call above until the cap is re-enabled.
        total = data.get("totalFound") or 0
        cap_note = ""
        if total > len(postings):
            self.mark_truncated(
                f"read {len(postings)} of {total} postings{cap_note} — the rest unread"
            )
        if self.async_fanout_enabled():
            details = self.fan_out_async(
                postings,
                lambda session, p: self._job_detail_async(session, p.get("id")),
            )
        else:
            details = self.fan_out(
                postings,
                lambda p: self._job_detail(p.get("id")),
                workers=_DETAIL_WORKERS,
            )
        self.report_detail_gaps(details, "details")
        for posting, detail in zip(postings, details):
            posting["_detail"] = detail or {}
        return data

    def _detail_url(self, posting_id: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings/{posting_id}"

    @staticmethod
    def _extract_detail(response: Any) -> dict[str, Any] | None:
        """The posting-detail fields ``parse()`` needs (None on non-200): the jobAd sections
        concatenated into raw HTML, and the native ``compensation`` block (min/max/currency/
        period — populated on 10.48% of postings, unread until this pass; see the module-level
        ``_salary()`` docstring). One fetch for both — this response is already the one the
        scraper makes for the description alone.

        qualifications and additionalInformation carry the requirements (years of
        experience etc.); companyDescription is deliberately skipped — it's the same
        boilerplate on every posting and would dilute the embedding.
        """
        if response.status_code != 200:
            return None
        payload = response.json()
        sections = (payload.get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(k) or {}).get("text")
            for k in ("jobDescription", "qualifications", "additionalInformation")
        ]
        return {
            "description": "\n".join(p for p in parts if p) or None,
            "compensation": payload.get("compensation") or None,
        }

    def _job_detail(self, posting_id: str | None) -> dict[str, Any] | None:
        """GET one posting's detail fields (None on failure). Sync path."""
        if not posting_id:
            return None
        try:
            response = http.fetch(
                "GET",
                self._detail_url(posting_id),
                timeout=30,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except http.RequestsError:
            return None  # a missing detail must not drop the job
        return self._extract_detail(response)

    async def _job_detail_async(
        self, session: Any, posting_id: str | None
    ) -> dict[str, Any] | None:
        """Same as :meth:`_job_detail` but over the shared multiplexed ``AsyncSession``."""
        if not posting_id:
            return None
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(posting_id),
                timeout=30,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except http.RequestsError:
            return None
        return self._extract_detail(response)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for p in raw.get("content", []):
            loc = p.get("location") or {}
            full_location = loc.get("fullLocation")
            location = (
                (
                    ", ".join(
                        part
                        for part in (s.strip() for s in full_location.split(","))
                        if part
                    )
                    or None
                )
                if full_location
                else (
                    ", ".join(
                        x
                        for x in (
                            loc.get("city"),
                            loc.get("region"),
                            loc.get("country"),
                        )
                        if x
                    )
                    or None
                )
            )
            detail = p.get("_detail") or {}
            description = html_to_text(detail.get("description"))
            comp_fields = _compensation_custom_fields(p.get("customField"))
            if comp_fields:
                description = f"{description or ''} {comp_fields}".strip()
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{p['id']}",
                    ats=self.ats,
                    company=(p.get("company") or {}).get("name") or self.company,
                    title=(p.get("name") or "").strip(),
                    location=location,
                    remote=bool(loc.get("remote")) or is_remote(location),
                    department=(p.get("department") or {}).get("label"),
                    url=f"https://jobs.smartrecruiters.com/{self.slug}/{p['id']}",
                    posted_at=p.get("releasedDate"),
                    scraped_at=scraped_at,
                    description=description,
                    experience=(p.get("experienceLevel") or {}).get("label"),
                    employment_type=(p.get("typeOfEmployment") or {}).get("label"),
                    salary=_salary(detail.get("compensation")),
                )
            )
        return jobs
