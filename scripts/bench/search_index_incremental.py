#!/usr/bin/env python3
"""Measure indexed search before/after an incremental append and index optimization."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb
import numpy as np

from headstart.serving.job_search import ANN_NPROBES, ANN_REFINE_FACTOR


def _bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _save(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True, default=str)
        out.write("\n")


def _measure(table, vectors):
    times, recalls = [], []
    for vector in vectors:
        exact = (
            table.search(vector)
            .metric("cosine")
            .bypass_vector_index()
            .select(["id", "_distance"])
            .limit(20)
            .to_list()
        )
        started = time.perf_counter()
        indexed = (
            table.search(vector)
            .metric("cosine")
            .nprobes(ANN_NPROBES)
            .refine_factor(ANN_REFINE_FACTOR)
            .select(["id", "_distance"])
            .limit(20)
            .to_list()
        )
        times.append((time.perf_counter() - started) * 1000)
        wanted = {row["id"] for row in exact}
        got = {row["id"] for row in indexed}
        recalls.append(len(wanted & got) / len(wanted))
    return {
        "median_ms": round(statistics.median(times), 2),
        "mean_recall_at_20": round(statistics.mean(recalls), 4),
        "min_recall_at_20": round(min(recalls), 4),
    }


def _fresh_control(table, vector, job_id: str) -> dict:
    exact = (
        table.search(vector)
        .metric("cosine")
        .bypass_vector_index()
        .select(["id", "_distance"])
        .limit(20)
        .to_list()
    )
    indexed = (
        table.search(vector)
        .metric("cosine")
        .nprobes(ANN_NPROBES)
        .refine_factor(ANN_REFINE_FACTOR)
        .select(["id", "_distance"])
        .limit(20)
        .to_list()
    )
    exact_ids = [row["id"] for row in exact]
    indexed_ids = [row["id"] for row in indexed]
    return {
        "id": job_id,
        "exact_rank": exact_ids.index(job_id) + 1 if job_id in exact_ids else None,
        "indexed_rank": indexed_ids.index(job_id) + 1
        if job_id in indexed_ids
        else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vectors", required=True)
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    db_path = Path(args.db)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = lancedb.connect(db_path).open_table("jobs")
    vectors = np.load(args.vectors)["vectors"].astype("float32")
    vector_index = next(i for i in table.list_indices() if "vector" in i.columns)
    sample = (
        table.search()
        .select(
            [
                "vector",
                "ats",
                "country",
                "remote",
                "employment_type",
                "is_full_time",
                "is_part_time",
                "is_contract",
                "is_internship",
                "posted_at",
                "first_seen",
            ]
        )
        .limit(args.rows)
        .to_list()
    )
    for n, row in enumerate(sample):
        row["id"] = f"greenhouse:incremental:{n}"
        row["ats"] = "greenhouse"
    sentinel = "greenhouse:incremental:fresh-control"
    sample[0]["id"] = sentinel
    sample[0]["vector"] = vectors[0].tolist()

    payload = {
        "rows_before": table.count_rows(),
        "bytes_before": _bytes(db_path),
        "before": _measure(table, vectors),
    }
    _save(dest, payload)
    started = time.perf_counter()
    table.add(sample)
    payload["append_seconds"] = round(time.perf_counter() - started, 3)
    payload["rows_after_append"] = table.count_rows()
    payload["bytes_after_append"] = _bytes(db_path)
    payload["stats_after_append"] = vars(table.index_stats(vector_index.name))
    payload["after_append"] = _measure(table, vectors)
    payload["fresh_control_after_append"] = _fresh_control(table, vectors[0], sentinel)
    _save(dest, payload)

    started = time.perf_counter()
    table.optimize()
    payload["optimize_seconds"] = round(time.perf_counter() - started, 3)
    payload["bytes_after_optimize"] = _bytes(db_path)
    payload["stats_after_optimize"] = vars(table.index_stats(vector_index.name))
    payload["after_optimize"] = _measure(table, vectors)
    payload["fresh_control_after_optimize"] = _fresh_control(
        table, vectors[0], sentinel
    )
    _save(dest, payload)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str), flush=True)
    controls = (
        payload["fresh_control_after_append"],
        payload["fresh_control_after_optimize"],
    )
    return 0 if all(c["exact_rank"] and c["indexed_rank"] for c in controls) else 3


if __name__ == "__main__":
    raise SystemExit(main())
