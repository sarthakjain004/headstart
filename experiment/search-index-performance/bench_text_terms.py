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


def _run(table, vector, where: str) -> tuple[float, list[str]]:
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
    for _ in range(11):
        started = time.perf_counter()
        rows = query()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), [row["id"] for row in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    vector = np.load(args.vector).astype("float32")
    result = []
    for term in TERMS:
        escaped = term.replace("'", "''")
        old = f"lower(title) LIKE '%{escaped}%'"
        new = f"contains(title_search, '{escaped}')"
        old_ms, old_ids = _run(table, vector, old)
        new_ms, new_ids = _run(table, vector, new)
        row = {
            "term": term,
            "old_count": table.count_rows(filter=old),
            "new_count": table.count_rows(filter=new),
            "old_ms": round(old_ms, 2),
            "new_ms": round(new_ms, 2),
            "old_ids": hashlib.sha256("\n".join(sorted(old_ids)).encode()).hexdigest(),
            "new_ids": hashlib.sha256("\n".join(sorted(new_ids)).encode()).hexdigest(),
        }
        result.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        json.dump(result, out, indent=2, sort_keys=True)
        out.write("\n")
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
