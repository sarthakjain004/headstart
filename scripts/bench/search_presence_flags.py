#!/usr/bin/env python3
"""Verify full Job-id set equality for materialized presence/shape flags."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import lancedb

CASES = (
    ("description_stored", "description IS NOT NULL"),
    ("salary_known", "min_salary_annual IS NOT NULL"),
    ("posted_at_comparable", "posted_at LIKE '____-__-__%'"),
)


def _fingerprint(table, where: str) -> tuple[int, str]:
    count = 0
    xor = 0
    for batch in table.search().where(where).select(["id"]).to_batches():
        for job_id in batch.column("id").to_pylist():
            xor ^= int.from_bytes(hashlib.sha256(job_id.encode()).digest())
            count += 1
    return count, f"{xor:064x}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    table = lancedb.connect(args.db).open_table("jobs")
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    clean = True
    with dest.open("w", encoding="utf-8") as out:
        for column, legacy in CASES:
            old_count, old_hash = _fingerprint(table, legacy)
            new_count, new_hash = _fingerprint(table, f"{column} = true")
            mismatch = table.count_rows(
                filter=f"coalesce(({legacy}), false) != {column}"
            )
            row = {
                "column": column,
                "legacy_count": old_count,
                "flag_count": new_count,
                "legacy_fingerprint": old_hash,
                "flag_fingerprint": new_hash,
                "row_mismatches": mismatch,
            }
            clean = clean and old_count == new_count and old_hash == new_hash
            clean = clean and mismatch == 0
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0 if clean else 3


if __name__ == "__main__":
    raise SystemExit(main())
