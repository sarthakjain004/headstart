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
``index sync`` wants: eviction is scoped to the Boards present in the snapshot, so a partial
harvest never touches the Boards it skipped (ADR-0014).

Run:  python -m headstart.ingest.scrape_run --max-boards 8000
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from headstart import log
from headstart.board_priority import load_scores, pick_boards
from headstart.config import CompanyRef, load_active_companies
from headstart.harvest import scrape_all
from headstart.ingest import REPO_ROOT

_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_JOBS_DIR = REPO_ROOT / "data" / "jobs"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"

_log = log.get(__name__, __spec__)


def _read_assignment(path: Path) -> list[CompanyRef]:
    """A planner-built board list (JSONL of ``{ats, slug, name}``) — the shard's exact scope."""
    companies: list[CompanyRef] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            companies.append(
                CompanyRef(ats=r["ats"], slug=r["slug"], name=r.get("name"))
            )
    return companies


def _log_board(key: str, jobs: int, error: str | None) -> None:
    """Live per-board line as each board completes — failures at INFO so a shard killed by its
    CI time budget has already streamed them; successes are per-board detail (DEBUG)."""
    if error is not None:
        _log.info(f"{key} failed: {error}")
    else:
        _log.debug(f"{key}: {jobs} jobs")


def _error_summary(errors: dict[str, str]) -> str:
    """Group board errors ("ats:slug" -> "ExcType: message") by exception type x ATS.

    Renders types sorted by count desc as ``{n} {ExcType} ({ats1} n1, {ats2} n2, {ats3} n3,
    +k more)`` (top 3 ATSes), joined by "; "."""
    by_type: dict[str, Counter] = defaultdict(Counter)
    for key, message in errors.items():
        by_type[message.split(":", 1)[0]][key.split(":", 1)[0]] += 1
    parts = []
    for exc_type, atses in sorted(
        by_type.items(), key=lambda item: (-sum(item[1].values()), item[0])
    ):
        ranked = sorted(atses.items(), key=lambda item: (-item[1], item[0]))
        detail = ", ".join(f"{ats} {n}" for ats, n in ranked[:3])
        if len(ranked) > 3:
            detail += f", +{len(ranked) - 3} more"
        parts.append(f"{sum(atses.values())} {exc_type} ({detail})")
    return "; ".join(parts)


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-boards",
        type=int,
        default=8000,
        help="boards to scrape this run (0 = all live boards)",
    )
    ap.add_argument(
        "--assignment",
        help="scrape a planner-built board list (JSONL of {ats,slug,name}) instead of "
        "selecting a slice — the scrape-shard mode (ADR-0026)",
    )
    ap.add_argument(
        "--outdir",
        default=str(_JOBS_DIR),
        help="output dir (default: data/jobs; a scrape shard writes its own fragment)",
    )
    args = ap.parse_args()

    if (
        args.assignment
    ):  # ADR-0026 scrape-shard mode — the planner already selected these boards
        companies = _read_assignment(Path(args.assignment))
        _log.info(f"harvest: {len(companies)} boards from {args.assignment} (shard)")
    else:
        companies = load_active_companies(_LEDGER, min_jobs=0)
        scores = load_scores(_PRIORITY)
        companies = pick_boards(companies, scores, args.max_boards)
        priority = sum(
            1 for c in companies if scores.get(f"{c.ats}:{c.slug}", 0.0) > 0.0
        )
        _log.info(
            f"harvest: {len(companies)} boards this run "
            f"({priority} priority + {len(companies) - priority} exploration)"
        )

    start = time.monotonic()
    result = scrape_all(
        companies,
        jobs_dir=Path(args.outdir),
        progress_every=200,
        on_board=_log_board,
    )
    elapsed = time.monotonic() - start
    if result.errors:
        _log.warning(
            f"{len(result.errors)} board errors: {_error_summary(result.errors)}"
        )
    _log.info(
        f"done: {result.unique} jobs from {result.boards} boards in {elapsed:0.0f}s "
        f"({len(result.errors)} board errors)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
