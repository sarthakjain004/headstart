#!/usr/bin/env python3
"""CrUX origin-list miner: Board hosts in the Chrome UX Report's origins beyond the top million.

Google's Chrome UX Report lists every origin real Chrome users visited often enough to be reported
(publicly discoverable and "sufficiently popular"), about 18.3M origins a month, ranked into
buckets. The top-1M lists everyone already mines carry only ~37 Boards of the sub-domain ATSes;
the tail buckets (rank 5M, 10M, 50M) hold small employers' career sites, which is where the
Teamtailor, JazzHR, Breezy and BambooHR Boards no ledger row holds are. Measured 2026-09-29 on 43%
of the 50M bucket plus the 5M and 10M buckets: 1,263 Teamtailor Boards, 369 unheld, 35% to 50% of
the unheld ones absent from the Common Crawl host graph too.

The dumps are `github.com/crissyfield/crux-dumps` (a BigQuery export of the CrUX `origin` and `rank`
columns, one `{rank}.txt.xz` per bucket, `https://host` per line). Terms, read 2026-09-29: the repo
declares no licence (GitHub API `license: null`, no LICENSE file), but what it holds is Google's
data, which Google's methodology page licenses as "CrUX datasets by Google are licensed under a
Creative Commons Attribution 4.0 International License". This keeps only the hostnames of Boards,
redistributes nothing, and credits the source here: "Chrome UX Report, Google, CC BY 4.0".

The repo's `meta.json` files are mutable (the root one grows a month at a time), so they are read
fresh on every run, never resumed and never cached. Per bucket file: download (one stream, resumed
only while the remote is unchanged), stream-decompress, keep origins whose host is a Board of a
`board_hosts.FAMILIES` ATS, write `matches-{file}.txt` (the checkpoint), delete the archive. A month
republished in place is read again with `--refresh`, which ignores the checkpoints. Then stage
through `candidate_pool`. Work dir `data/wayback-ats/.crux_origin_list/{month}/` (gitignored).

Run:   python -u scripts/discover/mine_crux_origin_list.py [--month 202608] [--min-rank 5000000]
       [--refresh]   (the default takes the 5M, 10M and 50M buckets, 93 MB of xz for 202608)
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
       and, for the families staged here that CLAUDE.md names (the run prints the reminder):
       python scripts/validate/dedupe_boards.py --ats recruitee --workers 4   (--apply only when
       no `unreachable`), python scripts/validate/clearcompany_shared_accounts.py
"""

from __future__ import annotations

import argparse
import json
import lzma
import time
from pathlib import Path
from urllib.parse import urlsplit

from board_hosts import FAMILIES, row_for_host
from candidate_pool import POOL, stage_by_ats
from discovery_fetch import download, fetch_bytes, remove_download, save_whole

REPO = "https://github.com/crissyfield/crux-dumps/raw/main/"
SUFFIXES = tuple(f".{apex}" for _, apex, _ in FAMILIES)


def latest_month() -> str:
    """The newest `YYYYMM` the repo's top-level meta.json lists, read fresh."""
    meta = json.loads(fetch_bytes(REPO + "meta.json"))
    return max(m["id"] for year in meta["years"] for m in year["months"])


def month_files(month: str) -> list[tuple[str, int]]:
    """`(file name, rank)` for every dump of `month`, read fresh."""
    meta = json.loads(fetch_bytes(f"{REPO}{month[:4]}/{month[4:]}/meta.json"))
    return [(f["file"], f["rank"]) for f in meta["files"]]


def board_hosts_in(archive: Path) -> tuple[list[str], int]:
    """The distinct Board hosts in one `.txt.xz` of origins, and the origins scanned."""
    found: set[str] = set()
    rows = 0
    with lzma.open(archive, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            rows += 1
            host = (urlsplit(line.strip()).hostname or "").lower()
            if host.endswith(SUFFIXES) and row_for_host(host):
                found.add(host)
    return sorted(found), rows


def mine_bucket(month: str, work: Path, name: str, rank: int, refresh: bool) -> None:
    """One bucket file: download, scan, checkpoint `matches-{name}.txt`, delete the archive."""
    matches = work / f"matches-{name}.txt"
    if matches.exists() and not refresh:
        return
    started = time.time()
    archive = work / name
    download(f"{REPO}{month[:4]}/{month[4:]}/{name}", archive)
    hosts, rows = board_hosts_in(archive)
    save_whole(matches, "".join(f"{h}\n" for h in hosts).encode())
    size = archive.stat().st_size
    remove_download(archive)
    print(
        f"rank {rank:>8}: {size / 1e6:5.1f} MB, {rows:>9,} origins, {len(hosts):>5,} Board hosts, {time.time() - started:4.0f}s",
        flush=True,
    )


def stage(work: Path, buckets: int) -> None:
    hosts = sorted(
        {h for p in work.glob("matches-*.txt") for h in p.read_text().split()}
    )
    print(f"{len(hosts)} distinct Board hosts in {buckets} buckets", flush=True)
    rows = (row for host in hosts if (row := row_for_host(host)))
    stage_by_ats(rows, "crux_origin_list")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--month", help="YYYYMM, default the latest the repo lists")
    parser.add_argument(
        "--min-rank",
        type=int,
        default=5_000_000,
        help="skip smaller buckets; the default is everything beyond the top million",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="read every bucket again, ignoring the checkpoints (a month republished in place)",
    )
    args = parser.parse_args()
    month = args.month or latest_month()
    work = POOL / ".crux_origin_list" / month
    work.mkdir(parents=True, exist_ok=True)
    files = [(name, rank) for name, rank in month_files(month) if rank >= args.min_rank]
    print(f"CrUX {month}: {len(files)} buckets from rank {args.min_rank}", flush=True)
    for name, rank in files:
        mine_bucket(month, work, name, rank, args.refresh)
    stage(work, len(files))


if __name__ == "__main__":
    main()
