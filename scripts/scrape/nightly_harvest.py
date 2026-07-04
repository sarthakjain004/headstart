#!/usr/bin/env python3
"""Bounded nightly harvest for the CI pipeline (ADR-0020) — scrape a rotating slice of the ledger.

Builds the scrape list straight from the committed liveness ledger (``config.load_active_companies``
with ``min_jobs=0``, so a board that dropped to zero postings is still scraped and its index rows
evict), shuffles it (no seed — a different slice each night, so a time-capped run rotates through
the whole board set over successive nights instead of re-scraping one fixed prefix), caps it at
``--max-boards``, and streams jobs to ``data/jobs/{ats}.jsonl`` via ``pipeline.scrape_all``.

Each run truncates the jsonl — the output is *this run's snapshot*, which is exactly what
``sync_index.py`` wants: eviction is scoped to the Boards present in the snapshot, so a partial
harvest never touches the Boards it skipped (ADR-0014).

Run:  python scripts/scrape/nightly_harvest.py --max-boards 8000
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

from headstart.config import load_active_companies
from headstart.pipeline import scrape_all

ROOT = Path(__file__).resolve().parents[2]
_LEDGER = ROOT / "data" / "validate" / "liveness"
_JOBS_DIR = ROOT / "data" / "jobs"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-boards",
        type=int,
        default=8000,
        help="boards to scrape this run (0 = all live boards)",
    )
    args = ap.parse_args()

    companies = load_active_companies(_LEDGER, min_jobs=0)
    random.shuffle(companies)
    if args.max_boards:
        companies = companies[: args.max_boards]
    print(f"harvest: {len(companies)} boards this run", file=sys.stderr, flush=True)

    start = time.monotonic()
    result = scrape_all(companies, jobs_dir=_JOBS_DIR, progress_every=200)
    elapsed = time.monotonic() - start
    print(
        f"done: {result.unique} jobs from {result.boards} boards in {elapsed:0.0f}s "
        f"({len(result.errors)} board errors)",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
