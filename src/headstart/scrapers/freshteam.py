"""Freshteam job-board scraper ({slug}.freshteam.com careers widget).

One unauthenticated GET returns the whole board — the public careers-page embed feed:
    GET https://{slug}.freshteam.com/hire/widgets/jobs.json
      -> {"jobs": [...], "branches": [...], "job_roles": [...]}

No second pass: each job carries its HTML ``description`` inline (100% populated across a
2,591-job / 169-tenant corpus scan, 2026-07-21), unlike Workday. Two fields are foreign keys
into the sibling arrays, so we index those once and join:
  - ``branch_id``   -> ``branches[].location`` (a pre-formatted "City, Country" string, 100%
                        populated; ``branches[].currency`` etc. ride along but we don't need them)
  - ``job_role_id`` -> ``job_roles[].name`` (the department)

Remote is a **native boolean** (``remote``), so this is a "both"-family scraper: we take the
native flag OR ``is_remote(location)`` OR the occasional ``preferred_remote_job_locations`` string
(present on ~4% of jobs) — best recall without over-claiming.

Not mapped, on purpose:
  - ``employment_type``: ``job_type`` is a bare numeric enum (2 == ~92% of jobs; 1,3,4,5,7,8 seen)
    whose labels aren't in the payload or reliably reachable — left unmapped rather than guessed
    (same call as Keka's ``jobType``). The tech gate and search don't depend on it.
  - ``experience``: no native field; the post-hoc extractor (ADR-0018) reads it from the description.
  - ``salary``: ``ctc_details`` was null on every one of the 2,591 scanned jobs — no shape to parse.

Known limits: the widget caps a tenant at 1000 jobs with no pagination parameter (an SMB, now-EOL
ATS — a real tech employer won't hit this). An unknown/dead slug soft-errors at HTTP 200 with an
HTML 404 page (not JSON), which ``fetch_raw`` treats as an empty board.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


def _branch_location(branch: dict) -> str | None:
    """A branch's display location: the pre-formatted ``location`` string, else built from the
    city/state/country parts (defensive — ``location`` was populated on every scanned branch)."""
    if branch.get("location"):
        return branch["location"]
    parts = (branch.get("city"), branch.get("state"), branch.get("country_code"))
    return ", ".join(p for p in parts if p) or None


class FreshteamScraper(BaseScraper):
    ats = "freshteam"

    def url(self) -> str:
        return f"https://{self.slug}.freshteam.com/hire/widgets/jobs.json"

    def fetch_raw(self) -> Any:
        """The widget payload, or ``{}`` for a dead tenant. An unknown slug returns an HTML 404
        at HTTP 200, so a JSON decode failure (or a non-object body) means no public board."""
        try:
            data = json.loads(self._get())
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        branch_loc = {b["id"]: _branch_location(b) for b in raw.get("branches") or []}
        role_name = {r["id"]: r.get("name") for r in raw.get("job_roles") or []}

        jobs: list[Job] = []
        for j in raw.get("jobs") or []:
            if j.get("deleted"):
                continue
            location = branch_loc.get(j.get("branch_id"))
            # ``remote`` is an authoritative native boolean, so the flag is always definitive
            # (bool, never None); the location signals only upgrade a native-False to True.
            remote = (
                bool(j.get("remote"))
                or bool(is_remote(location))
                or bool(is_remote(j.get("preferred_remote_job_locations")))
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=remote,
                    department=role_name.get(j.get("job_role_id")),
                    url=j.get("url")
                    or f"https://{self.slug}.freshteam.com/jobs/{j.get('unique_id', '')}",
                    posted_at=j.get("created_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("description")),
                )
            )
        return jobs
