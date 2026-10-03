#!/usr/bin/env python3
"""Compare a Restatement's ticks with the live Trends history over the runs both cover
(ADR-0330 step 3's check).

Each restated tick is stamped when its run's union began (``job_facts.facts_stamp``); the same
run's live tick is stamped later, in ``merge``. So a restated tick is paired with the first live
tick at or after it, within ``--pair-hours``. For each pair it prints the tech stock both count,
index-wide and by family, with watched roles and the non-tech diagnostic left out on both sides
(a Restatement does not count watched roles yet).

A match is the check the owner set before step 3 merges: under constant rules, the restated line
must track the live one. Where rules changed inside the window, the two are meant to differ, and
the diff names the family that moved.

Run: python scripts/eval/compare_restated_trends.py --live data/state --restated data/restated
"""

from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from headstart.trends.role_taxonomy import NON_TECH, WATCH_PREFIX
from headstart.trends.trend_history import DELTAS


def family_stock_by_tick(state_dir: Path) -> dict[str, Counter]:
    """``{tick stamp: {family: tech stock}}``, the history under ``state_dir`` replayed."""
    tables = []
    for path in (state_dir / DELTAS).glob("*.parquet"):
        table = pq.read_table(
            path, columns=["board", "metric", "family", "band", "delta"]
        )
        stamp = (pq.read_schema(path).metadata or {})[b"ts"].decode()
        table = table.filter(pc.equal(table["metric"], "stock"))
        tables.append((stamp, table))
    stock: Counter = Counter()
    out: dict[str, Counter] = {}
    for stamp, table in sorted(tables, key=lambda t: t[0]):
        summed = table.group_by(["board", "family", "band"]).aggregate(
            [("delta", "sum")]
        )
        for board, family, band, delta in zip(
            summed["board"].to_pylist(),
            summed["family"].to_pylist(),
            summed["band"].to_pylist(),
            summed["delta_sum"].to_pylist(),
            strict=True,
        ):
            if family != NON_TECH and not family.startswith(WATCH_PREFIX):
                stock[(board, family, band)] += delta
        if any(n < 0 for n in stock.values()):
            raise ValueError(
                f"negative reconstructed stock at {stamp}: missing history"
            )
        out[stamp] = +stock
    return out


def pairs(restated: list[str], live: list[str], hours: float) -> list[tuple[str, str]]:
    """Each restated tick with the first live tick at or after it, within ``hours``. A live tick
    pairs once: a run whose live tick is missing leaves its restated tick unpaired rather than
    matched to the next run's."""
    paired = []
    used: set[str] = set()
    for index, stamp in enumerate(restated):
        at = bisect_left(live, stamp)
        if at < len(live) and live[at] not in used:
            gap = datetime.fromisoformat(live[at]) - datetime.fromisoformat(stamp)
            next_run = restated[index + 1] if index + 1 < len(restated) else None
            if gap <= timedelta(hours=hours) and (
                next_run is None or live[at] < next_run
            ):
                used.add(live[at])
                paired.append((stamp, live[at]))
    return paired


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", type=Path, default=Path("data/state"))
    ap.add_argument("--restated", type=Path, default=Path("data/restated"))
    ap.add_argument("--pair-hours", type=float, default=3.0)
    ap.add_argument(
        "--max-median-gap",
        type=float,
        default=0.0,
        help="fail (exit 1) when the median |gap| exceeds this share, e.g. 0.01",
    )
    ap.add_argument(
        "--report", type=Path, default=Path("data/restated/comparison.json")
    )
    args = ap.parse_args()

    live = family_stock_by_tick(args.live)
    restated = family_stock_by_tick(args.restated)
    matched = pairs(sorted(restated), sorted(live), args.pair_hours)
    if not matched:
        print("no restated tick has a live tick to pair with", flush=True)
        return 1
    gaps = []
    report = []
    failures = len(restated) - len(matched)
    for restated_stamp, live_stamp in matched:
        mine, theirs = restated[restated_stamp], live[live_stamp]
        total_mine, total_theirs = sum(mine.values()), sum(theirs.values())
        gap = (total_mine - total_theirs) / total_theirs if total_theirs else 0.0
        gaps.append(abs(gap))
        differences = [
            {
                "board": key[0],
                "family": key[1],
                "band": key[2],
                "restated": mine[key],
                "live": theirs[key],
            }
            for key in sorted(mine.keys() | theirs.keys())
            if mine[key] != theirs[key]
        ]
        failures += bool(differences)
        report.append(
            {
                "restated_tick": restated_stamp,
                "live_tick": live_stamp,
                "absolute_job_difference": sum(
                    abs(d["restated"] - d["live"]) for d in differences
                ),
                "differences": differences,
            }
        )
        worst = max(
            set(mine) | set(theirs),
            key=lambda family: abs(mine[family] - theirs[family]),
        )
        print(
            f"{restated_stamp} ~ {live_stamp}: restated {total_mine:,} vs live "
            f"{total_theirs:,} ({gap:+.2%}); widest family {worst} "
            f"{mine[worst]:,} vs {theirs[worst]:,}",
            flush=True,
        )
    print(
        f"{len(matched)} paired ticks; median |gap| {statistics.median(gaps):.2%}, "
        f"max {max(gaps):.2%}",
        flush=True,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {"unpaired_ticks": len(restated) - len(matched), "pairs": report}, indent=2
        )
    )
    if failures:
        print(
            f"VALIDATION FAILED: {failures} unmatched or differing ticks; see {args.report}",
            flush=True,
        )
        return 1
    if (
        args.max_median_gap is not None
        and statistics.median(gaps) > args.max_median_gap
    ):
        print(f"median |gap| is above {args.max_median_gap:.2%}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
