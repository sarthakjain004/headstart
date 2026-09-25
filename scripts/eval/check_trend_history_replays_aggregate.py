#!/usr/bin/env python3
"""Check that ``headstart.trends.trend_history`` reproduces the aggregate trends ledger at every
tick of a real state (ADR-0230 step 3).

The history serves the index-wide counts at each tick from the Board-delta ledger's replay, and
reads the aggregate ledger (``role_trends.parquet``) only for the archive, the ticks before the
delta ledger began. This compares every tick's ``(metric, family, band, ats)`` counts with the
aggregate's own rows, and prints each mismatching tick as it finds it. The archive's ticks are
read from the aggregate itself, so they are checked too, as a check of the reading.

Run:
  HF_HUB_DISABLE_XET=1 .venv/bin/python -c "from huggingface_hub import snapshot_download; \\
      snapshot_download('imPoseidon/headstart-index', repo_type='dataset', local_dir='<dir>', \\
      allow_patterns=['data/state/*'])"
  PYTHONPATH=src .venv/bin/python -u scripts/eval/check_trend_history_replays_aggregate.py \\
      --state <dir>/data/state
Exit: 0 when every tick matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from headstart.trends.trend_history import TrendHistory

# A config directory the check does not need: the counts do not depend on the taxonomy's labels.
_NO_CONFIG = Path("/nonexistent-trends-config")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--state", type=Path, required=True, help="a data/state directory")
    args = ap.parse_args()

    started = time.perf_counter()
    history = TrendHistory.load(args.state, _NO_CONFIG)
    print(
        f"history: {len(history.ticks)} ticks, loaded in {time.perf_counter() - started:.1f}s",
        flush=True,
    )
    aggregate = pq.read_table(
        args.state / "role_trends.parquet",
        read_dictionary=["metric", "family", "band", "ats"],
    ).sort_by("ts")
    instants = pc.unique(aggregate["ts"]).to_pylist()
    ticks = [t.isoformat(timespec="seconds") for t in instants]
    missing = sorted(set(ticks) - set(history.ticks))
    extra = sorted(set(history.ticks) - set(ticks))
    print(
        f"aggregate: {len(ticks)} ticks; not in the history: {len(missing)}; "
        f"in the history only: {len(extra)}",
        flush=True,
    )
    # the delta ledger's first tick: its files sort by their stamp
    files = sorted((args.state / "role_trend_board_deltas").glob("*.parquet"))
    first_delta = (
        pq.read_table(files[0], columns=["ts"])["ts"][0].as_py() if files else None
    )
    boundaries = pc.value_counts(aggregate["ts"]).field("counts").to_pylist()
    start = 0
    mismatched = {"archive": 0, "replayed": 0}
    checked = {"archive": 0, "replayed": 0}
    for ts, length in zip(ticks, boundaries, strict=True):
        rows = aggregate.slice(start, length).to_pydict()
        start += length
        kind = "replayed" if first_delta and ts >= first_delta else "archive"
        want = {
            (metric, family, band, ats): count
            for metric, family, band, ats, count in zip(
                rows["metric"], rows["family"], rows["band"], rows["ats"], rows["count"]
            )
        }
        got = history.index_counts(ts)
        checked[kind] += 1
        if got != want:
            mismatched[kind] += 1
            differing = {
                k for k in want.keys() | got.keys() if want.get(k) != got.get(k)
            }
            print(
                f"MISMATCH {kind} tick {ts}: {len(differing)} group(s) differ, e.g. "
                f"{sorted(differing)[:3]}",
                flush=True,
            )
    for kind in ("archive", "replayed"):
        print(
            f"{kind}: {checked[kind]} ticks checked, {mismatched[kind]} mismatched",
            flush=True,
        )
    return 1 if missing or extra or any(mismatched.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
