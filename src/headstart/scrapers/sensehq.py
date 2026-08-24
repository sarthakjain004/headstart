"""SenseHQ job-board scraper ({slug}.sensehq.com).

SenseHQ exposes a clean public JSON feed (no auth) — found by probing, not in any existing
scraper repo:
    https://{slug}.sensehq.com/careers/api/jobs   ->  {"success", "data": {"rows": [...]}}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_PAGE_SIZE = 10  # the API's fixed page size (0-indexed ?page=N)
_MAX_PAGES = 100  # our own ceiling — reaching it means the board went unread


class SenseHQScraper(BaseScraper):
    ats = "sensehq"

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company)
        self._page = 0

    def url(self) -> str:
        return f"https://{self.slug}.sensehq.com/careers/api/jobs?page={self._page}"

    def fetch_raw(self) -> Any:
        # SenseHQ returns 10 rows/page (0-indexed ?page=N) — page through to the count.
        rows: list[dict] = []
        self._page = 0
        while True:
            data = json.loads(self._get()).get("data") or {}
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
        for r in (raw.get("data") or {}).get("rows", []):
            posted = None
            if r.get("created_on"):
                posted = datetime.fromtimestamp(
                    r["created_on"] / 1000, tz=UTC
                ).isoformat()
            location = r.get("location")
            workplace = r.get("workplace_type") or ""
            start, end = r.get("experience_start"), r.get("experience_end")
            experience = (
                f"{start}-{end}" if start is not None and end is not None else None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{r['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(r.get("title") or "").strip(),
                    location=location,
                    remote="remote" in workplace.lower() or is_remote(location),
                    department=r.get("department"),
                    url=f"https://{self.slug}.sensehq.com/careers/jobs/{r['id']}",
                    posted_at=posted,
                    scraped_at=scraped_at,
                    description=html_to_text(r.get("description_external")),
                    experience=experience,
                    employment_type=r.get("job_type"),
                )
            )
        return jobs
