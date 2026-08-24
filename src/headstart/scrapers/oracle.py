"""Oracle Recruiting Cloud (HCM) scraper.

Adapted from jobhive's Oracle scraper (kalil0321/ats-scrapers, MIT). Each Oracle tenant has
its own host, so the slug is the careers host (e.g. ``fa-etvl-saasfaprod1.fa.ocs.oraclecloud
.com``), optionally ``host/CX_2`` to override the site number (default ``CX_1``). The public
CandidateExperience REST API returns requisitions under ``items[0].requisitionList`` — the
pagination params must live *inside* the ``finder`` string, not as separate query params.
"""

from __future__ import annotations

import json
from typing import Any

from headstart import log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

#: The API's own maximum `limit`. Requesting more is silently clamped to it.
_PAGE_SIZE = 200
#: Our ceiling. Reaching it means the board did not end, we stopped reading it.
_MAX_PAGES = 100


class OracleScraper(BaseScraper):
    ats = "oracle"

    def __init__(self, slug: str, company: str | None = None) -> None:
        host, _, site = slug.partition("/")
        super().__init__(host, company)
        self.site = site or "CX_1"
        self._offset = (
            0  # advanced by `fetch_raw`; `url()` renders whatever page it is on
        )

    def url(self) -> str:
        finder = (
            f"findReqs;siteNumber={self.site}"
            f"%2Climit={_PAGE_SIZE}%2Coffset={self._offset}"
        )
        return (
            f"https://{self.slug}/hcmRestApi/resources/latest/"
            f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder={finder}"
        )

    def fetch_raw(self) -> Any:
        """Page through the requisition list. The base one-shot fetch read only the first 200.

        Measured live 2026-08-24 on ``fa-etvl-saasfaprod1``: ``TotalJobsCount`` 299 against a
        200-row first page, so a third of that board was dropped every run — and silently, since
        nothing compared the two. ``offset=200`` returns the remaining 99, so the API paginates
        correctly; only the caller never asked.
        """
        reqs: list[dict] = []
        total = 0
        self._offset = 0
        for _ in range(_MAX_PAGES):
            items = json.loads(self._get()).get("items") or []
            first = items[0] if items else {}
            batch = first.get("requisitionList") or []
            # Keep the first non-zero total: later pages echo it, but a missing one must not
            # reset what an earlier page already told us.
            total = first.get("TotalJobsCount") or total
            reqs.extend(batch)
            self._offset += _PAGE_SIZE
            # `total and ...` is load-bearing: without it a missing TotalJobsCount makes
            # `len(reqs) >= 0` true and stops after one page — a silent truncation wearing the
            # natural-end branch's clothes. Absent a total, the short page is the only honest end.
            if len(batch) < _PAGE_SIZE or (total and len(reqs) >= total):
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(reqs)} of {total or 'unknown'} "
                "requisitions — the rest unread"
            )
        if total and len(reqs) < total:
            _log.warning(
                f"{self.board_key()}: read {len(reqs)} of {total} requisitions — "
                "the rest is unread, not absent"
            )
        return {"items": [{"requisitionList": reqs}]}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        items = raw.get("items") or []
        reqs = items[0].get("requisitionList", []) if items else []
        jobs: list[Job] = []
        for r in reqs:
            location = r.get("PrimaryLocation") or r.get("PrimaryLocationCountry")
            workplace = r.get("WorkplaceType") or r.get("WorkplaceTypeCode") or ""
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{r['Id']}",
                    ats=self.ats,
                    company=r.get("LegalEmployer") or self.company,
                    title=(r.get("Title") or "").strip(),
                    location=location,
                    remote="remote" in workplace.lower() or is_remote(location),
                    department=r.get("Department") or r.get("JobFunction"),
                    url=(
                        f"https://{self.slug}/hcmUI/CandidateExperience/en/sites/"
                        f"{self.site}/job/{r['Id']}"
                    ),
                    posted_at=r.get("PostedDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(r.get("ShortDescriptionStr")),
                    employment_type=r.get("JobType") or r.get("JobSchedule"),
                )
            )
        return jobs
