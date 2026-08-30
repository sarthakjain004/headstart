#!/usr/bin/env python3
"""Measure how many Workday Boards change Job ids under ADR-0097's `_posting_key`.

ADR-0097 stopped `_posting_key` reading the detail response's `jobReqId`, so a Board whose
listing tier disagrees with that value renames every Job on it, once. This is the sweep behind
the ADR's migration figure — committed so the number can be re-derived rather than trusted, after
a first draft published a precise figure from a 4-Board sample.

For each sampled Board it fetches the listing, then for a few postings compares `_posting_key`
(listing-only, post-ADR-0097) against the detail's `jobReqId` (the id served today). A Board
where they disagree is a Board that migrates.

Result 2026-08-30 against the code as shipped, `--boards 140` (102 returned usable
listing+detail data, 27,510 postings): **1 Board (1.0%) and 452 postings (1.6%)** migrate —
`saabgroup/Saab_careers`, which carries no `bulletFields` for its URL to vouch for. Projected
over the 7,620 Scrapable Workday Boards `load_active_companies()` reports — not the cost ledger's
raw rows — that is ~75 Boards and ~17,000 raw postings, or ~1,200 served rows at Workday's ~6.9%
tech keep rate. One migrating Board in 102 is a small numerator; the projection is an order of
magnitude, not a forecast.

Run:
  .venv/bin/python -u scripts/eval/workday_id_migration.py --boards 140
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper, _posting_key

_LEDGER = Path(__file__).resolve().parents[2] / "data" / "state" / "board_cost.csv"


def _boards() -> list[str]:
    """Workday board URLs from the cost ledger — the only local list with the full slug URL."""
    return [
        line.split(",")[0].split(":", 1)[1]
        for line in _LEDGER.read_text(encoding="utf-8").splitlines()
        if line.startswith("workday:https://")
    ]


def _probe(url: str, per_board: int) -> tuple[str, int, float] | None:
    """(url, postings, share of sampled postings whose id changes), or None if unreadable."""
    try:
        scraper = WorkdayScraper(url)
        scraper._resolve_instance()
        postings: list[dict] = []
        scraper._exhaust({}, postings.extend, depth=0)
        if not postings:
            return None
        checked = migrated = 0
        for posting in postings[:per_board]:
            path = posting.get("externalPath")
            if not path:
                continue
            response = http.fetch(
                "GET",
                scraper._detail_url(path),
                timeout=20,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            if response.status_code != 200:
                continue
            served = (response.json().get("jobPostingInfo") or {}).get("jobReqId")
            if not served:
                continue
            checked += 1
            migrated += _posting_key(posting) != served
        return (url, len(postings), migrated / checked) if checked else None
    except Exception as exc:  # noqa: BLE001 - a Board we cannot read is not evidence either way
        print(f"  skip {url}: {type(exc).__name__}", flush=True)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--boards", type=int, default=140, help="Boards to sample (default 140)"
    )
    ap.add_argument(
        "--per-board", type=int, default=4, help="postings checked per Board"
    )
    ap.add_argument(
        "--seed", type=int, default=11, help="sample seed, for reproducibility"
    )
    args = ap.parse_args()

    random.seed(args.seed)
    sample = random.sample(_boards(), min(args.boards, len(_boards())))

    boards = migrating = rows = rows_migrating = 0
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        # `as_completed`, not `pool.map`: map yields in submission order, so one slow Board
        # holds back every result behind it (CLAUDE.md, Repo Conventions).
        futures = [pool.submit(_probe, url, args.per_board) for url in sample]
        for future in cf.as_completed(futures):
            result = future.result()
            if result is None:
                continue
            url, count, share = result
            boards += 1
            rows += count
            if share > 0.5:
                migrating += 1
                rows_migrating += count
                print(
                    f"  MIGRATES {url.split('//')[1]:<58}{count:>6} postings",
                    flush=True,
                )
    if not boards:
        print("no Board returned usable data", flush=True)
        return 2
    print(
        f"\nBoards with usable data: {boards}   migrating: {migrating} "
        f"({migrating / boards:.1%})",
        flush=True,
    )
    print(
        f"postings sampled: {rows}   on migrating Boards: {rows_migrating} "
        f"({rows_migrating / rows:.1%})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
