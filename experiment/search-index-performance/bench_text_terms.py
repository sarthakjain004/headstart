#!/usr/bin/env python3
"""Compare current substring filters with a normalized FM-indexed column across selectivities."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import lancedb
import numpy as np

TERMS = ("engineer", "senior", "java", "go", "react", "kubernetes", "rust", "c++")


def _run(table, vector, where: str, reps: int) -> tuple[float, list[str]]:
    def query():
        return (
            table.search(vector)
            .metric("cosine")
            .where(where, prefilter=True)
            .select(["id", "_distance"])
            .limit(20)
            .to_list()
        )

    query()
    query()
    samples = []
    rows = []
    for _ in range(reps):
        started = time.perf_counter()
        rows = query()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), [row["id"] for row in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source-column", default="title")
    ap.add_argument("--search-column", default="title_search")
    ap.add_argument("--terms", nargs="+", default=TERMS)
    ap.add_argument("--reps", type=int, default=11)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    vector = np.load(args.vector).astype("float32")
    result = []
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        for term in args.terms:
            escaped = term.replace("'", "''")
            old = f"lower({args.source_column}) LIKE '%{escaped}%'"
            new = f"contains({args.search_column}, '{escaped}')"
            old_ms, old_ids = _run(table, vector, old, args.reps)
            new_ms, new_ids = _run(table, vector, new, args.reps)
            row = {
                "term": term,
                "old_count": table.count_rows(filter=old),
                "new_count": table.count_rows(filter=new),
                "old_ms": round(old_ms, 2),
                "new_ms": round(new_ms, 2),
                "old_ids": hashlib.sha256(
                    "\n".join(sorted(old_ids)).encode()
                ).hexdigest(),
                "new_ids": hashlib.sha256(
                    "\n".join(sorted(new_ids)).encode()
                ).hexdigest(),
            }
            result.append(row)
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return (
        0
        if all(
            r["old_count"] == r["new_count"] and r["old_ids"] == r["new_ids"]
            for r in result
        )
        else 3
    )


if __name__ == "__main__":
    raise SystemExit(main())
