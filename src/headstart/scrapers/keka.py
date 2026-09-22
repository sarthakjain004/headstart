"""Keka job-board scraper ({slug}.keka.com careers API).

Reverse-engineered from a browser HAR (no public docs; Keka's documented API is OAuth-only).
Reading a board is one unauthenticated GET:

    /careers/api/jobs/default/active  -> JSON array of active jobs

This replaced a two-step path that first read the tenant UUID out of
``/careers/api/organization/default/careerportalinfo`` — where it only rides along inside
``careersBackgroundPath``, the portal background-image URL, so a portal with no custom background
carries no UUID there — falling back to the ``/careers`` page HTML, and only then fetched
``/careers/api/embedjobs/default/active/{tenant_uuid}``. Those two steps are strictly worse and
were losing whole Boards: measured over 150 random Hiring Boards (2026-09-22), they returned
**nothing at all on 23 of them (15%)** — a background-less portal whose ``/careers`` page is a
4.5 KB shell with no UUID in it defeats both sources at once, while ``careerportalinfo`` still
names a real company. This endpoint served every one of those 23, and across the 125 where both
answered it returned the same job count and the same fields, with the two-step never once
winning. Do not reintroduce the UUID dance as a fallback; it has no measured case.

This scraper was the **last** of the three call sites to migrate. ``check_liveness.p_keka`` and
``mine_keka.py`` both moved to this endpoint on 2026-07-27 for the same reason, on a 25-of-25
sample; ``p_keka`` also records where it comes from (the careers SPA's own call, read off
``cdn.keka.com/careers/v/2026/scripts/app/app.min.js``:
``$.ajax('/api/jobs/${apiPortalName}/active')``). Keep the three in step.
Measurements: `docs/keka/2026-09-22_direct-jobs-endpoint-measurement.md`.

Keka soft-errors at HTTP 200 with an HTML page ("Invalid Tenant" for an unknown slug,
"Forbidden Access" for a disabled portal) — either means no public board, so we yield no jobs
rather than misreading the HTML. Any *other* non-JSON 200 raises out of ``fetch`` rather than
degrading to an empty list: a board that raises is a per-company failure and is not evicted,
which is the safer read of a body we cannot classify.

The payload carries 14 keys on every board measured, plus an optional ``jobNumber`` (3 of 8
sampled). Every field :meth:`parse` reads is in the universal 14.

Three listing-only additions (2026-09-22), all read off the same ``active`` payload above — no
new request:

1. **``location`` joins every ``jobLocations`` entry, not just ``[0]``.** Measured live: 57 of
   929 jobs on ``kpgroup.keka.com`` alone carry more than one location; taking the first silently
   dropped the rest. Joined "; ", the same separator ``apple.py``/``google.py``/``workday.py``
   use for a multi-place string.
2. **``employment_type`` maps the ``jobType`` enum's two confirmed values.** Measured live across
   8 boards / 2,792 postings: ``2`` (2,715, "Full-time") and ``1`` (24, "Part-time") both occur on
   several independent tenants, in a ratio consistent with a real full/part split. A third value,
   ``0``, also appears (53, all on one tenant, ``csdemo`` — plausibly a sandbox/demo board and not
   independently corroborated elsewhere) and is deliberately left unmapped: guessing a label for a
   value seen on one tenant is exactly the "unsourced enum" risk this addition exists to avoid
   for the other two.

**Not done: company name from ``careerportalinfo`` (a change requested in #547, re-verified and
declined 2026-09-22).** #547 asked for this on the premise that the scraper "already fetches that
exact response" — true when #547 was written, false two minutes later: #529, merged first,
removed the ``careerportalinfo`` fetch from the listing path entirely (see above; it was strictly
worse for the *listing*). Reading it now for the name alone would be a **new** per-Board request,
not a free one, and #547's own "before shipping" note asks for a ≥100-board presence check against
ADR-0114's quality bar — a measurement this pass didn't do. Filed as a follow-up rather than forced
in under a premise that no longer holds; `board_page()`'s existing ``<title>`` reading
(``company_name.py``, ~11% yield) is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

# Keka renders these at HTTP 200 (not 404/403): an unknown slug -> "Invalid Tenant", a disabled
# careers portal -> "Forbidden Access". Either means there is no public board to read.
_DEAD_MARKERS = ("Invalid Tenant", "Forbidden Access")

# jobType's two confirmed values (module docstring) — 0 is deliberately absent, see there.
_JOB_TYPE_LABELS = {1: "Part-time", 2: "Full-time"}


def _location_part(loc: dict) -> str | None:
    """One ``jobLocations`` entry's "City, State, Country" string. ``.strip()`` per part: `city`
    carries a trailing space on some tenants' data (e.g. "Ahmedabad Center " while the sibling
    `name` field for the same location is clean), which would otherwise leak into the joined
    string."""
    parts = (
        (loc.get("city") or loc.get("name") or "").strip(),
        (loc.get("state") or "").strip(),
        (loc.get("countryName") or "").strip(),
    )
    return ", ".join(p for p in parts if p) or None


def _location(job_locations: Any) -> str | None:
    """Every stated location, "; "-joined (module docstring) — the separator
    ``apple.py``/``google.py``/``workday.py`` already use for the same shape."""
    parts = [_location_part(loc) for loc in job_locations or [{}]]
    return "; ".join(p for p in parts if p) or None


def _format_num(v: float) -> str:
    """Fixed-point, never scientific notation. Python's ``:g`` format (this function's own
    predecessor) silently switches to scientific notation ("1e+06") for values >= 1,000,000 —
    neither this module's ``_RANGE`` regex nor ``headstart.salary._num()`` can parse an exponent,
    so every genuine keka figure at or above ₹1,000,000 was silently discarded (real, evidenced:
    27% of a 300-job sample of rejected ``Job.salary`` field values, across 19 distinct companies
    — salary-extraction pass 2026-08-22)."""
    return f"{v:f}".rstrip("0").rstrip(".") or "0"


#: salaryPeriod -> the phrase word `headstart.salary._period_multiplier` recognizes, so
#: `_field_keka` annualizes the figure before the plausibility bounds are checked. See
#: `KekaScraper._salary_field`'s docstring for the live re-measurement behind this map. Period 4
#: (Annual) needs no word: `_period_multiplier`'s default, with no period phrase present, is
#: already annual.
_PERIOD_WORDS = {1: "hourly", 3: "monthly"}


class KekaScraper(BaseScraper):
    ats = "keka"
    url_shape = r"https://[^.]+\.keka\.com/careers/jobdetails/\d+"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}.keka.com/careers/jobdetails/{native_id}"

    def board_page(self) -> str:
        """The careers page, whose ``<title>`` is "Careers at {Name}" or "{Name} Careers".

        Most keka Boards render their ``<title>`` client-side and serve nothing to read, but
        where one exists the wrapper is as uniform as eightfold's, and every keka Board serves a
        slug today, so it is all upside. The measured rate is deliberately **not** repeated here:
        `headstart.company_name` holds it and ADR-0114 restates it as the spec of record, and
        three copies of it had already drifted apart before this docstring stopped being a fourth.

        Nothing else fetches this page — the listing no longer reads it for a tenant uuid — so it
        is always a genuinely new request, costing a measured 0.12s (~2 min across a full run,
        concurrent within each shard).
        """
        return f"https://{self.slug}.keka.com/careers"

    def url(self) -> str:
        return f"https://{self.slug}.keka.com/careers/api/jobs/default/active"

    def fetch_raw(self) -> Any:
        body = self._get()
        marker = next((m for m in _DEAD_MARKERS if m in body), None)
        if marker:
            # Not marked truncated: this module's own measurement (module docstring) is that the
            # marker *means* no public board, and truncating would hold a departed tenant's rows
            # in the index indefinitely. It is still worth a line — the two markers differ, and
            # "Forbidden Access" on a Board that was serving jobs yesterday is a portal someone
            # switched off, not a tenant that left.
            self.note_unreadable_board("the active-jobs array", f"a {marker!r} page")
            return []
        return json.loads(body)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            location = _location(j.get("jobLocations"))
            jobs.append(
                Job(
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=j.get("departmentName"),
                    url=self.job_url(j["id"]),
                    posted_at=j.get("publishedOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("description") or j.get("excerpt")),
                    experience=j.get("experience"),
                    employment_type=_JOB_TYPE_LABELS.get(j.get("jobType")),
                    salary=self._salary_field(j.get("salaryRange")),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format keka's salaryRange, e.g. '25000-30000 INR monthly'. None when no amounts
        published.

        The raw payload also carries a numeric ``salaryPeriod`` enum. A prior pass
        (salary-extraction pass 2026-08-22) called the label mapping "confirmed UNDECODABLE" on
        the grounds that no keka careers page's own JS bundle mentions "salary" anywhere — true,
        but that only rules out reverse-engineering the labels from keka's own product; it does
        not rule out an externally-sourced map being right. An independent third-party
        implementation (kalil0321/ats-scrapers) ships one — ``{1: Hourly, 3: Monthly, 4:
        Annual}`` — and re-measuring against it live (2026-09-22, ~200 random Hiring Boards,
        ~5,000 jobs with a stated amount) confirms it holds by magnitude: period 3 values are
        monthly-scale (10³–10⁵) on 179/215 (83%), period 4 values are annual-scale (10⁵–10⁷) on
        330/375 (88%), and both stated period-1 values are small enough to be plausible hourly
        rates. ``_PERIOD_WORDS`` below wires only those three — 0 ("Not Available") stays
        undecoded on purpose: it is keka's own blank-field default (see the ``or None`` note
        below) *and* a real per-company value, and re-measurement found it still genuinely
        mixed-magnitude (662 sampled values, a 60-board subsample, spanning LPA-, monthly- and
        absolute-annual-scale with no signal to split them by — see
        docs/salary-extraction/keka.md's 2026-09-22 update for the per-scale breakdown) — the
        "inconsistent per-company data entry" finding
        that closed this out in 2026-08-22 was real, just scoped to 0 rather than the whole
        enum. 2 ("Bi Weekly") had a single stated example in the whole re-measurement — too rare
        to confirm, and the upstream map omits it too.
        """
        raw = raw or {}
        # `or None` (truthy, not `is not None`) is deliberate here, checked against real data
        # before keeping it: unlike ashby's real bug (a genuinely STATED 0 silently dropped),
        # keka's 0 is a form default for "left blank" — every real 0/0 pair seen is a
        # fully-unfilled field, and an asymmetric 0/X pair reads as "only the ceiling was
        # entered," which `SalarySpan.min_annual` being a required int can't represent anyway
        # (same as any other ceiling-only figure). The `lo or hi` fallback below already
        # produces the correct bare-ceiling string for that case.
        lo, hi = raw.get("minimum") or None, raw.get("maximum") or None
        if not lo and not hi:
            return None
        span = (
            f"{_format_num(lo)}-{_format_num(hi)}"
            if lo and hi
            else _format_num(lo or hi)
        )
        period = _PERIOD_WORDS.get(raw.get("salaryPeriod"))
        return " ".join(str(x) for x in (span, raw.get("currency"), period) if x)
