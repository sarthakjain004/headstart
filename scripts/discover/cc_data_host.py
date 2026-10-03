"""Read Common Crawl's URL index off ``data.commoncrawl.org`` instead of the CDX API.

``index.commoncrawl.org`` (the CDX API) refuses an egress IP at the TCP level under sweep load, and
some days refuses everyone (2026-09-23: empty replies, curl 52, all day). ``data.commoncrawl.org``
is a different host serving the same index files, so a miner that can read them directly keeps
working when the API is down. See docs/discovery/common-crawl-mining.md for the file layout and
the sparse-block trap this module guards against.

The unit is a **SURT key range** ``[lo, hi)``. :func:`domain_range` turns a CDX ``matchType=domain``
target into one, so any miner keyed on host targets (``cc_miner.ATS_PATTERNS``) can use it, and
:func:`capture_urls` returns every captured URL in that range for one crawl.

When the data host answers 403, 429 or 503 (S3's SlowDown), requests move to the spare egress
(``headstart.network.spare_egress``, the WARP SOCKS proxy) and rotate it on repeated refusals, with paced retries and Retry-After (the shared client's 30 s cap).
"""

from __future__ import annotations

import gzip
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from headstart.network import http

DATA = "https://data.commoncrawl.org"
UA = "HeadStart-discovery/0.1 (ATS board discovery)"
#: Parallel block fetches per target. The data host is S3 behind CloudFront; stay polite.
WORKERS = 4
#: Bytes of cluster.idx read per window once the binary search has found the range's start.
WINDOW = 524288

_PACER = http.RoutePacer(0.25)


def capture_known_hosts(crawl_id, hosts, *, on_host=None):
    """Read many exact hosts in one pass over cluster.idx and each needed CDX block.

    The company-domain ATS audit has thousands of hosts. Binary-searching the
    remote sparse index separately for each would cost ~25 requests per host.
    Failed blocks leave every host they cover incomplete (None), never empty.
    """
    hosts = set(hosts)
    result = {host: [] for host in hosts}
    base = f"{DATA}/cc-index/collections/{crawl_id}/indexes/"
    response = _get(base + "cluster.idx")
    if response is None:
        if on_host:
            for host in hosts:
                on_host(host, None)
        return dict.fromkeys(hosts)
    lines = [line for line in response.content.splitlines() if line]
    keys = [_key(line) for line in lines]
    blocks = {}
    for host in hosts:
        lo = surt_host(host).encode() + b")"
        hi = surt_host(host).encode() + b"*"
        start = max(0, bisect_right(keys, lo) - 1)
        stop = bisect_left(keys, hi)
        for line in lines[start : max(start + 1, stop)]:
            parts = line.split(b"\t")
            block = (parts[1].decode(), int(parts[2]), int(parts[3]))
            blocks.setdefault(block, set()).add(host)
    print(
        f"[cc-data] {len(hosts)} known hosts in {len(blocks)} shared blocks", flush=True
    )

    def read(block, covered):
        filename, offset, length = block
        response = _get(base + filename, start=offset, end=offset + length - 1)
        if response is None:
            return None
        try:
            rows = gzip.decompress(response.content).splitlines()
        except (OSError, EOFError):
            return None
        surts = {}
        for host in covered:
            surts.setdefault(surt_host(host).encode(), []).append(host)
        hits = []
        for row in rows:
            key, _, rest = row.partition(b" ")
            matched_hosts = surts.get(key.partition(b")")[0])
            if matched_hosts is None:
                continue
            try:
                url = json.loads(rest.split(b" ", 1)[1])["url"]
                hits.extend((host, url) for host in matched_hosts)
            except (IndexError, ValueError, KeyError):
                return None
        return hits

    with ThreadPoolExecutor(WORKERS) as pool:
        remaining = Counter(host for covered in blocks.values() for host in covered)
        futures = {
            pool.submit(read, block, covered): covered
            for block, covered in blocks.items()
        }
        for number, future in enumerate(as_completed(futures), 1):
            hits = future.result()
            if hits is None:
                for host in futures[future]:
                    result[host] = None
            else:
                for host, url in hits:
                    if result[host] is not None:
                        result[host].append(url)
            for host in futures[future]:
                remaining[host] -= 1
                if remaining[host] == 0 and on_host:
                    on_host(host, result[host])
            if number % 100 == 0:
                print(
                    f"[cc-data] {number}/{len(blocks)} known-host blocks read",
                    flush=True,
                )
    return result


def surt_host(host: str) -> str:
    """``jobs.lever.co`` -> ``co,lever,jobs``: the host part of a SURT key."""
    # Common Crawl canonicalizes www.example.com and example.com to the same key.
    return ",".join(reversed(host.lower().strip(".").removeprefix("www.").split(".")))


def domain_range(target: str) -> tuple[bytes, bytes]:
    """The SURT key range CDX's ``matchType=domain`` covers for `target`.

    A key for the host itself is ``{surt})/...`` and one for a subdomain is ``{surt},...``, and
    ``)`` (0x29) and ``,`` (0x2c) are the only two of 0x29..0x2c a host can follow with, so the
    half-open range up to ``{surt}-`` (0x2d) holds both and nothing else. ``com,hrmdirect-x,``
    is a different domain and sorts after it.
    """
    s = surt_host(target).encode()
    return s + b")", s + b"-"


def crawl_ids(since: str = "") -> list[str]:
    """Every crawl id the data host lists, newest first, keeping ids ``>= since``."""
    r = _get(f"{DATA}/crawl-data/index.html")
    if r is None:
        return []
    ids = set(re.findall(r"CC-MAIN-\d{4}-\d{2}", r.text))
    return sorted((c for c in ids if c >= since), reverse=True)


def _get(url: str, *, start: int | None = None, end: int | None = None, tries: int = 6):
    """GET a whole file or byte range, using paced spare-egress retries on 403/429/503."""
    headers = {"User-Agent": UA}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    try:
        response = http.fetch(
            "GET",
            url,
            attempts=tries,
            timeout=90,
            headers=headers,
            egress_group="cc-data",
            egress_on=frozenset({403, 429, 503}),
            request_pacer=_PACER,
        )
    except http.RequestsError:
        return None
    # A Range GET must come back 206, never the whole ~100 MB file at 200.
    return response if response.status_code == (200 if start is None else 206) else None


def _seek_key(url: str, size: int, pos: int) -> tuple[int, bytes] | None:
    """(offset, SURT key) of the first *complete* line at or after byte `pos`."""
    r = _get(url, start=pos, end=min(pos + 16384, size - 1))
    if r is None:
        return None
    chunk = r.content
    off = 0 if pos == 0 else chunk.find(b"\n") + 1
    if off == 0 and pos != 0:
        return None
    line = chunk[off:].split(b"\n")[0]
    return (pos + off, _key(line)) if line else None


def _key(line: bytes) -> bytes:
    return line.split(b" ", 1)[0]


def select_blocks(
    lines: list[bytes], lo: bytes, hi: bytes
) -> list[tuple[str, int, int]]:
    """The cdx blocks among complete, sorted cluster.idx `lines` that hold keys in ``[lo, hi)``.

    cluster.idx is SPARSE — one line per ~3000 index lines, each naming the block that *starts*
    at its key — so the block holding `lo` normally starts at a key below it. Taking only lines
    whose key is ``>= lo`` misses it and usually returns nothing at all. The last line below `lo`
    is therefore carried and emitted once the range is reached.
    """
    blocks: list[tuple[str, int, int]] = []
    prev: tuple[str, int, int] | None = None
    for line in lines:
        parts = line.split(b"\t")
        if len(parts) < 4:
            continue
        key = _key(parts[0])
        try:
            entry = (parts[1].decode(), int(parts[2]), int(parts[3]))
        except ValueError:
            continue
        if key < lo:
            prev = entry
            continue
        if prev is not None:  # the block straddling the start of the range
            blocks.append(prev)
            prev = None
        if key >= hi:
            break
        blocks.append(entry)
    else:
        # The range starts past every line, so it sits in the last block.
        if prev is not None:
            blocks.append(prev)
    return blocks


def cc_blocks(crawl_id: str, lo: bytes, hi: bytes) -> list[tuple[str, int, int]] | None:
    """The ``(cdx file, offset, length)`` blocks holding keys ``[lo, hi)`` in one crawl.

    Binary-searches the crawl's sorted cluster.idx (~100 MB) with ~25 range GETs of 16 KB, then
    reads forward in :data:`WINDOW` chunks until the range ends.

    Returns None when the *lookup* failed (no cluster.idx, or a range GET died), so the caller can
    retry it later; ``[]`` only when the index genuinely holds nothing for the range. Collapsing
    the two marks a transient failure as permanently done — how CC-MAIN-2024-51 once came back
    "no blocks" despite a 128 MB cluster.idx.
    """
    idx = f"{DATA}/cc-index/collections/{crawl_id}/indexes/cluster.idx"
    r = _get(idx, start=0, end=0)
    try:
        size = int(r.headers["content-range"].rsplit("/", 1)[1]) if r else 0
    except (KeyError, ValueError):
        size = 0
    if not size:
        return None
    lo_pos, hi_pos, best = 0, size - 1, 0
    while lo_pos <= hi_pos:
        mid = (lo_pos + hi_pos) // 2
        got = _seek_key(idx, size, mid)
        if got is None:
            # A failed probe is NOT evidence that this offset sorts past the range. Folding it into
            # the `hi_pos = mid - 1` branch drags `best` below the true answer and the window never
            # reaches the host. Abort and let the caller retry the whole crawl.
            return None
        start, key = got
        if key < lo:
            lo_pos, best = mid + 1, start
        else:
            hi_pos = mid - 1
    # `best` is the start of a complete line, so the read never begins mid-line. (mine_ashby's
    # original read began 1 KB early, and a torn first line whose key sorted past the range ended
    # the scan before it started.)
    lines: list[bytes] = []
    pos, carry = best, b""
    while pos < size:
        r = _get(idx, start=pos, end=min(pos + WINDOW, size) - 1)
        if r is None:
            return None
        pos += len(r.content)
        *got, carry = (carry + r.content).split(b"\n")
        lines += got
        if lines and _key(lines[-1]) >= hi:
            break
    else:
        lines.append(carry)
    return select_blocks(lines, lo, hi)


def capture_urls(crawl_id: str, target: str) -> list[str] | None:
    """Every captured URL for `target` (CDX ``matchType=domain``) in one crawl. None on failure."""
    lo, hi = domain_range(target)
    blocks = cc_blocks(crawl_id, lo, hi)
    if blocks is None:
        return None
    base = f"{DATA}/cc-index/collections/{crawl_id}/indexes/"

    def fetch(block: tuple[str, int, int]) -> list[str] | None:
        fname, off, length = block
        r = _get(base + fname, start=off, end=off + length - 1)
        if r is None:
            return None
        try:
            text = gzip.decompress(r.content)
        except (
            OSError,
            EOFError,
        ):  # a truncated block: retry the crawl, don't crash the run
            return None
        urls = []
        for line in text.split(b"\n"):
            key, _, rest = line.partition(b" ")
            if not (lo <= key < hi):
                continue
            try:
                urls.append(json.loads(rest.split(b" ", 1)[1])["url"])
            except (IndexError, ValueError, KeyError):
                continue
        return urls

    urls: list[str] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fut in as_completed([ex.submit(fetch, b) for b in blocks]):
            got = fut.result()
            if got is None:
                ex.shutdown(cancel_futures=True)
                return None
            urls += got
    return urls
