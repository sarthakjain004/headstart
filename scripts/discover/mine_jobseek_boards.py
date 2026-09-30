#!/usr/bin/env python3
"""jobseek miner: the Board URLs `colophon-group/jobseek` keeps in `apps/crawler/data/boards.csv`.

jobseek (MIT, agent-driven, changes daily) is a job-search crawler whose `boards.csv` lists one
`board_url` per Board it monitors: 7,885 on 2026-09-29, across Greenhouse, Ashby, Pinpoint,
Recruitee and others. Most are Boards the ledgers hold, but its Pinpoint and Recruitee lists reach
small employers ours miss: 74 of 99 Pinpoint and 23 of 92 Recruitee Boards were unheld. It is a
cross-check rather than an independent source for those two (the Pinpoint sitemap index and the
Common Crawl host graph overlap it), and worth re-reading weekly.

Reads the one 2 MB file (cached under `data/wayback-ats/.jobseek_boards/`), maps each `board_url`
host to a ledger row through `tenant_hosts` (the sub-domain ATSes only: Pinpoint, Recruitee,
Teamtailor, Trakstar, Personio, BambooHR, Breezy, JazzHR, ClearCompany, Freshteam) and stages what
no ledger row holds through `board_heldness`. Liveness is `check_liveness.py`'s job.

Run:   python -u scripts/discover/mine_jobseek_boards.py [--refresh]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
"""

from __future__ import annotations

import argparse
import csv
import io
from urllib.parse import urlsplit

from board_heldness import POOL, stage_unheld
from discovery_fetch import fetch_cached
from tenant_hosts import row_for_host

URL = "https://raw.githubusercontent.com/colophon-group/jobseek/HEAD/apps/crawler/data/boards.csv"
CACHE = POOL / ".jobseek_boards" / "boards.csv"


def rows_by_ats(boards_csv: str) -> dict[str, list[tuple[str, str]]]:
    """`{ats: [(tenant, url)]}` for every `board_url` host that is a sub-domain ATS tenant."""
    by_ats: dict[str, list[tuple[str, str]]] = {}
    for board in csv.DictReader(io.StringIO(boards_csv)):
        row = row_for_host(urlsplit(board.get("board_url") or "").hostname or "")
        if row:
            by_ats.setdefault(row[0], []).append((row[1], row[2]))
    return by_ats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--refresh", action="store_true", help="re-download the cached file"
    )
    args = parser.parse_args()
    text = fetch_cached(URL, CACHE, refresh=args.refresh).decode("utf-8")
    by_ats = rows_by_ats(text)
    print(
        f"boards.csv read, {sum(map(len, by_ats.values()))} tenant Boards", flush=True
    )
    for ats, rows in sorted(by_ats.items()):
        print(ats, dict(stage_unheld(ats, rows, "jobseek_boards")), flush=True)


if __name__ == "__main__":
    main()
