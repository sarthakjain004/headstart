#!/usr/bin/env python3
"""Measure ANN latency and recall against exhaustive cosine search on real query vectors."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lancedb
import numpy as np

FILTERS = (
    ("none", None),
    ("ats", "ats = 'greenhouse'"),
    ("country", "country = 'IN'"),
    (
        "combined",
        "ats = 'greenhouse' AND remote = true AND first_seen >= '2026-09-14'",
    ),
)

IVF_SETTINGS = (
    ("default", {}),
    ("nprobe10", {"nprobes": 10}),
    ("nprobe20", {"nprobes": 20}),
    ("nprobe40", {"nprobes": 40}),
    ("nprobe80", {"nprobes": 80}),
    ("nprobe20_refine1", {"nprobes": 20, "refine": 1}),
    ("nprobe20_refine2", {"nprobes": 20, "refine": 2}),
    ("nprobe20_refine4", {"nprobes": 20, "refine": 4}),
    ("nprobe20_refine8", {"nprobes": 20, "refine": 8}),
    ("nprobe20_refine16", {"nprobes": 20, "refine": 16}),
    ("nprobe40_refine2", {"nprobes": 40, "refine": 2}),
    ("nprobe80_refine2", {"nprobes": 80, "refine": 2}),
    ("adaptive5", {"minimum_nprobes": 5, "maximum_nprobes": 0}),
    ("adaptive20", {"minimum_nprobes": 20, "maximum_nprobes": 0}),
)

HNSW_SETTINGS = (
    ("default", {}),
    ("ef30", {"ef": 30}),
    ("ef60", {"ef": 60}),
    ("ef120", {"ef": 120}),
    ("ef240", {"ef": 240}),
)


def _query(
    table: Any, vector: np.ndarray, where: str | None, setting: dict
) -> list[dict]:
    query = table.search(vector).metric("cosine")
    if where:
        query = query.where(where, prefilter=True)
    if n := setting.get("nprobes"):
        query = query.nprobes(n)
    if n := setting.get("minimum_nprobes"):
        query = query.minimum_nprobes(n)
    if "maximum_nprobes" in setting:
        query = query.maximum_nprobes(setting["maximum_nprobes"])
    if n := setting.get("refine"):
        query = query.refine_factor(n)
    if n := setting.get("ef"):
        query = query.ef(n)
    return query.select(["id", "_distance"]).limit(20).to_list()


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-db", required=True)
    ap.add_argument("--candidate-db", required=True)
    ap.add_argument("--vectors", required=True)
    ap.add_argument("--family", choices=("ivf", "hnsw"), required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    baseline = lancedb.connect(args.baseline_db).open_table("jobs")
    candidate = lancedb.connect(args.candidate_db).open_table("jobs")
    loaded = np.load(args.vectors)
    queries = loaded["queries"].tolist()
    vectors = loaded["vectors"].astype("float32")
    settings = IVF_SETTINGS if args.family == "ivf" else HNSW_SETTINGS

    truth: dict[tuple[str, int], list[str]] = {}
    for filter_name, where in FILTERS:
        for i, vector in enumerate(vectors):
            truth[filter_name, i] = [
                row["id"] for row in _query(baseline, vector, where, {})
            ]
        print(f"ground truth: {filter_name}", flush=True)

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        header = {
            "type": "header",
            "measured_at": datetime.now(UTC).isoformat(),
            "lancedb": lancedb.__version__,
            "family": args.family,
            "queries": queries,
            "indices": [
                {"name": i.name, "type": i.index_type, "columns": i.columns}
                for i in candidate.list_indices()
            ],
        }
        out.write(json.dumps(header, sort_keys=True) + "\n")
        out.flush()
        for setting_name, setting in settings:
            for filter_name, where in FILTERS:
                # Warm the plan/index once at this operating point.
                _query(candidate, vectors[0], where, setting)
                times = []
                recalls = []
                returned = []
                for i, vector in enumerate(vectors):
                    started = time.perf_counter()
                    rows = _query(candidate, vector, where, setting)
                    times.append((time.perf_counter() - started) * 1000)
                    ids = [row["id"] for row in rows]
                    expected = truth[filter_name, i]
                    recalls.append(
                        len(set(ids) & set(expected)) / len(expected)
                        if expected
                        else 1.0
                    )
                    returned.append(len(ids))
                row = {
                    "type": "result",
                    "setting": setting_name,
                    "parameters": setting,
                    "filter": filter_name,
                    "median_ms": round(statistics.median(times), 2),
                    "p95_ms": round(_p95(times), 2),
                    "mean_recall_at_20": round(statistics.mean(recalls), 4),
                    "min_recall_at_20": round(min(recalls), 4),
                    "min_returned": min(returned),
                    "times_ms": [round(v, 2) for v in times],
                    "recalls": [round(v, 4) for v in recalls],
                }
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                print(
                    f"{setting_name:<20} {filter_name:<10} "
                    f"median={row['median_ms']:>7.2f} ms p95={row['p95_ms']:>7.2f} "
                    f"recall={row['mean_recall_at_20']:.3f} "
                    f"min={row['min_recall_at_20']:.3f} returned={row['min_returned']}",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
