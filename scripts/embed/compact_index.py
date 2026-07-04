#!/usr/bin/env python3
"""Compact the ``jobs`` LanceDB table and drop old versions (ADR-0020).

Lance keeps every prior version's fragments after ``sync_index.py``'s incremental add/evict
cycles, so on a nightly cadence the on-disk size creeps well past the live rows. Compaction
merges small fragments and ``cleanup_old_versions`` reclaims the superseded ones — run this
after each sync, before the state is uploaded.

Run:  python scripts/embed/compact_index.py
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import lancedb

from headstart.search import PROD_TABLE

_DB = Path(__file__).resolve().parents[2] / "data" / "lancedb"


def main() -> None:
    table = lancedb.connect(_DB).open_table(PROD_TABLE)
    table.compact_files()
    removed = table.cleanup_old_versions(older_than=timedelta(0))
    print(
        f"compacted '{PROD_TABLE}': {table.count_rows()} rows, "
        f"reclaimed {getattr(removed, 'bytes_removed', removed)} bytes",
        flush=True,
    )


if __name__ == "__main__":
    main()
