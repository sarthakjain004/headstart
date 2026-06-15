"""Run all configured scrapers and assemble the combined job feed."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from headstart.config import CompanyRef
from headstart.models import Job
from headstart.scrapers.registry import get_scraper

_MAX_WORKERS = 8


@dataclass
class RunResult:
    jobs: list[Job] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # "ats:slug" -> message


def scrape_all(companies: list[CompanyRef], max_workers: int = _MAX_WORKERS) -> RunResult:
    """Scrape every company concurrently, deduping by job id and isolating failures.

    Each company runs in its own thread (the work is network-bound). A single
    company that errors is recorded in ``errors`` and skipped; merging of results
    happens on the main thread, so dedup stays deterministic.
    """

    def run_one(company: CompanyRef) -> list[Job]:
        return get_scraper(company.ats, company.slug, company.name).fetch()

    seen: dict[str, Job] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_one, c): c for c in companies}
        for future in as_completed(futures):
            company = futures[future]
            key = f"{company.ats}:{company.slug}"
            try:
                for job in future.result():
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
