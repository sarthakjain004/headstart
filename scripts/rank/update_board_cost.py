#!/usr/bin/env python3
"""Blend the scrape shards' measured per-board seconds into the cost ledger (ADR-0027).

Runs in the join stage, right after the fragments land. Each scrape shard timed every Board it
scraped and streamed the rows to ``board_cost.csv`` inside its own fragment dir; this reads all of
them and EWMA-blends them into ``data/state/board_cost.csv``, which rides the HF state round-trip
and is what the *next* run's ``plan_scrape.py`` bin-packs on.

Boards no shard scraped keep their existing row untouched — the same partial-harvest rule the
priority ledger follows (ADR-0022). A shard that died mid-write contributes every row it did
flush; only a torn final line is skipped.

Run:  python scripts/rank/update_board_cost.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from headstart.board_cost import ats_medians, load, read_shard_rows, save, update
from headstart.pipeline import COST_FILENAME

_ROOT = Path(__file__).resolve().parents[2]
_FRAGMENTS = _ROOT / "data" / "scrape" / "fragments"
_LEDGER = _ROOT / "data" / "state" / "board_cost.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fragments",
        type=Path,
        default=_FRAGMENTS,
        help="dir of scrape fragment dirs (default: data/scrape/fragments)",
    )
    ap.add_argument(
        "--ledger",
        type=Path,
        default=_LEDGER,
        help="cost ledger to update (default: data/state/board_cost.csv)",
    )
    args = ap.parse_args()

    measured: dict[str, tuple[float, int]] = {}
    shards = 0
    if args.fragments.is_dir():
        for path in sorted(args.fragments.glob(f"*/{COST_FILENAME}")):
            rows = read_shard_rows(path)
            if rows:
                shards += 1
            measured.update(rows)
            print(
                f"[cost] {path.parent.name}: {len(rows)} timed boards",
                file=sys.stderr,
                flush=True,
            )

    prev = load(args.ledger)
    rows = update(prev, measured)
    save(args.ledger, rows)

    new = sum(1 for b in measured if b not in prev)
    total = sum(c.seconds for c in rows.values())
    print(
        f"[cost] {len(measured)} boards timed across {shards} shard(s) | "
        f"{len(rows)} ledger rows ({new} new) | Σ {total / 60:.0f} board-minutes -> {args.ledger}",
        file=sys.stderr,
        flush=True,
    )
    for ats, med in sorted(ats_medians(rows).items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {med:8.1f}s median  {ats}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
