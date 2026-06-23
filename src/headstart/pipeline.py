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


class JobWriter:
    """Stream Jobs to per-ATS JSON Lines files (one full Job per line) under ``jobs_dir``.

    Each ``{jobs_dir}/{ats}.jsonl`` is truncated once at the start of a run, then appended to
    and flushed after every company — so a long local scrape writes incrementally (never
    buffering to the end) and survives a crash with whatever finished already on disk.
    Single-threaded by contract: ``write`` is only called from the scrape_all merge loop.
    """

    def __init__(self, jobs_dir: str | Path, atses: set[str]) -> None:
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Open (truncating) one handle per ATS up front so even an ATS that yields zero jobs
        # this run leaves an empty file rather than stale rows from a previous run.
        self._handles: dict[str, Any] = {
            ats: (self._dir / f"{ats}.jsonl").open("w", encoding="utf-8") for ats in atses
        }

    def write(self, jobs: list[Job]) -> None:
        touched = set()
        for job in jobs:
            handle = self._handles.get(job.ats)
            if handle is None:  # an ats not in the company list — open it lazily, just in case
                handle = self._handles[job.ats] = (self._dir / f"{job.ats}.jsonl").open(
                    "w", encoding="utf-8")
            handle.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")
            touched.add(handle)
        for handle in touched:
            handle.flush()

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()


def scrape_all(
    companies: list[CompanyRef],
    max_workers: int = _MAX_WORKERS,
    jobs_dir: str | Path | None = None,
) -> RunResult:
    """Scrape every company concurrently, deduping by job id and isolating failures.

    Each company runs in its own thread (the work is network-bound). A single
    company that errors is recorded in ``errors`` and skipped; merging of results
    happens on the main thread, so dedup stays deterministic.

    When ``jobs_dir`` is given, each company's Jobs are also streamed to
    ``{jobs_dir}/{ats}.jsonl`` (full Job per line) as it completes, so the scrape's output
    lands on disk incrementally instead of only in the returned result.
    """

    def run_one(company: CompanyRef) -> list[Job]:
        return get_scraper(company.ats, company.slug, company.name).fetch()

    seen: dict[str, Job] = {}
    errors: dict[str, str] = {}
    writer = JobWriter(jobs_dir, {c.ats for c in companies}) if jobs_dir else None
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(run_one, c): c for c in companies}
            for future in as_completed(futures):
                company = futures[future]
                key = f"{company.ats}:{company.slug}"
                try:
                    jobs = future.result()
                except Exception as exc:  # noqa: BLE001 - isolate per-company failures
                    errors[key] = f"{type(exc).__name__}: {exc}"
                    continue
                for job in jobs:
                    seen[job.id] = job
                if writer is not None:
                    writer.write(jobs)
    finally:
        if writer is not None:
            writer.close()
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
