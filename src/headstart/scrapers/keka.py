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
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

# Keka renders these at HTTP 200 (not 404/403): an unknown slug -> "Invalid Tenant", a disabled
# careers portal -> "Forbidden Access". Either means there is no public board to read.
_DEAD_MARKERS = ("Invalid Tenant", "Forbidden Access")


def _format_num(v: float) -> str:
    """Fixed-point, never scientific notation. Python's ``:g`` format (this function's own
    predecessor) silently switches to scientific notation ("1e+06") for values >= 1,000,000 —
    neither this module's ``_RANGE`` regex nor ``headstart.salary._num()`` can parse an exponent,
    so every genuine keka figure at or above ₹1,000,000 was silently discarded (real, evidenced:
    27% of a 300-job sample of rejected ``Job.salary`` field values, across 19 distinct companies
    — salary-extraction pass 2026-08-22)."""
    return f"{v:f}".rstrip("0").rstrip(".") or "0"


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
            loc = (j.get("jobLocations") or [{}])[0]
            # `city` carries a trailing space on some tenants' data (e.g. "Ahmedabad Center "
            # while the sibling `name` field for the same location is clean) — `.strip()` each
            # part so it can't leak into the joined string.
            parts = (
                (loc.get("city") or loc.get("name") or "").strip(),
                (loc.get("state") or "").strip(),
                (loc.get("countryName") or "").strip(),
            )
            location = ", ".join(p for p in parts if p) or None
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
                    # jobType is a bare numeric enum (0/1/2) whose labels aren't in the
                    # payload or reachable frontend code — left unmapped rather than guessed
                    salary=self._salary_field(j.get("salaryRange")),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format keka's salaryRange, e.g. '25000-30000 INR'. None when no amounts published.

        The raw payload also carries a numeric ``salaryPeriod`` enum (confirmed real: values 0-4
        seen across a 150-board sample, salary-extraction pass 2026-08-22) — but its label
        mapping is confirmed UNDECODABLE, not just undocumented: the tenant-specific JS bundle
        every keka careers page actually loads
        (`{slug}.keka.com/careers/api/embedjobs/js/{tenant_uuid}`) contains zero occurrences of
        the string "salary" anywhere in it — the public embed-jobs widget doesn't render salary
        at all, so no label mapping exists anywhere in the public product to reverse-engineer,
        not merely one this scraper hasn't found yet. Statistical inference from magnitude
        doesn't resolve it either: the same enum value spans both LPA-shorthand-scale numbers
        ("3-5") and absolute-rupee-scale numbers ("300000-500000") across different tenants,
        consistent with inconsistent data entry by each company's own HR staff rather than a
        clean, guessable convention. The period is correctly omitted rather than guessed.
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
        return " ".join(str(x) for x in (span, raw.get("currency")) if x)
