"""Run all configured scrapers and assemble the combined job feed."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from headstart.config import CompanyRef
from headstart.models import Job
from headstart.scrapers.registry import get_scraper


@dataclass
class RunResult:
    jobs: list[Job] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # "ats:slug" -> message


def scrape_all(companies: list[CompanyRef]) -> RunResult:
    """Scrape every company, deduping by job id and isolating failures.

    A single company that errors (network blip, slug retired, bad payload) is
    recorded in ``errors`` and skipped; it never aborts the whole run.
    """
    seen: dict[str, Job] = {}
    errors: dict[str, str] = {}
    for company in companies:
        key = f"{company.ats}:{company.slug}"
        try:
            scraper = get_scraper(company.ats, company.slug, company.name)
            for job in scraper.fetch():
                seen[job.id] = job
        except Exception as exc:  # noqa: BLE001 - isolate per-company failures
            errors[key] = f"{type(exc).__name__}: {exc}"
    return RunResult(jobs=list(seen.values()), errors=errors)


def build_feed(result: RunResult) -> dict[str, Any]:
    """Shape a RunResult into the JSON the dashboard consumes."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(result.jobs),
        "errors": result.errors,
        "jobs": [job.to_dict() for job in result.jobs],
    }


def write_feed(feed: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
