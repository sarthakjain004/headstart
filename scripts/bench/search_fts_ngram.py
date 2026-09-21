#!/usr/bin/env python3
"""Measure NGRAM FTS and show why its ranked surface cannot replace a boolean keyword filter."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb

TERMS = ("kubernetes", "engineer", "c++")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        for term in TERMS:

            def query(term=term):
                return (
                    table.search(term, query_type="fts")
                    .select(["id", "title", "_score"])
                    .limit(20)
                    .to_list()
                )

            query()
            samples = []
            rows = []
            for _ in range(11):
                started = time.perf_counter()
                rows = query()
                samples.append((time.perf_counter() - started) * 1000)
            escaped = term.replace("'", "''")
            substring = f"lower(title) LIKE '%{escaped}%'"
            ids = [row["id"] for row in rows]
            quoted = ",".join("'" + job_id.replace("'", "''") + "'" for job_id in ids)
            false_positives = (
                table.count_rows(filter=f"id IN ({quoted}) AND NOT ({substring})")
                if ids
                else 0
            )
            row = {
                "term": term,
                "median_ms": round(statistics.median(samples), 2),
                "returned": len(rows),
                "substring_matches": table.count_rows(filter=substring),
                "returned_outside_substring_set": false_positives,
                "semantic_note": "FTS returns a BM25-ranked top-k, not a boolean row filter",
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
