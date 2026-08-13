#!/usr/bin/env python3
"""Evict ATSes' vectors from the embedding store and the ``jobs`` table.

For when scraped *content* changes (e.g. a scraper starts capturing the requirements
sections): the store is id-keyed, so ``ingest.embed_run --resume`` would keep the stale
vectors forever. Dropping the affected ids from both the store and the table makes the
next ``ingest.embed_run --resume && ingest.index sync`` re-embed and re-add them fresh.

The store's two files are row-aligned (``meta.jsonl`` line N describes ``embeddings.f32``
row N), so eviction rewrites both in lockstep to temp files and swaps them in; the
manifest count is updated last, mirroring the store's own commit-marker convention.

Run:  python scripts/embed/evict_store.py --ats lever,recruitee
      python scripts/embed/evict_store.py --ids data/state/pending_upgrades.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lancedb
import numpy as np

from headstart.search import PROD_TABLE

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "data" / "embeddings" / "jobs"
_DB = _ROOT / "data" / "lancedb"
_ID_CHUNK = 512  # same ceiling index_plan.apply_sync uses, so one predicate can't grow unbounded


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _predicates(column: str, values: set[str]) -> list[str]:
    """Delete predicates for the table, chunked so one `IN (...)` cannot grow unbounded."""
    ordered = sorted(values)
    return [
        f"{column} IN ({', '.join(_quote(v) for v in ordered[start : start + _ID_CHUNK])})"
        for start in range(0, len(ordered), _ID_CHUNK)
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--ats", help="comma-separated ATSes to evict")
    group.add_argument(
        "--ids",
        help="file of Job ids to evict, one per line — the targeted form the embed planner's "
        "upgrade list feeds (ADR-0050)",
    )
    args = ap.parse_args()
    evict = {a.strip() for a in args.ats.split(",") if a.strip()} if args.ats else set()
    evict_ids = (
        {
            line.strip()
            for line in Path(args.ids).read_text().splitlines()
            if line.strip()
        }
        if args.ids
        else set()
    )
    print(
        f"evicting {'ATSes ' + ', '.join(sorted(evict)) if evict else f'{len(evict_ids)} ids'}",
        flush=True,
    )

    manifest = json.loads((_STORE / "manifest.json").read_text())
    dim = manifest["dim"]
    vectors = np.fromfile(_STORE / "embeddings.f32", dtype="float32").reshape(-1, dim)

    kept_meta: list[str] = []
    kept_rows: list[int] = []
    dropped = 0
    with (_STORE / "meta.jsonl").open(encoding="utf-8") as f:
        for row, line in enumerate(f):
            record = json.loads(line)
            if record["ats"] in evict or record["id"] in evict_ids:
                dropped += 1
            else:
                kept_meta.append(line)
                kept_rows.append(row)
    print(f"store: dropping {dropped} of {len(vectors)} rows", flush=True)

    tmp_vec = _STORE / "embeddings.f32.tmp"
    tmp_meta = _STORE / "meta.jsonl.tmp"
    vectors[kept_rows].tofile(tmp_vec)
    tmp_meta.write_text("".join(kept_meta), encoding="utf-8")
    tmp_vec.replace(_STORE / "embeddings.f32")
    tmp_meta.replace(_STORE / "meta.jsonl")
    manifest["count"] = len(kept_meta)
    (_STORE / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"store: {len(kept_meta)} rows remain", flush=True)

    # No skip-list rewrite here any more. The list is keyed on the description store, not on the
    # embedding store (ADR-0050), so dropping a vector no longer discards the text behind it —
    # the re-embed reads the stored description and needs no re-fetch at all. That removes
    # ADR-0048's trap, where an eviction made the next scrape skip exactly what it just threw away.
    table = lancedb.connect(_DB).open_table(PROD_TABLE)
    before = table.count_rows()
    for predicate in _predicates(*(("ats", evict) if evict else ("id", evict_ids))):
        table.delete(predicate)
    print(
        f"table '{PROD_TABLE}': {before} -> {table.count_rows()} rows",
        flush=True,
    )


if __name__ == "__main__":
    main()
