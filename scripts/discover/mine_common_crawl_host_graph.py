#!/usr/bin/env python3
"""Common Crawl host-graph miner: tenant hosts that were linked to but never fetched.

`cc_miner.py` and the Wayback feeders read URLs a crawler *fetched*. Common Crawl's host-level web
graph (`data.commoncrawl.org/projects/hyperlinkgraph/{release}/host/`) also lists every hostname
that only appears as a link target, and CC's own index page says 74.65% of the latest release's
245.8M host nodes are such dangling hosts -- exactly the population a CDX query cannot return.
Measured 2026-09-29 (one release): 5,328 tenant hosts on the sub-domain ATSes that no ledger row
holds; of the 20 unheld Teamtailor hosts checked against the Wayback CDX none had a capture, which is
why the archive-driven miners never found them.

The 48 `vertices` shards are `id<TAB>reversed host` lines, sorted by reversed host (`com.bamboohr.
acme` is `acme.bamboohr.com`). This reads them all once, one shard at a time, and keeps the lines
under the reversed prefix of each family in `tenant_hosts.FAMILIES`:

    download    `curl -C -`, one stream, resumable, aborts a stalled transfer and resumes it
    scan        streaming gunzip + one regex per chunk; the shard file is deleted afterwards
    checkpoint  a shard is done when `matches-{shard}.txt` exists (saved whole, never half-written)

Rerunning skips done shards and resumes a half-downloaded one. When every shard is done, the hosts
are mapped to ledger rows by `tenant_hosts.row_for_host` and staged through `board_heldness`.
1.77 GiB in all for the 2026-jul-aug-sep release; Common Crawl's terms of use allow access, no key.

Work dir: `data/wayback-ats/.common_crawl_host_graph/{release}/` (gitignored).

Run:   python -u scripts/discover/mine_common_crawl_host_graph.py [--release R] [--stage-only]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
"""

from __future__ import annotations

import argparse
import gzip
import re
import time
import zlib
from pathlib import Path

from board_heldness import POOL, stage_unheld
from discovery_fetch import download, save_whole
from tenant_hosts import reversed_prefixes, row_for_host

BASE = "https://data.commoncrawl.org/"
RELEASE = "cc-main-2026-jul-aug-sep"
CHUNK = 1 << 20


def paths_url(release: str) -> str:
    return (
        f"{BASE}projects/hyperlinkgraph/{release}/host/{release}-host-vertices.paths.gz"
    )


def shard_urls(release: str, work: Path) -> list[str]:
    listing = work / "vertices.paths.gz"
    if not listing.exists():
        download(paths_url(release), listing)
    return [BASE + line for line in gzip.open(listing, "rt").read().split()]


def scan_shard(gz_path: Path, pattern: re.Pattern[bytes]) -> tuple[list[bytes], int]:
    """The reversed hosts in one shard under a wanted prefix, and the rows scanned.

    Raises `EOFError` when the gzip stream is cut short, so a half-downloaded file is never
    mistaken for a finished shard."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    found: list[bytes] = []
    rows = 0
    tail = b""
    with gz_path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            text = tail + inflater.decompress(chunk)
            cut = text.rfind(b"\n") + 1
            body, tail = text[:cut], text[cut:]
            rows += body.count(b"\n")
            found.extend(pattern.findall(body))
    if not inflater.eof:
        raise EOFError(f"{gz_path.name}: gzip stream ends early")
    tail_match = pattern.findall(tail + b"\n") if tail else []
    return found + tail_match, rows + (1 if tail else 0)


def mine(release: str, work: Path) -> None:
    prefixes = sorted(reversed_prefixes(), key=len, reverse=True)
    pattern = re.compile(
        rb"^\d+\t((?:"
        + b"|".join(re.escape(p).encode() for p in prefixes)
        + rb")[^\t\n]*)$",
        re.MULTILINE,
    )
    urls = shard_urls(release, work)
    print(
        f"{release}: {len(urls)} vertices shards, {len(prefixes)} host families",
        flush=True,
    )
    for index, url in enumerate(urls):
        matches = work / f"matches-{index:05d}.txt"
        if matches.exists():
            continue
        gz_path = work / f"vertices-{index:05d}.txt.gz"
        started = time.time()
        while True:
            download(url, gz_path)
            try:
                found, rows = scan_shard(gz_path, pattern)
                break
            except (EOFError, zlib.error) as exc:
                print(f"  shard {index}: {exc}; refetching from scratch", flush=True)
                gz_path.unlink(missing_ok=True)
        save_whole(matches, b"".join(host + b"\n" for host in found))
        size = gz_path.stat().st_size
        gz_path.unlink()
        print(
            f"shard {index:2d}/{len(urls)}: {size / 1e6:6.1f} MB, {rows:>10,} rows, "
            f"{len(found):>6,} matches, {time.time() - started:5.0f}s",
            flush=True,
        )


def stage(work: Path) -> None:
    hosts = set()
    for path in sorted(work.glob("matches-*.txt")):
        hosts.update(
            ".".join(reversed(line.split("."))) for line in path.read_text().split()
        )
    print(
        f"{len(hosts)} distinct tenant-shaped hosts across {len(list(work.glob('matches-*.txt')))} shards",
        flush=True,
    )
    by_ats: dict[str, list[tuple[str, str]]] = {}
    for host in sorted(hosts):
        row = row_for_host(host)
        if row:
            by_ats.setdefault(row[0], []).append((row[1], row[2]))
    for ats, rows in sorted(by_ats.items()):
        print(ats, dict(stage_unheld(ats, rows, "common_crawl_host_graph")), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release", default=RELEASE)
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="skip the download, stage what is mined",
    )
    args = parser.parse_args()
    work = POOL / ".common_crawl_host_graph" / args.release
    work.mkdir(parents=True, exist_ok=True)
    if not args.stage_only:
        mine(args.release, work)
    stage(work)


if __name__ == "__main__":
    main()
