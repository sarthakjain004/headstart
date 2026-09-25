#!/usr/bin/env python3
"""Test pre/post-filter, projection, and ranked-sort window shapes independently."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb
import numpy as np

from headstart.serving.job_search import ANN_NPROBES, ANN_REFINE_FACTOR, RESULT_COLUMNS

FILTERS = {
    "ats": "ats = 'greenhouse'",
    "country": "country = 'IN'",
    "combined": ("ats = 'greenhouse' AND remote = true AND first_seen >= '2026-09-14'"),
}


def _timed(fn):
    started = time.perf_counter()
    value = fn()
    return (time.perf_counter() - started) * 1000, value


def _query(table, vector, where, prefilter, projection, limit):
    query = (
        table.search(vector)
        .metric("cosine")
        .nprobes(ANN_NPROBES)
        .refine_factor(ANN_REFINE_FACTOR)
    )
    if where:
        query = query.where(where, prefilter=prefilter)
    if projection is not None:
        query = query.select(projection)
    return query.limit(limit).to_list()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-db", required=True)
    ap.add_argument("--candidate-db", required=True)
    ap.add_argument("--vectors", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    baseline = lancedb.connect(args.baseline_db).open_table("jobs")
    candidate = lancedb.connect(args.candidate_db).open_table("jobs")
    vectors = np.load(args.vectors)["vectors"].astype("float32")
    production = [c for c in RESULT_COLUMNS if c in candidate.schema.names] + [
        "_distance"
    ]
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        for name, where in FILTERS.items():
            truth = [
                {
                    row["id"]
                    for row in (
                        baseline.search(vector)
                        .metric("cosine")
                        .where(where, prefilter=True)
                        .select(["id", "_distance"])
                        .limit(20)
                        .to_list()
                    )
                }
                for vector in vectors
            ]
            for prefilter in (True, False):
                times, recalls, returned = [], [], []
                for vector, wanted in zip(vectors, truth, strict=True):
                    elapsed, rows = _timed(
                        lambda vector=vector, where=where, prefilter=prefilter: _query(
                            candidate, vector, where, prefilter, ["id", "_distance"], 20
                        )
                    )
                    got = {row["id"] for row in rows}
                    times.append(elapsed)
                    recalls.append(len(got & wanted) / len(wanted))
                    returned.append(len(got))
                row = {
                    "experiment": "filter_order",
                    "filter": name,
                    "prefilter": prefilter,
                    "median_ms": round(statistics.median(times), 2),
                    "mean_recall_at_20": round(statistics.mean(recalls), 4),
                    "min_recall_at_20": round(min(recalls), 4),
                    "min_returned": min(returned),
                }
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                print(json.dumps(row, sort_keys=True), flush=True)

        for label, projection in (
            ("id_only", ["id", "_distance"]),
            ("production", production),
            ("all_columns", None),
        ):
            times = []
            for vector in vectors:
                elapsed, _ = _timed(
                    lambda vector=vector, projection=projection: _query(
                        candidate, vector, None, True, projection, 20
                    )
                )
                times.append(elapsed)
            row = {
                "experiment": "projection",
                "projection": label,
                "median_ms": round(statistics.median(times), 2),
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)

        vector = vectors[0]
        for limit in (20, 400, 1000, 2000):
            times = []
            for _ in range(7):
                elapsed, rows = _timed(
                    lambda limit=limit: _query(
                        candidate, vector, None, True, production, limit
                    )
                )
                rows.sort(
                    key=lambda row: (row.get("first_seen") or "", row.get("id") or ""),
                    reverse=True,
                )
                times.append(elapsed)
            row = {
                "experiment": "ranked_sort_window",
                "limit": limit,
                "median_ms": round(statistics.median(times), 2),
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
