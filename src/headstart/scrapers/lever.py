"""Lever job-board scraper (api.lever.co, with EU-instance fallback).

Lever runs a global instance (api.lever.co) and a separate EU instance (api.eu.lever.co,
behind jobs.eu.lever.co). The company slug alone doesn't say which, so we try global first
and fall back to EU when the slug isn't found there.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, epoch_ms_to_iso, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


def _salary(rng: dict | None) -> str | None:
    """Format Lever's structured salaryRange, e.g. '50000-70000 USD per-year-salary'."""
    rng = rng or {}
    lo, hi = rng.get("min"), rng.get("max")
    if not lo and not hi:
        return None
    span = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(
        str(x) for x in (span, rng.get("currency"), rng.get("interval")) if x
    )


def _description(j: dict) -> str | None:
    """The full posting text: intro + the lists sections (Requirements etc.) + closing.

    ``descriptionPlain`` alone is just the intro — the years-of-experience requirements
    almost always live in ``lists``, so dropping them starves experience extraction and
    the embedding.
    """
    parts = [j.get("descriptionPlain") or j.get("description")]
    for lst in j.get("lists") or []:
        section = "\n".join(s for s in (lst.get("text"), lst.get("content")) if s)
        if section:
            parts.append(section)
    parts.append(j.get("additionalPlain") or j.get("additional"))
    return html_to_text("\n".join(p for p in parts if p))


class LeverScraper(BaseScraper):
    ats = "lever"

    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.slug}?mode=json"

    def fetch_raw(self) -> Any:
        # try the global instance, then EU; a 404 on both means the company isn't on Lever.
        for host in ("api.lever.co", "api.eu.lever.co"):
            response = http.fetch(
                "GET", f"https://{host}/v0/postings/{self.slug}?mode=json"
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json()
        return []

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            categories = j.get("categories") or {}
            location = categories.get("location")
            workplace = (j.get("workplaceType") or "").lower()
            remote = workplace == "remote" or bool(is_remote(location))
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("text") or "").strip(),
                    location=location,
                    remote=remote,
                    department=categories.get("department") or categories.get("team"),
                    url=j.get("hostedUrl", ""),
                    posted_at=epoch_ms_to_iso(j.get("createdAt")),
                    scraped_at=scraped_at,
                    description=_description(j),
                    employment_type=categories.get("commitment"),
                    salary=_salary(j.get("salaryRange")),
                )
            )
        return jobs
