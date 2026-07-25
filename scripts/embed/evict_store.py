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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ats", required=True, help="comma-separated ATSes to evict")
    args = ap.parse_args()
    evict = {a.strip() for a in args.ats.split(",") if a.strip()}

    manifest = json.loads((_STORE / "manifest.json").read_text())
    dim = manifest["dim"]
    vectors = np.fromfile(_STORE / "embeddings.f32", dtype="float32").reshape(-1, dim)

    kept_meta: list[str] = []
    kept_rows: list[int] = []
    dropped = 0
    with (_STORE / "meta.jsonl").open(encoding="utf-8") as f:
        for row, line in enumerate(f):
            if json.loads(line)["ats"] in evict:
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

    table = lancedb.connect(_DB).open_table(PROD_TABLE)
    before = table.count_rows()
    quoted = ", ".join(f"'{a}'" for a in sorted(evict))
    table.delete(f"ats IN ({quoted})")
    print(
        f"table '{PROD_TABLE}': {before} -> {table.count_rows()} rows",
        flush=True,
    )


if __name__ == "__main__":
    main()
