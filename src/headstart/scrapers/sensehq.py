"""SenseHQ job-board scraper ({slug}.sensehq.com).

SenseHQ exposes a clean public JSON feed (no auth) — found by probing, not in any existing
scraper repo:
    https://{slug}.sensehq.com/careers/api/jobs   ->  {"success", "data": {"rows": [...]}}

Measured 2026-09-28 (`docs/sensehq/2026-09-28_careers-api-measurement.md`, ADR-0256): `count` is
the Board's whole total on every page; no rate limit showed over a 9,897-label sweep at 32
concurrent; pages default to 10 rows and `pageSize` is honoured but not used here. Boards are
discovered from Common Crawl, Wayback and a label sieve, and probed by `p_sensehq`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import BaseScraper

_PAGE_SIZE = (
    10  # the API's default page size (0-indexed ?page=N); `pageSize` is not sent
)
_MAX_PAGES = 100  # our own ceiling — reaching it means the board went unread


class SenseHQScraper(BaseScraper):
    ats = "sensehq"
    # from the scraper's construction (job_url below: {slug}.sensehq.com/careers/jobs/{id});
    # ZERO indexed rows today — source-derived only, same caveat oracle's entry used to carry.
    url_shape = r"https://[\w-]+\.sensehq\.com/careers/jobs/\d+"

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        self._page = 0

    def url(self) -> str:
        return f"https://{self.slug}.sensehq.com/careers/api/jobs?page={self._page}"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}.sensehq.com/careers/jobs/{native_id}"

    def fetch_raw(self) -> Any:
        # SenseHQ returns 10 rows/page (0-indexed ?page=N) — page through to the count.
        rows: list[dict] = []
        self._page = 0
        while True:
            payload = json.loads(self._get())
            data = payload.get("data") or {}
            if not rows and "rows" not in data:
                self.note_unreadable_board(
                    "a payload with `data.rows`", f"keys {sorted(payload)[:5]}"
                )
            if not rows:
                stated = data.get(
                    "count", 0
                )  # the first page's, for the shortfall line
            batch = data.get("rows", [])
            rows.extend(batch)
            self._page += 1
            # `data.get("count", 0)` would be a latent truncation bug if the live API ever
            # omitted `count` (a fixture missing it made this look broken in review) — but
            # probed live 2026-08-24 against zetwerk: `count` is always present (32, matching
            # 4 real pages of 10+10+10+2), so `len(rows) >= 0` never happens in practice. Not
            # fixed defensively: guarding against an input the real API never sends would be
            # untestable speculation, the opposite of what CLAUDE.md's measure-first rule asks.
            if len(batch) < _PAGE_SIZE or len(rows) >= data.get("count", 0):
                if len(rows) < stated:
                    # The rest are unread, not closed (ADR-0053); a negligible gap is left to
                    # ADR-0083's grace period (ADR-0121).
                    self.mark_truncated_unless_negligible(
                        len(rows),
                        stated,
                        f"read {len(rows)} of {stated} listed — a short page ended the walk",
                    )
                break
            if self._page > _MAX_PAGES:
                # A separate exit from the two above, because it means something different: the
                # board did not end, we stopped reading it (ADR-0053).
                self.mark_truncated(
                    f"hit the {_MAX_PAGES}-page cap at {len(rows)} of "
                    f"{data.get('count', 0)} jobs — the rest unread"
                )
                break
        return {"data": {"rows": rows}}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        rows = (raw.get("data") or {}).get("rows", [])
        for r in rows:
            title = (r.get("title") or "").strip()
            if not r.get("id") or not title:
                continue
            posted = None
            if r.get("created_on"):
                posted = datetime.fromtimestamp(
                    r["created_on"] / 1000, tz=UTC
                ).isoformat()
            location = _location(r)
            workplace = r.get("workplace_type") or ""
            start, end = r.get("experience_start"), r.get("experience_end")
            experience = (
                f"{start}-{end}" if start is not None and end is not None else None
            )
            jobs.append(
                Job(
                    id=self.job_id(r["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote="remote" in workplace.lower() or is_remote(location),
                    department=r.get("department"),
                    url=self.job_url(r["id"]),
                    posted_at=posted,
                    scraped_at=scraped_at,
                    description=html_to_text(r.get("description_external")),
                    experience=experience,
                    employment_type=r.get("job_type"),
                    requisition=r.get("code"),
                )
            )
        self.note_unread_rows(len(rows) - len(jobs), len(rows), "with no id or title")
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # No pay key in the listing row (all 18 keys read over 229 rows of 27 Boards, 2026-09-28).
        return None


def _location(row: dict) -> str | None:
    """The posting's ``location``, runs of spaces collapsed, with its office's country appended
    when it names a single place. ``location`` is free text and often lists several places
    (nishith-desai: nine, across four countries), while ``office`` is the one hiring office, so
    its country is only safe on a single place. An empty ``location`` falls back to that country
    (zee). Measured 2026-09-28 over 229 rows of 27 live Boards: 209 offices state a country."""
    location = " ".join((row.get("location") or "").split())
    country = ((row.get("office") or {}).get("country") or "").strip()
    if not location:
        return country or None
    if country and "," not in location and country.lower() not in location.lower():
        return f"{location}, {country}"
    return location
