#!/usr/bin/env python3
"""Build one named index profile and record its time and on-disk cost."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lancedb
from lancedb.index import (
    FTS,
    Bitmap,
    BTree,
    Fm,
    HnswFlat,
    HnswPq,
    HnswSq,
    IvfFlat,
    IvfPq,
    IvfSq,
)

PROFILES: dict[str, tuple[tuple[str, Any], ...]] = {
    "ats_bitmap": (("ats", Bitmap()),),
    "ats_btree": (("ats", BTree()),),
    "posted_btree": (("posted_at", BTree()),),
    "first_seen_btree": (("first_seen", BTree()),),
    "country_bitmap": (("country", Bitmap()),),
    "remote_bitmap": (("remote", Bitmap()),),
    "min_years_btree": (("min_years", BTree()),),
    "salary_group": (
        ("salary_currency", Bitmap()),
        ("min_salary_annual", BTree()),
        ("max_salary_annual", BTree()),
    ),
    "title_fm": (("title", Fm()),),
    "title_fts_ngram": (
        (
            "title",
            FTS(
                base_tokenizer="ngram",
                lower_case=True,
                stem=False,
                remove_stop_words=False,
                ngram_min_length=3,
                ngram_max_length=3,
            ),
        ),
    ),
    "location_fm": (("location", Fm()),),
    "scalar_candidate": (
        ("ats", Bitmap()),
        ("posted_at", BTree()),
        ("first_seen", BTree()),
    ),
    "ivf_pq": (("vector", IvfPq(distance_type="cosine")),),
    "ivf_sq": (("vector", IvfSq(distance_type="cosine")),),
    "ivf_flat": (("vector", IvfFlat(distance_type="cosine")),),
    "hnsw_pq": (("vector", HnswPq(distance_type="cosine", num_partitions=1)),),
    "hnsw_sq": (("vector", HnswSq(distance_type="cosine", num_partitions=1)),),
    "hnsw_flat": (("vector", HnswFlat(distance_type="cosine", num_partitions=1)),),
}


def _bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _save(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True, default=str)
        out.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    db_path = Path(args.db)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = lancedb.connect(db_path).open_table("jobs")
    before = _bytes(db_path)
    builds = []
    payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "lancedb": lancedb.__version__,
        "profile": args.profile,
        "rows": table.count_rows(),
        "bytes_before": before,
        "builds": builds,
        "status": "building",
    }
    _save(dest, payload)
    for column, config in PROFILES[args.profile]:
        started = time.perf_counter()
        table.create_index(column, config=config, replace=True)
        elapsed = time.perf_counter() - started
        index = next(i for i in table.list_indices() if column in i.columns)
        stats = table.index_stats(index.name)
        builds.append(
            {
                "column": column,
                "config": type(config).__name__,
                "seconds": round(elapsed, 3),
                "index": {
                    "name": index.name,
                    "type": index.index_type,
                    "columns": index.columns,
                },
                "stats": None if stats is None else vars(stats),
            }
        )
        print(
            f"{args.profile}: {column} {type(config).__name__} built in {elapsed:.2f}s",
            flush=True,
        )
        _save(dest, payload)

    after = _bytes(db_path)
    payload.update(
        {
            "status": "complete",
            "bytes_after": after,
            "bytes_added": after - before,
        }
    )
    _save(dest, payload)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
