#!/usr/bin/env python3
"""Record every count contributing to one cold no-filter facet payload."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import lancedb

from headstart import facets
from headstart.search import JobSearch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    searcher = JobSearch(None, table)
    original = facets._count
    rows = []
    lock = threading.Lock()

    def timed(table, where):
        started = time.perf_counter()
        count = original(table, where)
        elapsed = (time.perf_counter() - started) * 1000
        row = {"where": where, "count": count, "elapsed_ms": round(elapsed, 2)}
        with lock:
            rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        return count

    facets._count = timed
    try:
        started = time.perf_counter()
        searcher.facets({})
        wall_ms = (time.perf_counter() - started) * 1000
    finally:
        facets._count = original
    payload = {
        "wall_ms": round(wall_ms, 2),
        "counts": sorted(rows, key=lambda r: -r["elapsed_ms"]),
    }
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")
    print(
        json.dumps(
            {"wall_ms": round(wall_ms, 2), "slowest": payload["counts"][:10]}, indent=2
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
