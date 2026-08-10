"""Entry point: `python -m headstart` scrapes all companies and writes the feed."""

from __future__ import annotations

import os
from pathlib import Path

from headstart import log
from headstart.config import load_active_companies, load_companies
from headstart.harvest import build_feed, scrape_all, write_feed
from headstart.tech_filter import filter_jobs

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config" / "companies.toml"
_LEDGER = _ROOT / "data" / "validate" / "liveness"
_OUTPUT = _ROOT / "docs" / "jobs.json"
_JOBS_DIR = _ROOT / "data" / "jobs"
_TECH_DIR = _JOBS_DIR / "tech"


def main() -> None:
    log.setup()
    # Prefer the liveness ledger (its live boards, ADR-0012); fall back to the curated seed if
    # the ledger hasn't been generated yet.
    using_ledger = any(_LEDGER.glob("*.csv"))
    companies = (
        load_active_companies(_LEDGER) if using_ledger else load_companies(_CONFIG)
    )

    # The dashboard feed docs/jobs.json is rebuilt from the per-ATS JSONL (the source of truth) and
    # holds every Job in memory while doing so; at full-harvest scale (the active lists) that OOMs
    # and the file would be gigabytes, so default it off there and rely on the JSONL alone. The
    # small curated seed still builds the feed. HEADSTART_FEED=1/0 forces it on/off.
    feed_env = os.environ.get("HEADSTART_FEED")
    build_dashboard_feed = feed_env == "1" if feed_env is not None else not using_ledger

    # HEADSTART_RESUME=1 continues an interrupted harvest (append + skip already-done boards).
    resume = os.environ.get("HEADSTART_RESUME") == "1"

    result = scrape_all(
        companies, jobs_dir=_JOBS_DIR, progress_every=200, resume=resume
    )

    # Tech filter (ADR-0017): keep only software/tech roles in data/jobs/tech/. Everything downstream
    # — the feed, and the embedding/index/UI — reads the tech subset, not the full scrape, so the
    # embedding model only ever works on the jobs the product actually serves.
    tech = filter_jobs(_JOBS_DIR, _TECH_DIR)
    kept = sum(k for k, _ in tech.values())
    total = sum(t for _, t in tech.values())
    if total:
        print(
            f"tech filter: kept {kept}/{total} ({100 * kept / total:.0f}% tech) "
            f"-> per-ATS JSONL under {_TECH_DIR}"
        )

    if build_dashboard_feed:
        feed = build_feed(_TECH_DIR, result.errors)
        write_feed(feed, _OUTPUT)
        print(f"wrote {feed['count']} tech jobs to {_OUTPUT}")
    else:
        print(
            f"scraped {result.unique} unique jobs from {result.boards} boards "
            f"-> full set under {_JOBS_DIR}, tech subset under {_TECH_DIR}"
        )

    if result.errors:
        print(f"{len(result.errors)} board(s) failed:")
        for key, message in list(result.errors.items())[:10]:
            print(f"  {key}: {message}")
        if len(result.errors) > 10:
            print(f"  ...and {len(result.errors) - 10} more")


if __name__ == "__main__":
    main()
