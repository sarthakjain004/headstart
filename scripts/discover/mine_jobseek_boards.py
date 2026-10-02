#!/usr/bin/env python3
"""jobseek miner: the Board URLs `colophon-group/jobseek` keeps in `apps/crawler/data/boards.csv`.

jobseek (MIT, agent-driven, changes daily) is a job-search crawler whose `boards.csv` lists one
`board_url` per Board it monitors: 7,885 on 2026-09-29, across Greenhouse, Ashby, Pinpoint,
Recruitee and others. Most are Boards the ledgers hold, but its Pinpoint and Recruitee lists reach
small employers ours miss: 74 of 99 Pinpoint and 23 of 92 Recruitee Boards were unheld. It is a
cross-check rather than an independent source for those two (the Pinpoint sitemap index and the
Common Crawl host graph overlap it), and worth re-reading weekly.

Reads the one 2 MB file (cached under `data/wayback-ats/.jobseek_boards/`; `--refresh` reads it
again), maps each `board_url` host to a ledger row through `board_hosts` (the sub-domain ATSes
only: Pinpoint, Recruitee, Teamtailor, Trakstar, Personio, BambooHR, Breezy, JazzHR, ClearCompany,
Freshteam) and stages what no ledger row holds through `candidate_pool`. Liveness is
`check_liveness.py`'s job.

Run:   python -u scripts/discover/mine_jobseek_boards.py [--refresh]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
       and, for the families staged here that CLAUDE.md names (the run prints the reminder):
       python scripts/validate/dedupe_boards.py --ats recruitee --workers 4   (--apply only when
       no `unreachable`), python scripts/validate/clearcompany_shared_accounts.py
"""

from __future__ import annotations

import argparse
import csv
import io
from urllib.parse import urlsplit

from board_hosts import row_for_host
from candidate_pool import POOL, group_by_ats, stage_by_ats
from discovery_fetch import fetch_cached

URL = "https://raw.githubusercontent.com/colophon-group/jobseek/HEAD/apps/crawler/data/boards.csv"
CACHE = POOL / ".jobseek_boards" / "boards.csv"


def board_rows(boards_csv: str) -> list[tuple[str, str, str]]:
    """`(ats, tenant, url)` for every `board_url` host that is a sub-domain ATS Board."""
    rows = []
    for board in csv.DictReader(io.StringIO(boards_csv)):
        row = row_for_host(urlsplit(board.get("board_url") or "").hostname or "")
        if row:
            rows.append(row)
    return rows


def rows_by_ats(boards_csv: str) -> dict[str, list[tuple[str, str]]]:
    """`{ats: [(tenant, url)]}` for the same Boards."""
    return group_by_ats(board_rows(boards_csv))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--refresh", action="store_true", help="re-download the cached file"
    )
    args = parser.parse_args()
    text = fetch_cached(URL, CACHE, refresh=args.refresh).decode("utf-8")
    rows = board_rows(text)
    print(f"boards.csv read, {len(rows)} Boards on sub-domain ATSes", flush=True)
    stage_by_ats(rows, "jobseek_boards")


if __name__ == "__main__":
    main()
