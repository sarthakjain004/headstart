#!/usr/bin/env python3
"""Prove each materialized employment flag matches the full legacy Job-id set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import lancedb

from headstart.search_filters.employment_type_filter import RULES


def _set_fingerprint(table, where: str) -> tuple[int, str]:
    count = 0
    xor = 0
    modulus = 1 << 256
    total = 0
    batches = table.search().where(where).select(["id"]).to_batches()
    for batch in batches:
        for job_id in batch.column("id").to_pylist():
            value = int.from_bytes(hashlib.sha256(job_id.encode()).digest())
            xor ^= value
            total = (total + value) % modulus
            count += 1
    return count, f"{xor:064x}:{total:064x}"


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
        for name, rule in RULES.items():
            raw = rule.raw_clause()
            old_count, old_fingerprint = _set_fingerprint(table, raw)
            new_count, new_fingerprint = _set_fingerprint(
                table, f"{rule.column} = true"
            )
            mismatch = table.count_rows(
                filter=f"coalesce(({raw}), false) != {rule.column}"
            )
            row = {
                "filter": name,
                "legacy_count": old_count,
                "flag_count": new_count,
                "legacy_set_fingerprint": old_fingerprint,
                "flag_set_fingerprint": new_fingerprint,
                "row_mismatches": mismatch,
            }
            clean = (
                clean and old_count == new_count and old_fingerprint == new_fingerprint
            )
            clean = clean and mismatch == 0
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0 if clean else 3


if __name__ == "__main__":
    raise SystemExit(main())
