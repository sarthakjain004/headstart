"""Teamtailor job-board scraper ({slug}.teamtailor.com JSON Feed).

Teamtailor publishes a public JSON Feed (jsonfeed.org v1.1) at
``https://{slug}.teamtailor.com/jobs.json``. Each ``items`` entry carries the title, the job
URL, the publish date, the full ``content_html`` (the description), and a schema.org
``_jobposting`` block with the structured location and hiring organization. Everything we need
is inline — no per-job detail fetch.

**The feed is paginated and one page is not the Board.** ``jobs.json`` returns at most
:data:`_PAGE_SIZE` items; ``?page=N`` walks the rest. Measured 2026-08-25 over 766 live Boards:
27 of them (3.5%) sat at exactly 100 items, and paging those out found **4,046 Jobs — 26.4% of
that sample's true corpus — that had never been scraped at all** (``lovisacareers`` serves 779;
we read 100). That is a bigger hole than any field defect, because a Job never fetched cannot be
repaired downstream: it is simply absent from the index, and `sync` sees a Board that shrank.

``?limit=`` and ``?per_page=`` are ignored by the feed (both verified live), so paging is the
only way through.
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

#: Items per page the feed serves. A full page means there is probably another.
_PAGE_SIZE = 100


def _salary(jobposting: dict) -> str | None:
    """Format the schema.org baseSalary MonetaryAmount, e.g. '40000-60000 EUR YEAR'."""
    base = jobposting.get("baseSalary") or {}
    val = base.get("value") or {}
    lo, hi = val.get("minValue"), val.get("maxValue")
    if not lo and not hi:
        return None
    span = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(
        str(x) for x in (span, base.get("currency"), val.get("unitText")) if x
    )


def _location(jobposting: dict) -> str | None:
    """Join the first jobLocation's city/region/country from the schema.org block."""
    locs = jobposting.get("jobLocation") or []
    if not isinstance(locs, list) or not locs:
        return None
    addr = (locs[0] or {}).get("address") or {}
    parts = (
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("addressCountry"),
    )
    return ", ".join(p for p in parts if p) or None


class TeamtailorScraper(BaseScraper):
    ats = "teamtailor"

    def url(self) -> str:
        return f"https://{self.slug}.teamtailor.com/jobs.json"

    def fetch_raw(self) -> Any:
        """Walk every page of the Board — no page-count ceiling.

        Stops on the first short page: proof the Board is exhausted. A full page that adds no
        new ids means the feed is re-serving already-seen content instead of advancing — the one
        case page count alone can't tell apart from "still more real pages ahead" — so that is
        the sole early-exit signal short of a natural end, and it marks the Board truncated
        (ADR-0053): whatever sits past that point is unread, not absent, and `index sync` must
        not read it as a delisting.
        """
        merged: list[dict] = []
        seen: set[Any] = set()
        feed: dict = {}
        page = 1
        while True:
            suffix = "" if page == 1 else f"?page={page}"
            document = json.loads(self._get(f"{self.url()}{suffix}"))
            if page == 1:
                feed = document
            items = document.get("items") or []
            fresh = [i for i in items if i.get("id") not in seen]
            seen.update(i.get("id") for i in fresh)
            merged.extend(fresh)
            if len(items) < _PAGE_SIZE:
                break
            if not fresh:
                self.mark_truncated(
                    f"page {page} repeated no new ids after {len(merged)} Jobs — "
                    "the feed may be ignoring `page`"
                )
                break
            page += 1
        feed["items"] = merged
        return feed

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        feed_company = raw.get("title") or self.company
        jobs: list[Job] = []
        for it in raw.get("items", []):
            jp = it.get("_jobposting") or {}
            location = _location(jp)
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['id']}",
                    ats=self.ats,
                    company=(jp.get("hiringOrganization") or {}).get("name")
                    or feed_company,
                    title=(it.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=None,  # not exposed in the public feed
                    url=it.get("url", ""),
                    posted_at=it.get("date_published") or jp.get("datePosted"),
                    scraped_at=scraped_at,
                    description=html_to_text(it.get("content_html")),
                    employment_type=jp.get("employmentType"),
                    salary=_salary(jp),
                )
            )
        return jobs
