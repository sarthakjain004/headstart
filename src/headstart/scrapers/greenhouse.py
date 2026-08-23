"""Greenhouse job-board scraper (boards-api.greenhouse.io).

The bare ``/jobs`` list is metadata-only. ``?content=true`` inlines each posting's full
description (and a ``departments`` array) in the same single request — ~12x the payload but
no per-job fetch — so we use it to populate description and department.
"""

from __future__ import annotations

from typing import Any

from headstart import log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)


class GreenhouseScraper(BaseScraper):
    ats = "greenhouse"

    def url(self) -> str:
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        )

    def fetch_raw(self) -> Any:
        """The default JSON fetch, plus one **observation-only** check on the response envelope.

        Greenhouse answers with ``{"jobs": [...], "meta": {"total": N}}``, and this scraper has
        always read only ``jobs``. Measured 2026-08-23
        (docs/pipeline/2026-08-23_false-board-eviction-root-cause.md §4.1): the API sometimes
        returns a **silently short** list — HTTP 200, valid JSON, no error — and because nothing
        here can detect that, ``index sync`` evicts the missing postings as delistings
        (``databricks`` returned 816 of 821; ``metrostarsystems`` 84 of 90).

        ``len(jobs) != meta.total`` is the obvious guard, and it is deliberately **not** wired to
        :meth:`~BaseScraper.mark_truncated` yet. A sweep of 602 live boards found the two agreeing
        602/602, which only establishes they agree while the response is *healthy* — whether
        ``total`` stays authoritative *during* a short response is exactly the unknown, and
        marking a Board unauthoritative on a signal that might fire always (or never) is the
        failure ADR-0053's guards exist to avoid. So this logs and does nothing else; §4.1 records
        how to read a warning (and, harder, a silence) into a decision on shipping the guard.
        """
        raw = super().fetch_raw()
        total = (raw.get("meta") or {}).get("total")
        listed = len(raw.get("jobs") or [])
        if isinstance(total, int) and total != listed:
            _log.warning(
                f"{self.board_key()}: envelope disagrees — {listed} jobs listed but "
                f"meta.total={total} (delta {total - listed}); the response is short and "
                "says so, so a mark_truncated guard on this signal would fire here"
            )
        return raw

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            location = (j.get("location") or {}).get("name")
            department = (j.get("departments") or [{}])[0].get("name") or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=j.get("company_name") or self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=department,
                    url=j.get("absolute_url", ""),
                    posted_at=j.get("first_published") or j.get("updated_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("content")),
                )
            )
        return jobs
