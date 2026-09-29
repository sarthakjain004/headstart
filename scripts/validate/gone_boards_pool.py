#!/usr/bin/env python3
"""Write a `check_liveness.py` pool of every ledger row of the Boards the scrape keeps finding gone.

Two answers are in play, and they are not the same. **Gone** is the scrape's: the Board's listing
answered HTTP 404/410, or its host no longer resolves (`board_failures.is_gone`). **Dead** is the
probe's Liveness verdict, and only a probe writes it to the liveness ledger (ADR-0012). The scrape
counts each Board's consecutive gone scrapes, its `strikes` in `data/state/board_failures.csv`
(ADR-0058), and at `QUARANTINE_AT` (20) drops it from the Slice. So a quarantined Board can keep
its `live` row, and stay Scrapable, for weeks (#701). This hands every Board with at least
`--min-strikes` strikes and a `live` row to the probe, which decides.

Every row of such a Board goes in, each casing and each Workday data centre, so no older `live`
row is left to keep the Board Scrapable once the others are found dead (ADR-0219). Nothing here
touches the network or the ledger.

The default threshold is `QUARANTINE_AT`. #701's re-probe (ledger `checked_at` 2026-09-28) ran at
6 strikes, to take in the Boards on their way to quarantine too, and re-probed every Trakstar row
besides, so #775's inactive-account verdict landed. Trakstar refuses connections at the prober's
default worker count, and a refusal only reads unknown, so its ledger goes at 4 workers. These
commands reproduce that ledger:

    HF_HUB_DISABLE_XET=1 python -c "from huggingface_hub import hf_hub_download; hf_hub_download(
        'imPoseidon/headstart-index', 'data/state/board_failures.csv', repo_type='dataset',
        local_dir='.')"
    PYTHONPATH=src python scripts/validate/gone_boards_pool.py --min-strikes 6 --out /tmp/gone-pool
    PYTHONPATH=src python scripts/validate/check_liveness.py --dir /tmp/gone-pool --force
    LIVENESS_WORKERS=4 PYTHONPATH=src python scripts/validate/check_liveness.py trakstar --dir data/validate/liveness --force
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards import liveness_ledger
from headstart.boards.board_identity import lower_key
from headstart.boards.scrapable_boards import _board_of_row
from headstart.ingest import board_failures
from headstart.scrapers.registry import SCRAPERS, company_from_row


def pool_rows(
    ledger_dir: Path, failures: Path, min_strikes: int
) -> dict[str, list[tuple[str, str]]]:
    """Per ATS, the ``(tenant, url)`` of every ledger row whose Board has at least
    ``min_strikes`` consecutive gone scrapes and still has a ``live`` row. A Board with none is
    already out."""
    gone_boards = {
        lower_key(board)
        for board, row in board_failures.load(failures).items()
        if row.strikes >= min_strikes
    }
    rows_by_board: dict[str, list[liveness_ledger.Verdict]] = {}
    for path in sorted(ledger_dir.glob("*.csv")):
        if path.stem not in SCRAPERS:
            continue
        for v in liveness_ledger.load(path).values():
            board = _board_of_row(company_from_row(path.stem, v.tenant, v.url), v)
            if board is not None and board.lowercase_identity in gone_boards:
                rows_by_board.setdefault(board.lowercase_identity, []).append(v)
    pool: dict[str, list[tuple[str, str]]] = {}
    for rows in rows_by_board.values():
        if any(v.status == liveness_ledger.LIVE for v in rows):
            for v in rows:
                pool.setdefault(v.ats, []).append((v.tenant, v.url))
    return pool


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--failures",
        type=Path,
        default=ROOT / "data" / "state" / "board_failures.csv",
        help="each Board's consecutive gone scrapes (ADR-0058), pulled from HF",
    )
    ap.add_argument(
        "--min-strikes",
        type=int,
        default=board_failures.QUARANTINE_AT,
        help="default: the quarantine threshold",
    )
    ap.add_argument("--out", type=Path, required=True, help="the pool directory")
    args = ap.parse_args()

    if not args.failures.exists():
        sys.exit(
            f"{args.failures} is missing: pull it from HF first (see the docstring)"
        )
    args.out.mkdir(parents=True, exist_ok=True)
    pool = pool_rows(liveness_ledger.dir_for(ROOT), args.failures, args.min_strikes)
    for ats, rows in sorted(pool.items()):
        with (args.out / f"{ats}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["tenant", "url"])
            w.writerows(rows)
        print(f"  {ats}: {len(rows)} rows", flush=True)
    print(
        f"{sum(len(r) for r in pool.values())} rows of Boards at {args.min_strikes}+ strikes "
        f"-> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
