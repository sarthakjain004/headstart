#!/usr/bin/env python3
"""CrUX origin-list miner: tenant hosts in the Chrome UX Report's origins beyond the top million.

Google's Chrome UX Report lists every origin real Chrome users visited often enough to be reported
(publicly discoverable and "sufficiently popular"), about 18.3M origins a month, ranked into
buckets. The top-1M lists everyone already mines carry only ~37 Boards of the sub-domain ATSes;
the tail buckets (rank 5M, 10M, 50M) hold small employers' career sites, which is where the
Teamtailor, JazzHR, Breezy and BambooHR tenants no ledger row holds are. Measured 2026-09-29 on 43%
of the 50M bucket plus the 5M and 10M buckets: 1,263 Teamtailor Boards, 369 unheld, 35% to 50% of
the unheld ones absent from the Common Crawl host graph too.

The dumps are `github.com/crissyfield/crux-dumps` (a BigQuery export of the CrUX `origin` and `rank`
columns, one `{rank}.txt.xz` per bucket, `https://host` per line). Terms, read 2026-09-29: the repo
declares no licence (GitHub API `license: null`, no LICENSE file), but what it holds is Google's
data, which Google's methodology page licenses as "CrUX datasets by Google are licensed under a
Creative Commons Attribution 4.0 International License". This keeps only the hostnames of tenant
sites, redistributes nothing, and credits the source here: "Chrome UX Report, Google, CC BY 4.0".

Per file: download (resumable, one stream), stream-decompress, keep origins whose host is a tenant of
a `tenant_hosts.FAMILIES` ATS, write `matches-{file}.txt` (the checkpoint), delete the archive. Then
stage through `board_heldness`. Work dir `data/wayback-ats/.crux_origin_list/{month}/` (gitignored).

Run:   python -u scripts/discover/mine_crux_origin_list.py [--month 202608] [--min-rank 5000000]
       (the default takes the 5M, 10M and 50M buckets, 93 MB of xz for 202608)
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
"""

from __future__ import annotations

import argparse
import json
import lzma
import time
from pathlib import Path
from urllib.parse import urlsplit

from board_heldness import POOL, stage_unheld
from discovery_fetch import download, save_whole
from tenant_hosts import FAMILIES, row_for_host

REPO = "https://github.com/crissyfield/crux-dumps/raw/main/"
SUFFIXES = tuple(f".{apex}" for _, apex, _ in FAMILIES)


def latest_month(work: Path) -> str:
    """The newest `YYYYMM` the repo's top-level meta.json lists."""
    meta = work / "meta.json"
    download(REPO + "meta.json", meta)
    months = [
        m["id"]
        for year in json.loads(meta.read_text())["years"]
        for m in year["months"]
    ]
    return max(months)


def month_files(month: str, work: Path) -> list[tuple[str, int]]:
    """`(file name, rank)` for every dump of `month`."""
    meta = work / f"meta-{month}.json"
    download(f"{REPO}{month[:4]}/{month[4:]}/meta.json", meta)
    return [(f["file"], f["rank"]) for f in json.loads(meta.read_text())["files"]]


def tenant_hosts_in(archive: Path) -> tuple[list[str], int]:
    """The distinct tenant hosts in one `.txt.xz` of origins, and the origins scanned."""
    found: set[str] = set()
    rows = 0
    with lzma.open(archive, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            rows += 1
            host = (urlsplit(line.strip()).hostname or "").lower()
            if host.endswith(SUFFIXES) and row_for_host(host):
                found.add(host)
    return sorted(found), rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--month", help="YYYYMM, default the latest the repo lists")
    parser.add_argument(
        "--min-rank",
        type=int,
        default=5_000_000,
        help="skip smaller buckets; the default is everything beyond the top million",
    )
    args = parser.parse_args()
    root = POOL / ".crux_origin_list"
    root.mkdir(parents=True, exist_ok=True)
    month = args.month or latest_month(root)
    work = root / month
    work.mkdir(exist_ok=True)
    files = [
        (name, rank) for name, rank in month_files(month, work) if rank >= args.min_rank
    ]
    print(f"CrUX {month}: {len(files)} buckets from rank {args.min_rank}", flush=True)
    for name, rank in files:
        matches = work / f"matches-{name}.txt"
        if matches.exists():
            continue
        started = time.time()
        archive = work / name
        download(f"{REPO}{month[:4]}/{month[4:]}/{name}", archive)
        hosts, rows = tenant_hosts_in(archive)
        save_whole(matches, "".join(f"{h}\n" for h in hosts).encode())
        size = archive.stat().st_size
        archive.unlink()
        print(
            f"rank {rank:>8}: {size / 1e6:5.1f} MB, {rows:>9,} origins, {len(hosts):>5,} tenant hosts, {time.time() - started:4.0f}s",
            flush=True,
        )
    by_ats: dict[str, list[tuple[str, str]]] = {}
    hosts = sorted(
        {h for p in work.glob("matches-*.txt") for h in p.read_text().split()}
    )
    for host in hosts:
        row = row_for_host(host)
        if row:
            by_ats.setdefault(row[0], []).append((row[1], row[2]))
    print(f"{len(hosts)} distinct tenant hosts in {len(files)} buckets", flush=True)
    for ats, rows in sorted(by_ats.items()):
        print(ats, dict(stage_unheld(ats, rows, "crux_origin_list")), flush=True)


if __name__ == "__main__":
    main()
