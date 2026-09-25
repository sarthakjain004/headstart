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

**A second surface, ``/jobs.rss``, fills ``department`` and ``remote`` — additively, not as a
listing replacement.** ``jobs.json`` genuinely has neither field (its ``_jobposting`` block never
states them); ``jobs.rss`` carries ``<tt:department>`` and ``<remoteStatus>`` for the same
postings, joined on the RSS ``<guid>``, which is byte-identical to the ``jobs.json`` item ``id``
(verified live 2026-09-22 on 3 boards: the guid set equals the id set on every board under the RSS
cap below). Measured over 18 boards / 576 postings: ``remoteStatus`` on 100%, ``tt:department`` on
73.8% — tenant-optional, not a parse gap (two boards state it on zero of 109 combined postings,
nine others state it on every posting).

**``jobs.rss`` caps at 100 items with a non-functional ``page`` param — verified live 2026-09-22**:
``lovisacareers``'s ``jobs.rss`` and ``jobs.rss?page=2`` return byte-identical 445,695-byte bodies,
while its ``jobs.json?page=2`` returns 100 genuinely different ids — so the listing walk above is
unaffected (it already paginates correctly) and only the enrichment join is capped. That leaves
Jobs past the 100th on a board department/remote-blind, same as today, rather than making anything
worse: the join only ever adds fields, never removes or reorders a listed Job (ADR-0053 truncation
semantics: a Job the join doesn't reach is unenriched, not evicted).

``remoteStatus``'s live vocabulary is ``fully``/``hybrid``/``none``/``onsite`` (measured on
``zunogroup``: 2/4/8/13) — not the ``fully``/``none``-only enum a third-party scraper maps.
Followed the repo's own convention for an ambiguous middle value (ashby, workday, bamboohr):
``fully`` -> True, ``none``/``onsite`` -> False, ``hybrid`` -> None.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import BaseScraper

#: Items per page the feed serves. A full page means there is probably another.
_PAGE_SIZE = 100

_RSS_NS = {"tt": "https://teamtailor.com/locations"}

#: `remoteStatus`'s live vocabulary (module docstring) mapped the same way ashby/workday/bamboohr
#: resolve an explicit "hybrid" flag: True/False on the unambiguous ends, None (unknown) on the
#: middle rather than guessing.
_REMOTE_STATUS = {"fully": True, "none": False, "onsite": False}


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
    url_shape = r"https://.+/jobs/\d+.*"

    def url(self) -> str:
        return f"https://{self.slug}.teamtailor.com/jobs.json"

    def job_url(self, url: str) -> str:
        """The feed states the job's own link directly (``url``); nothing to build, so this
        simply names that as the declared source (ADR-0153)."""
        return url

    def rss_url(self) -> str:
        return f"https://{self.slug}.teamtailor.com/jobs.rss"

    def _rss_enrichment(self) -> dict[str, dict]:
        """``{guid: {"department", "remote"}}`` off ``jobs.rss`` — additive, capped at the
        feed's own first 100 items (module docstring). Never raises: a fetch or parse failure
        here must not cost the Board its listing, so it degrades to no enrichment for this run
        rather than failing `fetch_raw`.
        """
        try:
            body = self._get(self.rss_url())
        except http.RequestsError as exc:
            self._log.info(f"{self.board_key()}: jobs.rss enrichment skipped ({exc})")
            return {}
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            self._log.info(f"{self.board_key()}: jobs.rss did not parse ({exc})")
            return {}
        enrichment: dict[str, dict] = {}
        for item in root.iter("item"):
            guid = item.findtext("guid")
            if not guid:
                continue
            department = item.findtext("tt:department", namespaces=_RSS_NS)
            status = item.findtext("remoteStatus")
            enrichment[guid] = {
                "department": department.strip() if department else None,
                "remote": _REMOTE_STATUS.get(status) if status else None,
            }
        return enrichment

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
                if "items" not in document:
                    self.note_unreadable_board(
                        "a JSON Feed with `items`", f"keys {sorted(document)[:5]}"
                    )
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
        feed["_rss_enrichment"] = self._rss_enrichment()
        return feed

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        feed_company = raw.get("title") or self.company
        enrichment = raw.get("_rss_enrichment") or {}
        jobs: list[Job] = []
        for it in raw.get("items", []):
            jp = it.get("_jobposting") or {}
            location = _location(jp)
            enriched = enrichment.get(it["id"]) or {}
            jobs.append(
                Job(
                    id=self.job_id(it["id"]),
                    ats=self.ats,
                    company=(jp.get("hiringOrganization") or {}).get("name")
                    or feed_company,
                    title=(it.get("title") or "").strip(),
                    location=location,
                    remote=(
                        enriched["remote"]
                        if "remote" in enriched
                        else is_remote(location)
                    ),
                    department=enriched.get(
                        "department"
                    ),  # jobs.rss join (jobs.json omits it)
                    url=self.job_url(it.get("url", "")),
                    posted_at=it.get("date_published") or jp.get("datePosted"),
                    scraped_at=scraped_at,
                    description=html_to_text(it.get("content_html")),
                    employment_type=jp.get("employmentType"),
                    salary=self._salary_field(jp),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """Format the schema.org baseSalary MonetaryAmount, e.g. '40000-60000 EUR YEAR'."""
        base = raw.get("baseSalary") or {}
        val = base.get("value") or {}
        lo, hi = val.get("minValue"), val.get("maxValue")
        if not lo and not hi:
            return None
        return salary.to_field(
            lo or hi,
            hi if lo and hi else None,
            base.get("currency"),
            val.get("unitText"),
        )
