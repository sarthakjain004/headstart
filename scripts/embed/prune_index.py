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

The keep-set is the live liveness ledger (enabled ATSes only), mapped into the index's ``board_of``
key space via each scraper's ``board_key()`` (Workday's id Board is ``{company}/{site}``, not its
URL slug). Canonicalisation is ``board.lower()``; the representative kept per duplicate job is the
lexicographically-smallest Board casing — the same rule ``dedupe_ledger.py`` uses, so a future scrape
re-sees the kept row instead of re-embedding it.

Dry-run by default (reports what it would delete); pass ``--apply`` to execute. Run after
``sync_index.py``. Refuses to apply if the keep-set looks too small to trust (a broken ledger must
not evict the whole index).

Run:  python scripts/embed/prune_index.py            # dry-run
      python scripts/embed/prune_index.py --apply    # delete
Exit: 0 clean/dry-run, 1 on a safety abort.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import lancedb

from headstart.config import load_active_companies
from headstart.corpus import board_of
from headstart.index_sync import apply_sync
from headstart.scrapers.registry import get_scraper
from headstart.search import PROD_TABLE

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "data" / "validate" / "liveness"
_DB = _ROOT / "data" / "lancedb"
_MIN_KEEP_BOARDS = (
    1000  # a healthy ledger has ~40k live Boards; refuse to prune below this
)


def live_keep_set(ledger_dir: str | Path) -> set[str]:
    """Canonical (lowercased) Board keys that should survive: every live ledger Board on an enabled
    ATS, in the index's ``board_of`` key space (via ``board_key()``). ``load_active_companies``
    already drops dead Boards and ``DISABLED_ATS``; ``min_jobs=0`` keeps currently-empty live Boards."""
    keep: set[str] = set()
    for company in load_active_companies(ledger_dir, min_jobs=0):
        try:
            keep.add(
                get_scraper(company.ats, company.slug, company.name).board_key().lower()
            )
        except Exception:  # noqa: BLE001 - a malformed ledger row shouldn't sink the whole set
            continue
    return keep


def plan_prune(index_ids: list[str], keep: set[str]) -> tuple[list[str], list[str]]:
    """Split index ids into (evict_off_board, evict_duplicate).

    ``evict_off_board``: Board not in ``keep``. ``evict_duplicate``: among the survivors, every id
    but the lexicographically-smallest per (lowercased Board, native id) group — the case-variant
    dupes of one job."""
    off_board: list[str] = []
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for jid in index_ids:
        canon = board_of(jid).lower()
        if canon not in keep:
            off_board.append(jid)
            continue
        native = jid.rsplit(":", 1)[1]
        groups[(canon, native)].append(jid)
    duplicate: list[str] = []
    for ids in groups.values():
        if len(ids) > 1:
            duplicate.extend(
                sorted(ids)[1:]
            )  # keep the lex-min Board casing, drop the rest
    return off_board, duplicate


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
        # a readable sample so the operator can eyeball before committing
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
