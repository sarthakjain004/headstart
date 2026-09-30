#!/usr/bin/env python3
"""Common Crawl host-graph miner: Board hosts that were linked to but never fetched.

`cc_miner.py` and the Wayback feeders read URLs a crawler *fetched*. Common Crawl's host-level web
graph (`data.commoncrawl.org/projects/hyperlinkgraph/{release}/host/`) also lists every hostname
that only appears as a link target, and CC's own index page says 74.65% of the latest release's
245.8M host nodes are such dangling hosts -- exactly the population a CDX query cannot return.
Measured 2026-09-29 (one release): 5,328 Board hosts on the sub-domain ATSes that no ledger row
holds; of the 20 unheld Teamtailor hosts checked against the Wayback CDX none had a capture, which is
why the archive-driven miners never found them.

The 48 `vertices` shards are `id<TAB>reversed host` lines, sorted by reversed host (`com.bamboohr.
acme` is `acme.bamboohr.com`). This reads them all once, one shard at a time, and keeps the lines
under the reversed prefix of each family in `board_hosts.FAMILIES`:

    download    `curl -C -`, one stream, resumed only while the remote is unchanged, size printed
                as it grows (`discovery_fetch.download`)
    scan        streaming gunzip + one regex per chunk; a cut or corrupt shard is fetched again,
                at most SCAN_ATTEMPTS times with a pause, then the run fails loudly
    checkpoint  a shard is done when `matches-{shard}.txt` exists (saved whole, never half-written);
                the shard file is deleted afterwards

Rerunning skips done shards and resumes a half-downloaded one. When every shard is done, the hosts
are mapped to ledger rows by `board_hosts.row_for_host` and staged through `candidate_pool`.
1.77 GiB in all for the 2026-jul-aug-sep release; Common Crawl's terms of use allow access, no key.

Work dir: `data/wayback-ats/.common_crawl_host_graph/{release}/` (gitignored).

Run:   python -u scripts/discover/mine_common_crawl_host_graph.py [--release R] [--stage-only]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats {ats}
       and, for the families staged here that CLAUDE.md names (the run prints the reminder):
       python scripts/validate/dedupe_boards.py --ats recruitee --workers 4   (--apply only when
       no `unreachable`), python scripts/validate/clearcompany_shared_accounts.py
"""

from __future__ import annotations

import argparse
import gzip
import re
import time
import zlib
from pathlib import Path

from board_hosts import reversed_prefixes, row_for_host
from candidate_pool import POOL, stage_by_ats
from discovery_fetch import download, remove_download, save_whole

BASE = "https://data.commoncrawl.org/"
RELEASE = "cc-main-2026-jul-aug-sep"
CHUNK = 1 << 20
SCAN_ATTEMPTS = (
    3  # fetches of one shard that may come back cut or corrupt before the run fails
)
RESCAN_PAUSE = 30  # seconds between them, times the attempt number


def paths_url(release: str) -> str:
    return (
        f"{BASE}projects/hyperlinkgraph/{release}/host/{release}-host-vertices.paths.gz"
    )


def shard_urls(release: str, work: Path) -> list[str]:
    listing = work / "vertices.paths.gz"
    if not listing.exists():
        download(paths_url(release), listing)
    with gzip.open(listing, "rt") as handle:
        return [BASE + line for line in handle.read().split()]


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


def fetch_and_scan(
    url: str, gz_path: Path, pattern: re.Pattern[bytes]
) -> tuple[list[bytes], int]:
    """Download one shard and scan it. A cut or corrupt file is deleted and fetched again after a
    pause, at most SCAN_ATTEMPTS times; then `RuntimeError`, never an endless refetch loop."""
    for attempt in range(1, SCAN_ATTEMPTS + 1):
        download(url, gz_path)
        try:
            return scan_shard(gz_path, pattern)
        except (EOFError, zlib.error) as exc:
            print(
                f"  {gz_path.name}: {exc}; refetching from scratch ({attempt}/{SCAN_ATTEMPTS})",
                flush=True,
            )
            remove_download(gz_path)
            if attempt < SCAN_ATTEMPTS:
                time.sleep(RESCAN_PAUSE * attempt)
    raise RuntimeError(
        f"{gz_path.name} came back cut or corrupt {SCAN_ATTEMPTS} times: {url}"
    )


def prefix_pattern() -> re.Pattern[bytes]:
    """One regex that keeps a vertices line when its reversed host is under a wanted prefix."""
    prefixes = sorted(reversed_prefixes(), key=len, reverse=True)
    return re.compile(
        rb"^\d+\t((?:"
        + b"|".join(re.escape(p).encode() for p in prefixes)
        + rb")[^\t\n]*)$",
        re.MULTILINE,
    )


def mine(release: str, work: Path) -> None:
    pattern = prefix_pattern()
    urls = shard_urls(release, work)
    print(
        f"{release}: {len(urls)} vertices shards, {len(reversed_prefixes())} host families",
        flush=True,
    )
    for index, url in enumerate(urls):
        matches = work / f"matches-{index:05d}.txt"
        if matches.exists():
            continue
        gz_path = work / f"vertices-{index:05d}.txt.gz"
        started = time.time()
        found, rows = fetch_and_scan(url, gz_path, pattern)
        save_whole(matches, b"".join(host + b"\n" for host in found))
        size = gz_path.stat().st_size
        remove_download(gz_path)
        print(
            f"shard {index:2d}/{len(urls)}: {size / 1e6:6.1f} MB, {rows:>10,} rows, "
            f"{len(found):>6,} matches, {time.time() - started:5.0f}s",
            flush=True,
        )


def mined_hosts(work: Path) -> tuple[set[str], int]:
    """The distinct hosts every done shard kept, and how many shards that is."""
    hosts: set[str] = set()
    shards = sorted(work.glob("matches-*.txt"))
    for path in shards:
        hosts.update(
            ".".join(reversed(line.split("."))) for line in path.read_text().split()
        )
    return hosts, len(shards)


def stage(work: Path) -> None:
    hosts, shards = mined_hosts(work)
    print(
        f"{len(hosts)} distinct Board-shaped hosts across {shards} shards", flush=True
    )
    rows = (row for host in sorted(hosts) if (row := row_for_host(host)))
    stage_by_ats(rows, "common_crawl_host_graph")


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
