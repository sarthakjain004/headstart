#!/usr/bin/env python3
"""Prune stale and duplicate rows from the production ``jobs`` LanceDB table (ADR-0023).

The incremental, board-scoped sync (``index_sync``) only evicts within Boards scraped this run, so
it can't reach two classes of dead weight — which this sweep removes:

  1. **Rows on Boards no longer live.** A Board that left the scrape list (went dead, dropped from
     the liveness ledger, or belongs to a disabled ATS) is never re-scraped, so ``index_sync`` never
     revisits its rows. They linger forever.
  2. **Case-variant duplicate rows.** The same job indexed under more than one slug casing — Workday
     sites like ``.../External`` vs ``.../external`` produce ``company/External`` and ``company/external``
     Board keys, hence two ids for one job. Same lowercased Board + native id → keep one, drop the rest.

Planning lives in :mod:`headstart.index_prune`; this is the CLI that runs it against the table. The
keep-set is the live ledger (enabled ATSes) mapped into the index's ``board_of`` key space via each
scraper's ``board_key()``. Dry-run by default; ``--apply`` deletes. Run after ``sync_index.py``.
Refuses to apply if the keep-set looks too small to trust (a broken ledger must not evict the index).

Run:  python scripts/embed/prune_index.py            # dry-run
      python scripts/embed/prune_index.py --apply    # delete
Exit: 0 clean/dry-run, 1 on a safety abort.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import lancedb

from headstart.index_prune import live_keep_set, plan_prune
from headstart.index_sync import apply_sync
from headstart.search import PROD_TABLE

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "data" / "validate" / "liveness"
_DB = _ROOT / "data" / "lancedb"
_MIN_KEEP_BOARDS = (
    1000  # a healthy ledger has ~40k live Boards; refuse to prune below this
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="delete (default: dry-run report only)"
    )
    ap.add_argument(
        "--ledger",
        default=str(_LEDGER),
        help="liveness ledger dir (default: data/validate/liveness)",
    )
    ap.add_argument(
        "--db", default=str(_DB), help="lancedb dir (default: data/lancedb)"
    )
    args = ap.parse_args()

    keep = live_keep_set(args.ledger)
    print(f"keep-set: {len(keep)} canonical live Boards (enabled ATSes)", flush=True)
    if len(keep) < _MIN_KEEP_BOARDS:
        print(
            f"[prune] ABORT: keep-set has only {len(keep)} Boards (< {_MIN_KEEP_BOARDS}) — the ledger "
            "looks broken/empty; refusing to prune so a bad ledger can't evict the index.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    index_ids = [
        r["id"] for r in table.search().select(["id"]).limit(max(n, 1)).to_list()
    ]
    off_board, duplicate = plan_prune(index_ids, keep)
    evict = off_board + duplicate
    print(
        f"index: {len(index_ids)} rows | evict {len(evict)} "
        f"({len(off_board)} off-Board + {len(duplicate)} duplicate) -> {len(index_ids) - len(evict)} remain",
        flush=True,
    )

    if not args.apply:
        for label, ids in (("off-Board", off_board), ("duplicate", duplicate)):
            for jid in ids[:8]:
                print(f"  [{label}] {jid}", flush=True)
        print("dry-run — pass --apply to delete", flush=True)
        return 0

    apply_sync(table, [], evict)
    print(
        f"done: pruned {len(evict)} rows; table '{PROD_TABLE}' now holds {table.count_rows()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
