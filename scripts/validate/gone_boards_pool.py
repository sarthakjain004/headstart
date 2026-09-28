#!/usr/bin/env python3
"""Write a `check_liveness.py` pool of every ledger row of the Boards the scrape keeps finding gone.

The scrape counts each Board's consecutive gone answers (HTTP 404/410, a host that no longer
resolves) in `data/state/board_failures.csv` (ADR-0058), and quarantines it at `QUARANTINE_AT`. But
only a probe writes the liveness ledger (ADR-0012), so a quarantined Board can stay `live` there,
and Scrapable, for weeks (#701). This hands exactly those Boards to the probe, which decides:

    HF_HUB_DISABLE_XET=1 python -c "from huggingface_hub import hf_hub_download; hf_hub_download(
        'imPoseidon/headstart-index', 'data/state/board_failures.csv', repo_type='dataset',
        local_dir='.')"
    PYTHONPATH=src python scripts/validate/gone_boards_pool.py --out /tmp/gone-pool
    PYTHONPATH=src python scripts/validate/check_liveness.py --dir /tmp/gone-pool --force

Every row of a struck Board goes in, each casing and each Workday data centre, so no older `live`
row is left to keep the Board Scrapable once the others are found dead (ADR-0219). Nothing here
touches the network or the ledger.
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
    ``min_strikes`` gone strikes and still has a ``live`` row. A Board with none is already out."""
    struck = {
        lower_key(board)
        for board, row in board_failures.load(failures).items()
        if row.strikes >= min_strikes
    }
    boards: dict[str, list[liveness_ledger.Verdict]] = {}
    for path in sorted(ledger_dir.glob("*.csv")):
        if path.stem not in SCRAPERS:
            continue
        for v in liveness_ledger.load(path).values():
            board = _board_of_row(company_from_row(path.stem, v.tenant, v.url), v)
            if board is not None and board.lowercase_identity in struck:
                boards.setdefault(board.lowercase_identity, []).append(v)
    pool: dict[str, list[tuple[str, str]]] = {}
    for rows in boards.values():
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
        help="the scrape's gone-strike ledger, pulled from HF",
    )
    ap.add_argument("--ledger-dir", type=Path, default=liveness_ledger.dir_for(ROOT))
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
    pool = pool_rows(args.ledger_dir, args.failures, args.min_strikes)
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
