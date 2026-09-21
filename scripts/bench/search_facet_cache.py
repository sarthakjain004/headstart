#!/usr/bin/env python3
"""Measure cold and warm facets for the retained table and bounded filter-key cache."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb
from search_table import FACET_CASES, _FixedEncoder, _load_vector

from headstart.search import JobSearch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    searcher = JobSearch(_FixedEncoder(_load_vector(table, args.vector)), table)
    results = []
    for name, filters in FACET_CASES:
        started = time.perf_counter()
        cold = searcher.facets(dict(filters, q="first query"))
        cold_ms = (time.perf_counter() - started) * 1000
        warm = []
        for n in range(25):
            started = time.perf_counter()
            got = searcher.facets(dict(filters, q=f"different query {n}"))
            warm.append((time.perf_counter() - started) * 1000)
            assert got is cold
        row = {
            "case": name,
            "cold_ms": round(cold_ms, 2),
            "warm_median_ms": round(statistics.median(warm), 4),
            "warm_max_ms": round(max(warm), 4),
        }
        results.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, sort_keys=True)
        out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
