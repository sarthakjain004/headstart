#!/usr/bin/env python3
"""JobTech miner: Board hosts in the apply links of Sweden's public job ads (Teamtailor first).

Arbetsförmedlingen's JobTech publishes every open Platsbanken ad (about 43,000) as open data. About
6.6% carry an `application_details.url` on a `*.teamtailor.com` host, which names a Teamtailor
Board. Measured 2026-09-29 on 886 ads read through the **Search API** (not this endpoint): 56
distinct Teamtailor slugs, 9 unheld (16.1%), 8 of 9 live.

This reads **JobStream's `/v2/snapshot`, which the research did not measure**: the endpoint the
vendor's own Getting Started says to use for "COLLECT ALL THE ADS" ("We don't want you to use the
search API for this. It's expensive in terms of band width, CPU cycles"). One request, about
300 MB, no pagination (the Search API stops at offset 2,000, so a full sweep there needs several
hundred windowed requests). Terms and key, read 2026-09-29: "Our open data, open APIs and open
source code is free for anyone to use". The docs say "you need a key to authenticate yourself",
yet the OpenAPI document declares no security scheme and the endpoint answers 200 with no key,
header or account; a keyless read of a doc that says a key is needed is **the owner's call**, not
this script's. Nothing here sends a credential or works around a check.

Streams the snapshot (a JSON array on one line) with `raw_decode`, keeps only the apply-link host
of each ad, appends each new Board host to `hosts.txt` as it is found, maps them through
`board_hosts` and stages the unheld Boards through `candidate_pool`. Ads on a vanity host (about
70% of Teamtailor ads) name no Board and are not read. The download prints its size as it grows.

The server ends a transfer that runs about ten minutes, cleanly and mid-ad, and curl calls that a
success: on the owner's link (0.2 MB/s to this host) only 108.5 MB of the ~300 MB, 9,934 ads,
arrived. `ads` therefore raises `SnapshotCut` on an unclosed array. A cut file is never kept as the
cache: it is renamed `snapshot.cut.json`, what it held is still staged (a partial read is still
real candidates), and the next run downloads again. The snapshot is not gzip-compressed (no
`Content-Encoding` for `Accept-Encoding: gzip`), so a faster link is the only way to the rest.

Work dir `data/wayback-ats/.jobtech_jobstream/` (gitignored).

Run:   python -u scripts/discover/mine_jobtech_jobstream_snapshot.py
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats teamtailor
       (and the reminders the run prints for any other family it stages)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from board_hosts import row_for_host
from candidate_pool import POOL, stage_by_ats
from discovery_fetch import download

SNAPSHOT_URL = "https://jobstream.api.jobtechdev.se/v2/snapshot"
WORK = POOL / ".jobtech_jobstream"
SNAPSHOT = WORK / "snapshot.json"
CUT = WORK / "snapshot.cut.json"
HOSTS = WORK / "hosts.txt"
SOURCE = "jobtech_jobstream_snapshot"
CHUNK = 1 << 20
PROGRESS_EVERY_ADS = 5_000


class SnapshotCut(ValueError):
    """The array ends before its closing `]`: the server closed the stream early."""


class Scan(NamedTuple):
    ads: int
    hosts: int
    cut: bool


def ads(path: Path):
    """Every ad object of the snapshot, decoded one at a time from the array.

    Raises `SnapshotCut` after the last complete ad when the array is never closed: the server
    ended a slow transfer cleanly mid-ad (measured 2026-09-30: 108.5 MB and 9,934 ads read in 9.5
    min at 0.2 MB/s, then a normal end of stream, which curl reports as success)."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = closed = False
    with path.open(encoding="utf-8") as handle:
        while chunk := handle.read(CHUNK):
            buffer += chunk
            pos = 0
            while True:
                while pos < len(buffer) and buffer[pos] in " \n\r\t,[":
                    started = started or buffer[pos] == "["
                    pos += 1
                if pos < len(buffer) and buffer[pos] == "]":
                    closed = True
                if pos >= len(buffer) or buffer[pos] == "]":
                    break
                try:
                    ad, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    break  # the ad continues in the next chunk
                yield ad
                pos = end
            buffer = buffer[pos:]
    if not started:
        raise ValueError(f"{path.name} is not a JSON array")
    if not closed:
        raise SnapshotCut(f"{path.name} ends before the array's closing bracket")


def scan(path: Path, hosts_path: Path) -> Scan:
    """Read the snapshot, appending each new Board host to `hosts_path` (flushed) as it is found."""
    seen: set[str] = set()
    total = 0
    cut = False
    with hosts_path.open("w", encoding="utf-8") as sink:
        try:
            for ad in ads(path):
                total += 1
                url = ((ad.get("application_details") or {}).get("url")) or ""
                host = (urlsplit(url).hostname or "").lower()
                if host and host not in seen and row_for_host(host):
                    seen.add(host)
                    sink.write(f"{host}\n")
                    sink.flush()
                if total % PROGRESS_EVERY_ADS == 0:
                    print(f"  {total:,} ads read, {len(seen)} Board hosts", flush=True)
        except SnapshotCut:
            cut = True
    return Scan(total, len(seen), cut)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    result = None
    if SNAPSHOT.exists():
        result = scan(SNAPSHOT, HOSTS)
        if result.cut:
            SNAPSHOT.replace(CUT)
            print("the cached snapshot was cut; downloading it again", flush=True)
            result = None
    if result is None:
        download(SNAPSHOT_URL, SNAPSHOT, resume=False)
        result = scan(SNAPSHOT, HOSTS)
        if result.cut:
            SNAPSHOT.replace(CUT)
            print(
                f"WARNING: the snapshot ends before the array's closing bracket; the {result.ads} "
                f"ads before the cut are read and the rest are not. It is kept as {CUT.name}; the "
                "next run downloads again, ideally on a faster link.",
                flush=True,
            )
    print(
        f"{result.ads} ads, {result.hosts} distinct Board-shaped apply hosts",
        flush=True,
    )
    hosts = HOSTS.read_text(encoding="utf-8").split()
    stage_by_ats((row for host in sorted(hosts) if (row := row_for_host(host))), SOURCE)


if __name__ == "__main__":
    main()
