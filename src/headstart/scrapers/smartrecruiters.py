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
its truncation message) to re-enable the 50-page cap — and route a capped read to the
unconditional `mark_truncated`, since a cap is not a negligible shortfall (ADR-0121).

The postings list has no description; a second pass fetches each posting's detail
(GET .../postings/{id} -> jobAd.sections.jobDescription.text) in a bounded thread pool to
fill it in. That same detail response also carries a native `compensation.{min,max,currency,
period}` block (10.48% of postings, previously unread — see `_salary_field()`), so one fetch now
feeds both `description` and `salary`. A failed detail fetch leaves both None — the job is
still kept.

`department` falls back to `function.label` when `department.label` is null (added 2026-09-22).
Measured live that day, 8 boards / 772 postings: `department` null on 54.7%, `function` present on
100% of those. Re-running `is_tech(title, None)` vs `is_tech(title, department or function)` over
just that null-department slice: +22 promoted to tech, 0 lost, on that sample. It matters twice —
`tech_filter` rule 4 promotes a vague title sitting in a technical department/function, and
`tech_detail_wanted` (the pre-detail gate) reads the same pair, so a null department was silently
narrowing both. A **wider, independently drawn** 10-board / 720-posting re-check (different boards
from the 8 above) found the same shape at a different scale — 77.6% null, +83 gained — but also
**3 losses**, all from a title tripping only the ambiguous "…Engineer" rule (`tech_filter.py`
rule 2) whose department is genuinely a non-software function once substituted in: one
`Sales`-departed "Solutions Engineer" (`tech_filter.py`'s own docstring already treats a
Sales-department "Solutions Engineer" as correctly non-tech, the same call `_NON_SOFTWARE`'s
`sales` term makes on purpose), and two `Manufacturing`-departed hardware/software roles at one
company — the latter is a genuine `tech_filter.py` gap (only `hardware`, not `manufacturing`, is
in `_ORG_NOT_ROLE`'s org-vs-discipline exception, ADR-0068), pre-existing and out of scope here:
this change only supplies `department` where none existed, it does not touch how `department` is
read. Net across both samples: 105 gained, 2-3 lost — a real, disclosed trade in the gate's
recall-biased direction, not a silent one.

Reading this field does NOT need a `doc_prep.DERIVATIONS_VERSION` bump: `salary` is a
re-observed FACT_FIELD (`update_meta.py`), so once a Board is rescraped its now-populated raw
`Job.salary` differs from the stored one, `refresh_row`'s `salary_inputs_moved` fires, and the
cascade re-runs on every already-indexed row for that Board — no version sweep required. A
bump is for when unchanged input starts parsing differently; here the input itself changed
from `None` to a real string.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import salary
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
)

_DETAIL_WORKERS = 8
_PAGE_SIZE = 100  # our page size, not the provider's ceiling (ADR-0070)
# Our own ceiling, sized by cost rather than by tech density — because density does not fall off
# down the list. Measured live: 14.1% tech at offset 500 across 40 random boards over 500 postings,
# and 6 of the 15 boards over 3,000 run 14-62% tech at *half* and *end* of board. 5,000 postings is
# the most this scraper can read and still stay under ADR-0064's gate floor at the slow end of
# fleet throughput; what stays truncated above it is ~0%-tech retail the gate handles. Sized
# against the 15 min floor; it is 10 min since 2026-09-24, so re-derive before re-enabling.
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


def _department_of(p: dict) -> str | None:
    """``department.label``, falling back to ``function.label`` when the posting states no
    department (module docstring) — read by both the pre-detail tech gate and ``parse()`` so
    the two can never disagree. No new ``Job`` field: the whole value here is that
    ``filter_tech`` reads ``department``."""
    return (p.get("department") or {}).get("label") or (p.get("function") or {}).get(
        "label"
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


class SmartRecruitersScraper(BaseScraper):
    ats = "smartrecruiters"
    url_shape = r"https://jobs\.smartrecruiters\.com/[^/]+/\d+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?limit={_PAGE_SIZE}"

    def job_url(self, posting_id: str) -> str:
        return f"https://jobs.smartrecruiters.com/{self.slug}/{posting_id}"

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
        # Against that exact total the shortfall is measured, so a negligible one is left to
        # ADR-0083's grace period (ADR-0121) — measured 2026-09-24, `accorhotel` lost its eviction
        # scope over 6378 of 6379. A cap hit is a hard cap, not noise: re-enabling the cap must
        # also send `capped` reads to the unconditional `self.mark_truncated(why)`.
        total = data.get("totalFound") or 0
        cap_note = ""
        if total > len(postings):
            self.mark_truncated_unless_negligible(
                len(postings),
                total,
                f"read {len(postings)} of {total} postings{cap_note} — the rest unread",
            )
        # `parse` reads `name` and `_department_of` off this listing posting and never off
        # `_detail`, so the gate asks `filter_tech`'s own question with `filter_tech`'s own
        # inputs. A gated posting still ships as a Job without a description.
        details = self.run_detail_pass(
            postings,
            key_of=lambda posting: posting.get("id"),
            what="details",
            title_of=lambda posting: posting.get("name"),
            department_of=_department_of,
        )
        for posting in postings:
            posting["_detail"] = details.get(posting.get("id")) or {}
        return data

    def detail_request(self, posting: dict[str, Any]) -> DetailRequest:
        posting_id = posting.get("id")
        if not posting_id:
            raise DetailLost("no posting id")
        return DetailRequest(
            f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings/{posting_id}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def read_detail(self, posting: dict[str, Any], response: Any) -> dict[str, Any]:
        """The posting-detail fields ``parse()`` needs: the jobAd sections concatenated into raw
        HTML, and the native ``compensation`` block (min/max/currency/period — populated on
        10.48% of postings, unread until this pass; see
        :meth:`SmartRecruitersScraper._salary_field`'s docstring). One fetch for both — this
        response is already the one the scraper makes for the description alone.

        qualifications and additionalInformation carry the requirements (years of
        experience etc.); companyDescription is deliberately skipped — it's the same
        boilerplate on every posting and would dilute the embedding.
        """
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
                    id=self.job_id(p["id"]),
                    ats=self.ats,
                    company=(p.get("company") or {}).get("name") or self.company,
                    title=(p.get("name") or "").strip(),
                    location=location,
                    remote=bool(loc.get("remote")) or is_remote(location),
                    department=_department_of(p),
                    url=self.job_url(p["id"]),
                    posted_at=p.get("releasedDate"),
                    scraped_at=scraped_at,
                    description=description,
                    experience=(p.get("experienceLevel") or {}).get("label"),
                    employment_type=(p.get("typeOfEmployment") or {}).get("label"),
                    salary=self._salary_field(detail.get("compensation")),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format the posting-detail's native ``compensation`` block, e.g. "70000-85000 EUR 1
        YEAR" — the same RANGE + CODE + interval shape lever/recruitee/teamtailor/ashby/
        personio/rippling already produce (``_field_range_currency_interval`` in salary.py,
        registered for this ATS alongside this helper). Found via direct API inspection
        (2026-08-25, experiment/location-audit-2026-08-25/smartrecruiters.md): populated on
        10.48% of postings, and description-mining independently misses 81.7% of those (134/164
        in a 1,500-posting comparison) — reading this field roughly doubles smartrecruiters'
        salary coverage at zero extra request cost, since the detail fetch already happens for
        the description.

        ``lo``/``hi`` are checked with ``is not None``, not truthiness: real junk values observed
        in the same pass include ``{"max": 0, "currency": "GBP"}`` and ``{"min": 1, "max": 1,
        "currency": "GTQ"}`` — a truthy check on a 0 floor would misread a stated "$0-$85,000" as
        a bare ceiling figure (the same trap ashby's own ``_salary_field()`` docstring records
        from the Ramp ``minValue=0`` code-review catch). Passed through honestly instead, both
        reach ``_bounded`` and are correctly declined there (0 sits below every currency's
        plausible floor).

        A ``max``-only block (``min`` absent) is declined outright rather than passed through as
        a bare single value. ``_field_range_currency_interval``'s single-value path always reads
        a lone figure as a floor with no ceiling (see ashby.py's own ``_salary_field()``
        docstring: it checked its mirror shape directly against live data, found 0/820 real
        occurrences, and left it on that path deliberately since it never happens in practice
        there). SmartRecruiters' native block does send max-only in practice — live-verified
        2026-08-26, 1/19 populated ``compensation`` blocks across 60 boards/348 postings, e.g.
        ``{"max": 12150, "currency": "MXN", "period": "MONTHLY"}`` — and reading that through the
        single-value path would silently misreport a stated ceiling ("up to 12,150 MXN/month") as
        an unbounded floor ("145,800/year, no ceiling", annualized). Unlike the ``max: 0`` junk
        case above, this one is not caught by ``_bounded`` either — 145,800 clears the
        USD-fallback plausibility bounds cleanly, so it would ship as a confident, wrong number
        rather than a safe decline."""
        if not raw:
            return None
        lo, hi = raw.get("min"), raw.get("max")
        if lo is None:
            return None
        period = _STRUCTURED_PERIOD.get((raw.get("period") or "").upper())
        return salary.to_field(lo, hi, raw.get("currency"), period)
