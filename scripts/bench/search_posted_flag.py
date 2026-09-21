#!/usr/bin/env python3
"""Test replacing the posting-date LIKE guard with one bitmap-indexed boolean."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lancedb
import numpy as np
from lancedb.index import Bitmap


def _fingerprint(table, where: str) -> tuple[int, str]:
    count = 0
    xor = 0
    for batch in table.search().where(where).select(["id"]).to_batches():
        for job_id in batch.column("id").to_pylist():
            xor ^= int.from_bytes(hashlib.sha256(job_id.encode()).digest())
            count += 1
    return count, f"{xor:064x}"


def _page(table, vector, where: str) -> float:
    def query():
        return (
            table.search(vector)
            .metric("cosine")
            .nprobes(80)
            .refine_factor(2)
            .where(where, prefilter=True)
            .select(["id", "_distance"])
            .limit(20)
            .to_list()
        )

    query()
    samples = []
    for _ in range(11):
        started = time.perf_counter()
        query()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    table.add_columns({"posted_at_comparable": "posted_at LIKE '____-__-__%'"})
    table.create_index("posted_at_comparable", config=Bitmap())
    vector = np.load(args.vector).astype("float32")
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    clean = True
    with dest.open("w", encoding="utf-8") as out:
        for days in (1, 7, 30, 90):
            cutoff = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
            old = f"posted_at >= '{cutoff}' AND posted_at LIKE '____-__-__%'"
            new = f"posted_at >= '{cutoff}' AND posted_at_comparable = true"
            old_count, old_hash = _fingerprint(table, old)
            new_count, new_hash = _fingerprint(table, new)
            row = {
                "days": days,
                "cutoff": cutoff,
                "old_count": old_count,
                "new_count": new_count,
                "old_fingerprint": old_hash,
                "new_fingerprint": new_hash,
                "old_page_ms": round(_page(table, vector, old), 2),
                "new_page_ms": round(_page(table, vector, new), 2),
            }
            clean = clean and old_count == new_count and old_hash == new_hash
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0 if clean else 3


if __name__ == "__main__":
    raise SystemExit(main())
