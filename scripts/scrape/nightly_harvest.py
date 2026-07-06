#!/usr/bin/env python3
"""Bounded nightly harvest for the CI pipeline (ADR-0020, ADR-0022) — a priority-first slice.

Builds the scrape list straight from the committed liveness ledger (``config.load_active_companies``
with ``min_jobs=0``, so a board that dropped to zero postings is still scraped and its index rows
evict), then orders it by the board-priority ledger: boards with tech-job history first (highest
score first, so a time-budget-truncated run still covers the top boards), with an exploration tail
of randomly rotated unscored boards so discovery never starves. No priority ledger yet → the old
behavior, a pure shuffle. Capped at ``--max-boards``; jobs stream to ``data/jobs/{ats}.jsonl`` via
``pipeline.scrape_all``.

Each run truncates the jsonl — the output is *this run's snapshot*, which is exactly what
``sync_index.py`` wants: eviction is scoped to the Boards present in the snapshot, so a partial
harvest never touches the Boards it skipped (ADR-0014).

Run:  python scripts/scrape/nightly_harvest.py --max-boards 8000
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from headstart.board_priority import load_scores, pick_boards
from headstart.config import load_active_companies
from headstart.pipeline import scrape_all

ROOT = Path(__file__).resolve().parents[2]
_LEDGER = ROOT / "data" / "validate" / "liveness"
_JOBS_DIR = ROOT / "data" / "jobs"
_PRIORITY = ROOT / "data" / "state" / "board_priority.csv"


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
    scores = load_scores(_PRIORITY)
    companies = pick_boards(companies, scores, args.max_boards)
    priority = sum(1 for c in companies if scores.get(f"{c.ats}:{c.slug}", 0.0) > 0.0)
    print(
        f"harvest: {len(companies)} boards this run "
        f"({priority} priority + {len(companies) - priority} exploration)",
        file=sys.stderr,
        flush=True,
    )

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
