#!/usr/bin/env python3
"""List served rows whose vector encodes another posting's text, for a one-off re-embed.

A vector is built once, from the text a Job had at first embed. A requisition cloned from
another is embedded with the original's text (a byte-identical vector), and when it is then
rewritten, nothing re-embedded it before ADR-0285 (#694). The clearest evidence is a vector
shared, byte for byte, by rows whose titles differ: at most one of them can be what it encodes.
Every row of such a group is listed; re-embedding the one that was right costs one Doc and
changes nothing.

`update_meta` stamps the listed ids `doc_hash` "stale" (ADR-0285), so `embed_plan` re-embeds each
once, when its Board is next in a Slice. Rows embedded since ADR-0285 carry a real fingerprint
and are never touched by the list.

Reads the served table straight off HF by column (no 4 GB pull), so it needs an HF token:

    HF_HUB_DISABLE_XET=1 PYTHONPATH=src python scripts/embed/list_stale_shared_vectors.py
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "config" / "stale_shared_vectors.txt"
_TABLE = "hf://datasets/imPoseidon/headstart-index/data/lancedb/jobs.lance"


def _norm(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def stale_ids(rows) -> list[str]:
    """Every id in a group of byte-identical vectors whose titles disagree.

    ``rows`` yields ``(id, title, vector_bytes)``."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for job_id, title, vector in rows:
        key = hashlib.blake2b(vector, digest_size=16).hexdigest()
        groups[key].append((job_id, _norm(title)))
    return sorted(
        job_id
        for members in groups.values()
        if len({title for _, title in members}) > 1
        for job_id, _ in members
    )


def _served_rows(version: int | None):
    import lance
    import numpy as np
    from huggingface_hub import get_token

    ds = lance.dataset(
        _TABLE, version=version, storage_options={"hf_token": get_token()}
    )
    total = ds.count_rows()
    print(f"served table v{ds.version}, {total:,} rows", flush=True)
    read = 0
    for batch in ds.to_batches(columns=["id", "title", "vector"], batch_size=8192):
        ids = batch.column("id").to_pylist()
        titles = batch.column("title").to_pylist()
        flat = np.asarray(
            batch.column("vector").values.to_numpy(zero_copy_only=False),
            dtype=np.float32,
        ).reshape(len(ids), -1)
        yield from zip(ids, titles, (row.tobytes() for row in flat), strict=True)
        read += len(ids)
        print(f"read {read:,} of {total:,} rows", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", type=int, default=None, help="table version (latest)")
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()
    ids = stale_ids(_served_rows(args.version))
    args.out.write_text("".join(f"{i}\n" for i in ids), encoding="utf-8")
    print(f"{len(ids):,} ids -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
