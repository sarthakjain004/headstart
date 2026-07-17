#!/usr/bin/env python3
"""Compact the LanceDB store by rebuilding it fresh — reclaims on-disk size (ADR-0020, ADR-0023).

``sync_index.py`` / ``prune_index.py`` add and delete rows, and each cycle downloads the whole
``data/lancedb`` from the dataset. ``table.optimize()`` merges fragments and drops old *versions*,
but it does **not** delete fragment files that aren't part of the local version history — and a
week of additive uploads (pre-ADR-0023 ``--delete``) left thousands of such untracked orphans, so
the download-then-optimize path plateaued at ~14 GB. Rewriting each table into a fresh directory
keeps only the live fragments (measured: 1.9 GB → 0.23 GB), and the ``--delete`` upload then prunes
the remote to match. Cheap relative to the download: a couple of hundred MB rewritten in seconds.

Run:  python scripts/embed/compact_index.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import lancedb

_DB = Path(__file__).resolve().parents[2] / "data" / "lancedb"


def main() -> None:
    db = lancedb.connect(_DB)
    names = list(db.list_tables().tables)
    if not names:
        print("no tables to compact", flush=True)
        return

    rebuilt = _DB.with_name(_DB.name + ".rebuild")
    shutil.rmtree(rebuilt, ignore_errors=True)
    fresh = lancedb.connect(rebuilt)
    for name in names:
        rows = db.open_table(name).to_arrow()  # only the live version's rows
        fresh.create_table(name, rows)
        print(
            f"rebuilt '{name}': {fresh.open_table(name).count_rows()} rows", flush=True
        )

    # Swap the rebuilt store in for the bloated one (orphan fragments dropped with the old dir).
    shutil.rmtree(_DB)
    rebuilt.rename(_DB)
    print(f"compacted: rebuilt {len(names)} table(s) fresh at {_DB}", flush=True)


if __name__ == "__main__":
    main()
