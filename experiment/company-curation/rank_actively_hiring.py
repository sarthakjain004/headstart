"""Prototype the "actively hiring" ranking behind a Hot-companies tab, from the ADR-0143
Board-delta ledger — the data that already exists, rather than a new measurement.

Inputs, both from the HF dataset (pull with snapshot_download; ~4 MB total):
  data/state/role_trend_board_counts.parquet   current per-Board (metric, family, band, ats) counts
  data/state/role_trend_board_deltas/*.parquet append-only per-tick changes to that snapshot

Three traps are handled here rather than documented, because each one silently produces a
plausible-looking wrong list:

1. **The first delta tick is a baseline dump.** The ledger began 2026-09-13 with no prior
   snapshot, so tick 1 emits every Board's entire stock as a delta (196,824 rows against the
   next tick's 127). Summing from tick 0 makes every Board look newly created.
2. **`watch:` families double-count** against centroid families (ADR-0051) and must be excluded
   from any total.
3. **`new` is a rolling 7-day *level*, not per-tick inflow** (ADR-0051). Read the level; never
   sum it across ticks.

A fourth trap is only reported, not fixed: a Board whose whole stock appears after the baseline
was newly *discovered*, not newly *hiring* (ADR-0143's own concern). Those are excluded from the
ranked lists and counted separately.

Run:  python -u experiment/company-curation/rank_actively_hiring.py <state_dir>
"""

from __future__ import annotations

import collections
import glob
import sys
from pathlib import Path

import pyarrow.parquet as pq

WATCH = "watch:"  # headstart.roles.WATCH_PREFIX — double-counts, see ADR-0051


def load_levels(counts_path: Path) -> tuple[collections.Counter, collections.Counter]:
    """Current per-Board `new` (rolling 7-day level) and `stock` totals, watch families dropped."""
    table = pq.read_table(counts_path).to_pydict()
    new: collections.Counter = collections.Counter()
    stock: collections.Counter = collections.Counter()
    for board, metric, family, count in zip(
        table["board"], table["metric"], table["family"], table["count"], strict=True
    ):
        if family.startswith(WATCH):
            continue
        (new if metric == "new" else stock)[board] += count
    return new, stock


def net_stock_change(delta_dir: Path) -> collections.Counter:
    """Net change in stock per Board across the window, skipping the baseline tick."""
    files = sorted(glob.glob(str(delta_dir / "*.parquet")))
    moved: collections.Counter = collections.Counter()
    for path in files[1:]:  # files[0] is the baseline dump, not a change
        table = pq.read_table(path).to_pydict()
        for board, metric, family, delta in zip(
            table["board"],
            table["metric"],
            table["family"],
            table["delta"],
            strict=True,
        ):
            if metric == "stock" and not family.startswith(WATCH):
                moved[board] += delta
    return moved


def main(state: Path) -> None:
    new, stock = load_levels(state / "role_trend_board_counts.parquet")
    moved = net_stock_change(state / "role_trend_board_deltas")
    newly_found = {b for b, d in moved.items() if stock[b] > 0 and d >= 0.9 * stock[b]}
    print(
        f"Boards: {len(stock)}   stock {sum(stock.values())}   7d-new {sum(new.values())}"
    )
    print(
        f"changed stock in window: {len(moved)}   newly DISCOVERED (excluded): {len(newly_found)}"
    )

    def show(title: str, rows: list[tuple[str, int, str]], n: int = 12) -> None:
        print(f"\n{title}")
        for board, value, extra in rows[:n]:
            print(f"  {value:>6}  {extra:<30} {board}")

    show(
        "A. VOLUME — 7-day new count (big employers always win)",
        [
            (b, v, f"stock {stock[b]}, net {moved.get(b, 0):+d}")
            for b, v in new.most_common(600)
            if b not in newly_found and stock[b] >= 8
        ],
    )
    show(
        "B. EXPANSION — net stock growth (opening more than it closes)",
        sorted(
            (
                (b, moved[b], f"stock {stock[b]}, 7d-new {new[b]}")
                for b in moved
                if b not in newly_found and stock[b] >= 15 and moved[b] > 0
            ),
            key=lambda r: -r[1],
        ),
    )
    show(
        "C. RATE — 7-day new as a share of open roles (also a churn/agency detector)",
        sorted(
            (
                (
                    b,
                    round(100 * new[b] / stock[b]),
                    f"stock {stock[b]}, 7d-new {new[b]}",
                )
                for b in stock
                if stock[b] >= 40 and b not in newly_found and new[b] > 0
            ),
            key=lambda r: -r[1],
        ),
    )


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/state"))
