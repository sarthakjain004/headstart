#!/usr/bin/env python3
"""JobTech miner: Board hosts in the apply links of Sweden's public job ads (Teamtailor first).

Arbetsförmedlingen's JobTech publishes every open Platsbanken ad (about 43,000) as open data. About
6.6% carry an `application_details.url` on a `*.teamtailor.com` host, which names a Teamtailor
tenant. Measured 2026-09-29 on 886 ads read through the Search API: 56 distinct Teamtailor slugs, 9
unheld (16.1%), 8 of 9 live.

Reads JobStream's `/v2/snapshot`, the endpoint the vendor's own Getting Started says to use for
"COLLECT ALL THE ADS" ("We don't want you to use the search API for this. It's expensive in terms of
band width, CPU cycles"): one request, about 300 MB, no pagination (the Search API stops at offset
2,000, so a full sweep there needs several hundred windowed requests). Terms and key, read
2026-09-29: "Our open data, open APIs and open source code is free for anyone to use"; the docs say
"you need a key to authenticate yourself", yet the OpenAPI document declares no security scheme and
the endpoint answers 200 with no key, header or account (the docs read as stale; a general key was
reported published, never verified). Nothing here sends a credential or works around a check.

Streams the snapshot (a JSON array on one line) with `raw_decode`, keeps only the apply-link host
of each ad, maps it through `tenant_hosts` and stages the unheld Boards through `board_heldness`.
Ads on a vanity host (about 70% of Teamtailor ads) name no tenant and are not read.

The server ends a transfer that runs about ten minutes, cleanly and mid-ad, and curl calls that a
success: on the owner's link (0.2 MB/s to this host) only 108.5 MB of the ~300 MB, 9,934 ads,
arrived. `ads` therefore raises `SnapshotCut` on an unclosed array, and `main` says so and stages
what it read (a partial read is still real candidates). The snapshot is not gzip-compressed
(no `Content-Encoding` for `Accept-Encoding: gzip`), so a faster link is the only way to the rest.

Run:   python -u scripts/discover/mine_jobtech_jobstream_snapshot.py
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats teamtailor
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from board_heldness import POOL, stage_unheld
from discovery_fetch import download
from tenant_hosts import row_for_host

SNAPSHOT_URL = "https://jobstream.api.jobtechdev.se/v2/snapshot"
SNAPSHOT = POOL / ".jobtech_jobstream" / "snapshot.json"
CHUNK = 1 << 20


class SnapshotCut(ValueError):
    """The array ends before its closing `]`: the server closed the stream early."""


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


def main() -> None:
    if not SNAPSHOT.exists():
        download(SNAPSHOT_URL, SNAPSHOT, resume=False)
    hosts: Counter[str] = Counter()
    total = 0
    try:
        for ad in ads(SNAPSHOT):
            total += 1
            url = ((ad.get("application_details") or {}).get("url")) or ""
            host = (urlsplit(url).hostname or "").lower()
            if host and row_for_host(host):
                hosts[host] += 1
    except SnapshotCut as cut:
        print(
            f"WARNING: {cut}; the {total} ads before the cut are read and the rest are not. "
            f"Delete {SNAPSHOT} and rerun on a faster link for the whole snapshot.",
            flush=True,
        )
    print(f"{total} ads, {len(hosts)} distinct tenant-shaped apply hosts", flush=True)
    by_ats: dict[str, list[tuple[str, str]]] = {}
    for host in sorted(hosts):
        row = row_for_host(host)
        by_ats.setdefault(row[0], []).append((row[1], row[2]))
    for ats, rows in sorted(by_ats.items()):
        print(ats, dict(stage_unheld(ats, rows, "jobtech_jobstream")), flush=True)


if __name__ == "__main__":
    main()
