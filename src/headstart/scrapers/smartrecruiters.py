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
than from #202's projection a second time (#227). Trivially reversible — uncomment the two
marked lines to re-enable the 50-page cap.

The postings list has no description; a second pass fetches each posting's detail
(GET .../postings/{id} -> jobAd.sections.jobDescription.text) in a bounded thread pool to
fill it in. A failed detail fetch leaves description None — the job is still kept.
"""

from __future__ import annotations

import json
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


class SmartRecruitersScraper(BaseScraper):
    ats = "smartrecruiters"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?limit={_PAGE_SIZE}"

    def fetch_raw(self) -> Any:
        # First pass: page the listing by `offset` until a short page or the cap. Second pass: fill
        # each posting's description concurrently. The detail pass multiplexes over one HTTP/2
        # connection by default (ADR-0016); a failed fetch leaves ``_description`` None.
        data = json.loads(self._get())
        batch = data.get("content") or []
        postings = list(batch)
        page = 1
        # `and page < _MAX_PAGES` disabled for now — uncapped rollout, see #227.
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
        # page. `capped = page == _MAX_PAGES and len(batch) == _PAGE_SIZE` / the cap-naming note
        # are commented out along with the cap itself (#227): with no cap enforced, `page` reaching
        # `_MAX_PAGES` can no longer be what stopped the loop, so naming it would mislabel a genuine
        # short read as a cap hit.
        total = data.get("totalFound") or 0
        if total > len(postings):
            self.mark_truncated(
                f"read {len(postings)} of {total} postings — the rest unread"
            )
        if self.async_fanout_enabled():
            descriptions = self.fan_out_async(
                postings,
                lambda session, p: self._job_description_async(session, p.get("id")),
            )
        else:
            descriptions = self.fan_out(
                postings,
                lambda p: self._job_description(p.get("id")),
                workers=_DETAIL_WORKERS,
            )
        self.report_detail_gaps(descriptions, "descriptions")
        for posting, description in zip(postings, descriptions):
            posting["_description"] = description
        return data

    def _detail_url(self, posting_id: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings/{posting_id}"

    @staticmethod
    def _extract_description(response: Any) -> str | None:
        """Concatenate the posting-detail jobAd sections into raw HTML (None on non-200).

        qualifications and additionalInformation carry the requirements (years of
        experience etc.); companyDescription is deliberately skipped — it's the same
        boilerplate on every posting and would dilute the embedding.
        """
        if response.status_code != 200:
            return None
        sections = (response.json().get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(k) or {}).get("text")
            for k in ("jobDescription", "qualifications", "additionalInformation")
        ]
        return "\n".join(p for p in parts if p) or None

    def _job_description(self, posting_id: str | None) -> str | None:
        """GET one posting's detail and return its jobDescription (None on failure). Sync path."""
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
            return None  # a missing description must not drop the job
        return self._extract_description(response)

    async def _job_description_async(
        self, session: Any, posting_id: str | None
    ) -> str | None:
        """Same as :meth:`_job_description` but over the shared multiplexed ``AsyncSession``."""
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
        return self._extract_description(response)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for p in raw.get("content", []):
            loc = p.get("location") or {}
            location = (
                loc.get("fullLocation")
                or ", ".join(
                    x
                    for x in (loc.get("city"), loc.get("region"), loc.get("country"))
                    if x
                )
                or None
            )
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
                    description=html_to_text(p.get("_description")),
                    experience=(p.get("experienceLevel") or {}).get("label"),
                    employment_type=(p.get("typeOfEmployment") or {}).get("label"),
                )
            )
        return jobs
