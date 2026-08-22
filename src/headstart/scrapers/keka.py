"""Keka job-board scraper ({slug}.keka.com careers embed API).

Reverse-engineered from a browser HAR (no public docs; Keka's documented API is OAuth-only).
The careers SPA loads jobs from an unauthenticated embed API in two steps:
  1. GET /careers/api/organization/default/careerportalinfo  -> carries the tenant UUID
  2. GET /careers/api/embedjobs/default/active/{tenant_uuid}  -> JSON array of active jobs

The UUID in step 1 only rides along inside ``careersBackgroundPath`` (the portal's background-image
URL), so a portal with no custom background image carries no UUID there — for those we fall back to
the ``/careers`` page HTML, which embeds it. Keka also soft-errors at HTTP 200 with an HTML page
("Invalid Tenant" for an unknown slug, "Forbidden Access" for a disabled portal) — either means no
public board, so we yield no jobs rather than misreading the HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
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


def _salary(rng: dict | None) -> str | None:
    """Format keka's salaryRange, e.g. '25000-30000 INR'. None when no amounts published.

    The raw payload also carries a numeric ``salaryPeriod`` enum (confirmed real: values 0-4 seen
    across a 150-board sample, salary-extraction pass 2026-08-22) — but its label mapping is
    confirmed UNDECODABLE, not just undocumented: the tenant-specific JS bundle every keka careers
    page actually loads (`{slug}.keka.com/careers/api/embedjobs/js/{tenant_uuid}`) contains zero
    occurrences of the string "salary" anywhere in it — the public embed-jobs widget doesn't
    render salary at all, so no label mapping exists anywhere in the public product to reverse-
    engineer, not merely one this scraper hasn't found yet. Statistical inference from magnitude
    doesn't resolve it either: the same enum value spans both LPA-shorthand-scale numbers ("3-5")
    and absolute-rupee-scale numbers ("300000-500000") across different tenants, consistent with
    inconsistent data entry by each company's own HR staff rather than a clean, guessable
    convention. The period is correctly omitted rather than guessed.
    """
    rng = rng or {}
    # `or None` (truthy, not `is not None`) is deliberate here, checked against real data before
    # keeping it: unlike ashby's real bug (a genuinely STATED 0 silently dropped), keka's 0 is a
    # form default for "left blank" — every real 0/0 pair seen is a fully-unfilled field, and an
    # asymmetric 0/X pair reads as "only the ceiling was entered," which `SalarySpan.min_annual`
    # being a required int can't represent anyway (same as any other ceiling-only figure). The
    # `lo or hi` fallback below already produces the correct bare-ceiling string for that case.
    lo, hi = rng.get("minimum") or None, rng.get("maximum") or None
    if not lo and not hi:
        return None
    span = (
        f"{_format_num(lo)}-{_format_num(hi)}" if lo and hi else _format_num(lo or hi)
    )
    return " ".join(str(x) for x in (span, rng.get("currency")) if x)


class KekaScraper(BaseScraper):
    ats = "keka"

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self._tenant: str | None = None

    def url(self) -> str:
        base = f"https://{self.slug}.keka.com/careers/api"
        if self._tenant is None:
            return f"{base}/organization/default/careerportalinfo"
        return f"{base}/embedjobs/default/active/{self._tenant}"

    def fetch_raw(self) -> Any:
        # step 1: portal info. A soft-error HTML page (200) means no public board.
        info = self._get()
        if any(marker in info for marker in _DEAD_MARKERS):
            return []
        tenant = self._tenant_uuid(info)
        if not tenant:
            return []
        self._tenant = tenant
        # step 2: the active-jobs array
        return json.loads(self._get())

    def _tenant_uuid(self, info: str) -> str | None:
        """The org UUID: from careerportalinfo when a background image carries it, else from the
        ``/careers`` page (portals with no custom background omit it from careerportalinfo)."""
        match = _UUID_RE.search(info)
        if match:
            return match.group(0)
        page = self._get(f"https://{self.slug}.keka.com/careers")
        match = _UUID_RE.search(page)
        return match.group(0) if match else None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            loc = (j.get("jobLocations") or [{}])[0]
            location = (
                ", ".join(
                    x
                    for x in (
                        loc.get("city") or loc.get("name"),
                        loc.get("state"),
                        loc.get("countryName"),
                    )
                    if x
                )
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=j.get("departmentName"),
                    url=f"https://{self.slug}.keka.com/careers/jobdetails/{j['id']}",
                    posted_at=j.get("publishedOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("description") or j.get("excerpt")),
                    experience=j.get("experience"),
                    # jobType is a bare numeric enum (0/1/2) whose labels aren't in the
                    # payload or reachable frontend code — left unmapped rather than guessed
                    salary=_salary(j.get("salaryRange")),
                )
            )
        return jobs
