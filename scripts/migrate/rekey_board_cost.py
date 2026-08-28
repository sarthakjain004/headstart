#!/usr/bin/env python3
"""Re-key `data/state/board_cost.csv` from `{ats}:{slug}` to `board_key` (ADR-0096).

    python -u scripts/migrate/rekey_board_cost.py --check     # report, write nothing
    python -u scripts/migrate/rekey_board_cost.py --apply     # rewrite the local file

A one-off. The cost ledger was written by `harvest` under the scraper's raw slug while the
priority ledger was written from Job ids under `board_key`; ADR-0096 makes both use `board_key`,
which means the ledger already on HF has to be rewritten once. There is no self-healing read to
do it lazily: `board_key()` is **not idempotent** — Workday's parses a careers URL and raises
`ValueError` on anything else — so a re-key applied twice fails loudly rather than silently, and
a transitional read path would have to know which rows it had already converted.

Measured 2026-08-28 on the live ledger: **85,839 rows -> 85,825 keys, 14 merged, 0 unmappable**,
and 72,006 rows were already board_key-shaped because 17 of the 20 ATSes have nothing to strip.
Only Workday and Personio move, and only 14 collapse — the pod pairs where both URLs happen to
carry a cost row. Where two rows do collapse, **the newest measurement wins**, since that is the
one describing the Board as it is now; ties go to the larger `seconds`, because under-pricing is
what produces a straggler and over-pricing costs only a little packing slack.

Case is deliberately preserved. `board_key()` does not fold it, and neither does the read side:
`_dedupe_boards` lowercases only to *choose* a survivor and then keeps that row's original
spelling, which is the exact string `scrape_plan` later looks up. Folding here would be a second,
unrelated change — the ADR-0023 case-variant rows are left alone, and were measured contributing
0 s of mispricing.

Upload is deliberately not automated — this rewrites production state. Run `--apply`, check the
diff, then upload with the command it prints.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import log
from headstart.board_cost import BoardCost, load, save
from headstart.scrapers.registry import SCRAPERS

# An explicit dotted name, as `scripts/embed/cluster_roles.py` does. Run by path rather than
# `-m`, `__name__` is "__main__" and `__spec__` is None, so `log.get(__name__, __spec__)`
# yields a logger outside the `headstart` root that reaches no handler and prints nothing.
_log = log.get("headstart.rekey_board_cost")
LEDGER = Path("data/state/board_cost.csv")


def rekey(board: str) -> str | None:
    """The `board_key` for a `{ats}:{slug}` row, or None when no scraper can read it."""
    ats, _, slug = board.partition(":")
    scraper = SCRAPERS.get(ats)
    if scraper is None or not slug:
        return None
    try:
        return scraper(slug).board_key()
    except Exception:  # noqa: BLE001 — a malformed slug keeps its row under the old key
        return None


def better(a: BoardCost, b: BoardCost) -> BoardCost:
    """Of two measurements of one Board, the one to keep."""
    if a.updated_at != b.updated_at:
        return a if a.updated_at > b.updated_at else b
    return a if a.seconds >= b.seconds else b


def migrate(rows: dict[str, BoardCost]) -> tuple[dict[str, BoardCost], int, int]:
    """Returns (re-keyed ledger, rows merged away, rows left under their old key)."""
    grouped: dict[str, list[BoardCost]] = defaultdict(list)
    unmapped = 0
    for board, cost in rows.items():
        key = rekey(board)
        if key is None:
            unmapped += 1
            grouped[board].append(cost)  # keep it rather than lose a measurement
            continue
        grouped[key].append(cost)
    out: dict[str, BoardCost] = {}
    for key, costs in grouped.items():
        best = costs[0]
        for other in costs[1:]:
            best = better(best, other)
        out[key] = best
    return out, len(rows) - len(out), unmapped


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    # `--check` mirrors `scripts/fetch/pull_lancedb.py`: report without touching anything. It
    # is also the default, so the destructive path is always the one you had to ask for.
    ap.add_argument("--check", action="store_true", help="report only (the default)")
    ap.add_argument("--apply", action="store_true", help="rewrite the file")
    ap.add_argument("--ledger", default=str(LEDGER))
    args = ap.parse_args()

    path = Path(args.ledger)
    rows = load(path)
    if not rows:
        _log.error(f"no ledger at {path} — pull it from HF first")
        return 1
    out, merged, unmapped = migrate(rows)
    _log.info(
        f"{len(rows):,} rows -> {len(out):,} board_keys "
        f"({merged:,} merged, {unmapped:,} unmappable and left as-is)"
    )
    already = sum(1 for k in rows if rekey(k) == k)
    _log.info(f"{already:,} rows were already board_key-shaped (17 of 20 ATSes agree)")
    if not args.apply:
        _log.info("--check only; pass --apply to rewrite")
        return 0
    save(path, out)
    _log.info(f"wrote {path}")
    _log.info(
        "upload it with:\n"
        '  python -c "import os;from huggingface_hub import HfApi;'
        "HfApi(token=os.environ['HF_TOKEN']).upload_file("
        "path_or_fileobj='data/state/board_cost.csv',"
        "path_in_repo='data/state/board_cost.csv',"
        "repo_id='imPoseidon/headstart-index',repo_type='dataset',"
        "commit_message='ADR-0096: re-key board_cost.csv to board_key')\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
