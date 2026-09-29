#!/usr/bin/env python3
"""List served rows whose title and description fail today's English gate, for a one-off re-gate.

The English gate (`doc_prep.is_english`) runs when a Job is first embedded, and never again on a
row already served. A description that arrived or changed after the embed, or a row embedded
before the gate existed, can leave a non-English posting in an English-only index (#706).
`embed_plan` re-gates every embedded Job it re-evaluates since ADR-0286. This list names the rows
already served that no re-evaluation would reach, so `embed_plan` re-gates them too, on each run
that reads them; the answer is deterministic, so a listed Job that passes simply stays.

Only rows with a description are listed: on a bare title the language guess is too noisy to
evict on. ``--exclude`` leaves out Boards whose text reads non-English for a reason of our own:
on 2026-09-29, `avature:ea`'s English postings carried Spanish page labels (a scraper locale bug,
fixed separately under #706).

Reads the served table straight off HF by column (no 4 GB pull), so it needs an HF token:

    PYTHONPATH=src python scripts/embed/list_non_english_served.py
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from headstart.ingest.doc_prep import is_english

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "config" / "regate_english.txt"
_TABLE = "hf://datasets/imPoseidon/headstart-index/data/lancedb/jobs.lance"


def non_english(rows: list[tuple[str, str, str]]) -> list[str]:
    """The ids of ``(id, title, description)`` rows with a description that fail the gate."""
    return [
        job_id
        for job_id, title, description in rows
        if description.strip() and not is_english(title, description)
    ]


def _served_batches(version: int | None) -> Iterator[list[tuple[str, str, str]]]:
    import lance
    from huggingface_hub import get_token

    ds = lance.dataset(
        _TABLE, version=version, storage_options={"hf_token": get_token()}
    )
    print(f"served table v{ds.version}, {ds.count_rows():,} rows", flush=True)
    for batch in ds.to_batches(columns=["id", "title", "description"], batch_size=5000):
        yield [
            # `is_english` reads the first 500 characters; holding only those keeps memory small.
            (job_id, title or "", (description or "")[:500])
            for job_id, title, description in zip(
                batch.column("id").to_pylist(),
                batch.column("title").to_pylist(),
                batch.column("description").to_pylist(),
                strict=True,
            )
        ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", type=int, default=None, help="table version (latest)")
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="an id prefix to leave out, e.g. avature:ea: (repeatable)",
    )
    args = ap.parse_args()
    # Each batch is gated as it arrives and each failing id written as it is found, so a slow
    # read or a crash loses nothing already gated; the sorted list replaces it at the end.
    partial = args.out.with_suffix(".partial")
    ids: list[str] = []
    with (
        ProcessPoolExecutor(max_workers=args.workers) as pool,
        partial.open("w") as sink,
    ):
        futures = [
            pool.submit(non_english, rows) for rows in _served_batches(args.version)
        ]
        for n, future in enumerate(as_completed(futures), 1):
            found = [
                i for i in future.result() if not i.startswith(tuple(args.exclude))
            ]
            ids.extend(found)
            sink.write("".join(f"{i}\n" for i in found))
            sink.flush()
            if n % 20 == 0:
                print(f"  {n} batches gated, {len(ids):,} fail", flush=True)
    args.out.write_text("".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8")
    partial.unlink()
    print(f"{len(ids):,} ids -> {args.out}", flush=True)
    print(Counter(i.split(":", 1)[0] for i in ids).most_common(10), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
