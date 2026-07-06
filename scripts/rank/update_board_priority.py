#!/usr/bin/env python3
"""Blend the night's tech-job counts into the board-priority ledger (ADR-0022).

Runs after the tech filter: every Board present in the harvest snapshot (``data/jobs``)
gets its EWMA score refreshed from its tech-subset count (``data/jobs/tech``); Boards the
run didn't scrape carry their rows unchanged. The ledger drives the next harvest's slice
ordering and the embed's within-bucket ordering.

Run:  python scripts/rank/update_board_priority.py
Seed: python scripts/rank/update_board_priority.py --jobs data/jobs/tech
      (tech corpus as its own snapshot — for warming the ledger from a full local corpus)
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from headstart.board_priority import load, save, update
from headstart.corpus import board_of, iter_jobs

_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=Path, default=_ROOT / "data" / "jobs")
    ap.add_argument("--tech", type=Path, default=_ROOT / "data" / "jobs" / "tech")
    ap.add_argument(
        "--ledger", type=Path, default=_ROOT / "data" / "state" / "board_priority.csv"
    )
    args = ap.parse_args()

    snapshot_boards = {board_of(j["id"]) for j in iter_jobs(args.jobs)}
    tech_counts = Counter(board_of(j["id"]) for j in iter_jobs(args.tech))
    prev = load(args.ledger)
    rows = update(prev, tech_counts, snapshot_boards)
    save(args.ledger, rows)

    new = sum(1 for b in snapshot_boards if b in rows and b not in prev)
    pruned = sum(1 for b in snapshot_boards if b in prev and b not in rows)
    carried = sum(1 for b in prev if b not in snapshot_boards)
    print(
        f"priority: {len(snapshot_boards)} boards in snapshot | "
        f"{len(rows)} ledger rows ({new} new, {pruned} pruned, {carried} carried) "
        f"-> {args.ledger}",
        file=sys.stderr,
        flush=True,
    )
    top = sorted(rows.items(), key=lambda kv: -kv[1].score)[:10]
    for board, p in top:
        print(
            f"  {p.score:9.1f}  {board} ({p.last_tech_jobs} tech jobs)",
            file=sys.stderr,
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
