#!/usr/bin/env python3
"""Apply the branch's retained migration and index policy to one candidate table."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import lancedb

from headstart.ingest.index import (
    _create_search_indexes,
    _migrate_employment_type_flags,
    _migrate_experience_filter_flags,
    _migrate_posted_at_comparable,
    _migrate_presence_flags,
)


def _bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _save(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    db_path = Path(args.db)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = lancedb.connect(db_path).open_table("jobs")
    before = _bytes(db_path)
    payload = {"status": "building", "bytes_before": before}
    _save(dest, payload)
    started = time.perf_counter()
    _migrate_employment_type_flags(table)
    _migrate_presence_flags(table)
    _migrate_posted_at_comparable(table)
    _migrate_experience_filter_flags(table)
    _create_search_indexes(table)
    seconds = time.perf_counter() - started
    payload = {
        "status": "complete",
        "seconds": round(seconds, 3),
        "bytes_before": before,
        "bytes_after": _bytes(db_path),
        "indices": [
            {"name": i.name, "type": i.index_type, "columns": i.columns}
            for i in table.list_indices()
        ],
    }
    _save(dest, payload)
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
