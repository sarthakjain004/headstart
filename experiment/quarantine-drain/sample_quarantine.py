#!/usr/bin/env python3
"""Draw a stratified sample of the quarantined Boards to re-probe.

The three strata are the ones the ledger itself distinguishes once it is joined to
``board_priority.csv``, and they are expected to behave completely differently:

  recent-producer  a priority row refreshed in the last week — the Board served tech jobs days
                   before it was struck out, so "no longer exists" is already implausible
  stale-producer   a priority row, but older than a week
  never-produced   no priority row at all: the Board has never contributed a tech job

Usage: sample_quarantine.py <state-dir> <out-ids.txt> [seed]
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

RECENT_CUTOFF = "2026-09-15"
QUOTA = {"recent-producer": None, "stale-producer": 60, "never-produced": 100}


def main() -> int:
    state, out = Path(sys.argv[1]), Path(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20260921

    fail = list(csv.DictReader((state / "board_failures.csv").open(encoding="utf-8")))
    prio = {
        r["board"].lower(): r
        for r in csv.DictReader((state / "board_priority.csv").open(encoding="utf-8"))
    }

    strata: dict[str, list[str]] = {k: [] for k in QUOTA}
    for row in fail:
        if int(row["strikes"]) < 5:
            continue
        p = prio.get(row["board"].lower())
        if p is None:
            strata["never-produced"].append(row["board"])
        elif p["updated_at"] >= RECENT_CUTOFF:
            strata["recent-producer"].append(row["board"])
        else:
            strata["stale-producer"].append(row["board"])

    rng = random.Random(seed)
    picked: list[str] = []
    for name, quota in QUOTA.items():
        pool = sorted(strata[name])
        take = pool if quota is None or quota >= len(pool) else rng.sample(pool, quota)
        picked.extend(take)
        print(f"{name:16s} population {len(pool):4d}  sampled {len(take):4d}", flush=True)

    out.write_text("\n".join(sorted(picked)) + "\n", encoding="utf-8")
    print(f"\n{len(picked)} ids -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
