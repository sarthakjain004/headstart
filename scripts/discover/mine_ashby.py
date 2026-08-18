#!/usr/bin/env python3
"""Full Ashby miner — harvest every Ashby Board slug from Wayback and Common Crawl.

Ashby had no dedicated miner (Greenhouse/Lever/Workday/Zoho each do), so its candidate pool was
only ever whatever a single Common-Crawl snapshot happened to hold. Two gaps this closes:

  * **Wayback was never page-mined for this host.** `wayback_pages.py` queries `matchType=domain`,
    which *times out* on jobs.ashbyhq.com (verified 2026-07-27: 300s, no response) — Ashby is one
    dense host, so the domain-collapse forces a full server-side scan. `matchType=prefix` answers
    instantly and pages the same index, so this miner uses prefix and fetches every `&page=N`.
  * **Common Crawl was mined for one crawl.** `cc_miner_checkpoint.txt` held only
    `CC-MAIN-2026-25|jobs.ashbyhq.com|{0,1}`. Ashby slugs churn — a company that hired in 2023 and
    paused is absent from the newest snapshot — so this sweeps every crawl back to `CC_SINCE`.

The Common-Crawl half has two transports, because the CDX *API* host bans an egress IP at the TCP
level under sweep load (`connect()` to index.commoncrawl.org:443 -> ECONNREFUSED while DNS still
resolves, so TLS impersonation and challenge solvers are irrelevant). **data.commoncrawl.org, the
S3 mirror of the same index files, is a different host and stays reachable** — so when the API is
unreachable this falls back to binary-searching each crawl's sorted `cluster.idx` with range GETs
and pulling only the `cdx-NNNNN.gz` blocks for this host. Set `ASHBY_CC_S3=1` to skip straight
there once you know you are blocked. See docs/discovery/common-crawl-mining.md.

Both feeders are candidate-grade (historical, so full of dead Boards and URL noise); the slug is
only real once `api.ashbyhq.com/posting-api/job-board/{slug}` answers 200. Validate with
`scripts/validate/check_liveness.py ashby` after folding, and let *it* write the ledger.

Measured 2026-07-27: Wayback yielded 6,362 slugs; Common Crawl then added **0** across 15 crawls
swept back from CC-MAIN-2026-25 — its Ashby index is a strict subset of Wayback's. For a
path-based ATS on one dense host, page-mine Wayback first; CC is confirmation, not a primary feeder.

Output: data/wayback-ats/ashby.csv (ats,tenant,url), deduped and merged with whatever is there.
Resumable: completed (source, page) keys land in data/wayback-ats/.ashby_pages_done.

Run:  python -u scripts/discover/mine_ashby.py            # both feeders
      python -u scripts/discover/mine_ashby.py wayback    # just Wayback
      python -u scripts/discover/mine_ashby.py cc         # just Common Crawl
      ASHBY_CC_S3=1 python -u scripts/discover/mine_ashby.py cc   # CC via the S3 mirror
"""

import csv
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
OUT = WB / "ashby.csv"
STATE = WB / ".ashby_pages_done"
UA = "HeadStart-discovery/0.1 (ATS board discovery)"
WORKERS = int(os.environ.get("ASHBY_MINE_WORKERS") or 6)
CC_SINCE = (
    os.environ.get("ASHBY_CC_SINCE") or "CC-MAIN-2022"
)  # crawl ids sort lexically
CC_PACE = float(os.environ.get("ASHBY_CC_PACE") or 1.5)  # seconds between CDX calls

WAYBACK = "http://web.archive.org/cdx/search/cdx"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"

# Every URL shape an Ashby slug shows up in. Group 1 is the slug.
PATTERNS = [
    re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9][A-Za-z0-9._-]*)"),
    re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9][A-Za-z0-9._-]*)"),
]
# First path segments that are Ashby's own app, not a Board.
RESERVED = {
    "embed",
    "api",
    "static",
    "assets",
    "_next",
    "favicon.ico",
    "robots.txt",
    "sitemap.xml",
    "manifest.json",
    "login",
    "signup",
    "app",
    "posting-api",
    "job-board",
    "undefined",
    "null",
    "index.html",
}


def valid(slug: str) -> bool:
    s = slug.lower()
    if s in RESERVED or len(s) < 2 or len(s) > 63:
        return False
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", s):
        return False
    return not s.endswith((".js", ".css", ".png", ".svg", ".ico", ".json", ".xml"))


def slugs_from(text: str) -> set[str]:
    found = set()
    for line in text.splitlines():
        line = urllib.parse.unquote(line.strip())
        for pat in PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            s = m.group(1).lower().split("?")[0].split("#")[0]
            if valid(s):
                found.add(s)
            break
    return found


def get(url: str, tries: int = 6) -> str | None:
    """Fetch, distinguishing a real no-match from a block. None == blocked, retry a later run.

    Three outcomes, and conflating the last two is the trap:
      text  -> 200, real data
      ""    -> a definitive *empty* answer: 404, or CC's 400 "Page N invalid: First Page is 0,
               Last Page is M", which means the pages simply ran out
      None  -> blocked (403/429/5xx/timeout); wait it out, never hammer

    Common Crawl rate-limits hard, so a block must be *waited out* — an immediate retry loop is
    what gets the egress IP banned outright (cc_miner.py's docstring records the same lesson).
    Backoff is exponential and capped.
    """
    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:  # a real no-match, not a block
                return ""
            # CC answers a past-the-end page with 400, not an empty 200. Reading that as a block
            # aborts the whole sweep one page after the data ends (cost: 38 unmined crawls,
            # 2026-07-27) — see docs/discovery/common-crawl-mining.md.
            if r.status_code == 400 and "invalid" in (r.text or "").lower():
                return ""
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(min(4 * 2**attempt, 90))
    return None


# --- output ------------------------------------------------------------------------------------
_lock = threading.Lock()


def load_existing() -> set[str]:
    seen = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.add(r["tenant"].lower())
    return seen


def append(new: set[str]) -> None:
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for s in sorted(new):
            w.writerow(["ashby", s, f"https://jobs.ashbyhq.com/{s}"])


def done_keys() -> set[str]:
    return set(STATE.read_text().split()) if STATE.exists() else set()


def mark(key: str) -> None:
    with STATE.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


# --- feeders -----------------------------------------------------------------------------------
def wayback(seen: set[str], done: set[str]) -> None:
    """Page-mine the Wayback CDX index for both Ashby hosts (prefix match — domain times out)."""
    # Bare hosts only: a *path* prefix (`api.ashbyhq.com/posting-api/job-board`) makes the CDX
    # server scan rather than seek, and times out (verified 2026-07-27). The slug regexes below
    # filter the path, so the extra selectivity buys nothing anyway.
    for host in ("jobs.ashbyhq.com", "api.ashbyhq.com"):
        base = f"{WAYBACK}?url={urllib.parse.quote(host)}&matchType=prefix&fl=original&collapse=urlkey"
        n = get(
            f"{WAYBACK}?url={urllib.parse.quote(host)}&matchType=prefix&showNumPages=true"
        )
        if not n or not n.strip().isdigit():
            print(f"  [wayback] {host}: no page count ({n!r}) — skipped", flush=True)
            continue
        npages = int(n.strip())
        todo = [p for p in range(npages) if f"wb|{host}|{p}" not in done]
        print(f"  [wayback] {host}: {npages} pages, {len(todo)} to fetch", flush=True)

        def do(page: int, host=host, base=base):
            text = get(f"{base}&page={page}")
            if text is None:
                return f"  [wayback] {host} p{page}: BLOCKED — retry next run"
            found = slugs_from(text)
            with _lock:
                new = found - seen
                seen.update(new)
                if new:
                    append(new)
                mark(f"wb|{host}|{page}")
            return f"  [wayback] {host} p{page}: +{len(new)} new (total {len(seen)})"

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(do, p) for p in todo]):
                print(fut.result(), flush=True)


def _known_crawl_ids() -> list[str]:
    """Crawl ids for the S3 fallback, when collinfo.json's host is blocked.

    collinfo.json is authoritative and used whenever it is reachable; this is only the offline
    substitute. Crawl ids are immutable historical facts, and a wrong guess is harmless — an id
    that does not exist simply has no cluster.idx, so `_cc_blocks` returns nothing after one HEAD.
    Unioned with whatever ids the local cc_miner checkpoint has already seen.
    """
    static = [
        f"CC-MAIN-{y}-{w:02d}"
        for y, weeks in (
            (2022, (5, 21, 27, 33, 40, 49)),
            (2023, (6, 14, 23, 40, 50)),
            (2024, (10, 18, 22, 26, 30, 33, 38, 42, 46, 51)),
            (2025, (5, 8, 13, 18, 21, 26, 30, 33, 38, 43, 47, 51)),
            (2026, (4, 8, 12, 17, 21, 25)),
        )
        for w in weeks
    ]
    ckpt = ROOT / "data" / "discover" / "cc_miner_checkpoint.txt"
    if ckpt.exists():
        static += re.findall(r"CC-MAIN-\d{4}-\d{2}", ckpt.read_text(encoding="utf-8"))
    return sorted({c for c in static if c >= CC_SINCE}, reverse=True)


def _range(url: str, start: int, end: int) -> bytes | None:
    """One HTTP Range GET (inclusive bounds). None on failure."""
    for attempt in range(5):
        try:
            r = requests.get(
                url,
                timeout=90,
                headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"},
            )
            if r.status_code in (200, 206):
                return r.content
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(min(3 * 2**attempt, 45))
    return None


def _seek_key(url: str, size: int, pos: int) -> tuple[int, bytes] | None:
    """(offset, SURT key) of the first *complete* line at or after byte `pos`."""
    chunk = _range(url, pos, min(pos + 16384, size - 1))
    if not chunk:
        return None
    off = 0 if pos == 0 else chunk.find(b"\n") + 1
    if off == 0 and pos != 0:
        return None
    line = chunk[off:].split(b"\n")[0]
    return (pos + off, line.split(b" ", 1)[0]) if line else None


def _cc_blocks(crawl_id: str, prefix: bytes) -> list[tuple[str, int, int]] | None:
    """Locate the cdx blocks covering `prefix` by binary-searching the crawl's cluster.idx.

    cluster.idx is a *sorted*, sparse map `SURT ts \\t cdx-NNNNN.gz \\t offset \\t length \\t n`
    — one line per ~3000 index lines. It is ~100 MB, but sorted order means ~25 range GETs of
    16 KB find the neighbourhood, and one 512 KB window then covers every line for the host.

    Returns `None` when the *lookup itself* failed (no cluster.idx, or a range GET died), so the
    caller can retry it later; `[]` only when the index genuinely holds nothing for the prefix.
    Collapsing those two into `[]` marks a transient failure as permanently done — that is how
    CC-MAIN-2024-51 came back "no blocks" despite having a 128 MB cluster.idx.
    """
    base = f"https://data.commoncrawl.org/cc-index/collections/{crawl_id}/indexes/"
    idx = base + "cluster.idx"
    try:
        head = requests.head(idx, timeout=60, headers={"User-Agent": UA})
        size = int(head.headers.get("content-length") or 0)
    except Exception:  # noqa: BLE001
        size = 0
    if not size:
        return None
    lo, hi, best = 0, size - 1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        got = _seek_key(idx, size, mid)
        if got is None:
            # A failed probe is NOT evidence that this offset sorts past the prefix. Folding it
            # into the `hi = mid - 1` branch (as this first did) silently drags `best` below the
            # true answer, and the window then never reaches the host — which is how CC-MAIN-2024-51
            # reported "no captures" even though `com,ashbyhq,jobs)/photoroom/…` is demonstrably
            # in its cluster.idx. Abort instead, and let the caller retry the whole crawl.
            return None
        start, key = got
        if key < prefix:
            lo, best = mid + 1, start
        else:
            hi = mid - 1
    window = _range(idx, max(0, best - 1024), min(best + 524288, size - 1))
    if not window:
        return None
    # cluster.idx is SPARSE — one line per ~3000 index lines — so the block holding our prefix
    # normally *starts at a key below it*. Taking only lines whose key >= prefix therefore misses
    # the block that actually contains the host, usually returning nothing at all. Carry the last
    # sub-prefix line and emit it as soon as the range is reached or passed.
    blocks: list[tuple[str, int, int]] = []
    prev: tuple[str, int, int] | None = None
    for line in window.split(b"\n"):
        parts = line.split(b"\t")
        if len(parts) < 4:
            continue
        key = parts[0].split(b" ", 1)[0]
        try:
            entry = (parts[1].decode(), int(parts[2]), int(parts[3]))
        except ValueError:
            continue
        if key < prefix:
            prev = entry
            continue
        if prev is not None:  # the block straddling the start of our range
            blocks.append(prev)
            prev = None
        if not key.startswith(prefix):
            break  # sorted, so the first key past the prefix ends the range
        blocks.append(entry)
    if prev is not None:  # prefix sorts past every line in the window
        blocks.append(prev)
    return blocks


def commoncrawl_s3(seen: set[str], done: set[str], crawls: list[dict]) -> None:
    """Mine the CC index off data.commoncrawl.org instead of index.commoncrawl.org.

    The CDX *API* host bans an egress IP at the TCP level under sweep load (see
    docs/discovery/common-crawl-mining.md; verified again 2026-07-27 — `connect()` to
    index.commoncrawl.org:443 returns ECONNREFUSED while DNS still resolves, so no client
    library, TLS impersonation or solver can help). The **S3 mirror serving the same index
    files is a different host and stays reachable**, so this path keeps working when the API
    host is blocked. It is also cheaper: range GETs pull only the gzip blocks for one host
    instead of paging the whole result set.
    """
    # One prefix for the whole domain: SURT sorts `com,ashbyhq,api)/` and `com,ashbyhq,jobs)/`
    # adjacently, so a single search+fetch covers both hosts instead of doing the work twice.
    for crawl in crawls:
        cid = crawl["id"]
        key0 = f"s3|{cid}|ashbyhq.com"
        if key0 in done:
            continue
        blocks = _cc_blocks(cid, b"com,ashbyhq,")
        if blocks is None:  # lookup failed — leave unmarked so a later run retries it
            print(f"  [cc-s3] {cid}: index lookup failed — retry next run", flush=True)
            continue
        if not blocks:
            # Deliberately NOT marked done. An empty result is indistinguishable from a
            # mis-seeked search, and this miner has been wrong that way before — so an empty
            # crawl stays retryable rather than being frozen as "checked, nothing there".
            print(
                f"  [cc-s3] {cid}: no blocks found — left unmarked for a later run",
                flush=True,
            )
            continue
        base = f"https://data.commoncrawl.org/cc-index/collections/{cid}/indexes/"
        found: set[str] = set()
        ok = True
        for fname, off, length in blocks:
            time.sleep(CC_PACE)
            raw = _range(base + fname, off, off + length - 1)
            if raw is None:
                ok = False
                break
            try:
                text = gzip.decompress(raw).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001, S112
                continue
            found |= slugs_from(text)
        if not ok:
            print(f"  [cc-s3] {cid}: BLOCKED — retry next run", flush=True)
            continue
        with _lock:
            new = found - seen
            seen.update(new)
            if new:
                append(new)
            mark(key0)
        print(
            f"  [cc-s3] {cid}: {len(blocks)} blocks, +{len(new)} new (total {len(seen)})",
            flush=True,
        )


def commoncrawl(seen: set[str], done: set[str]) -> None:
    """Sweep every Common-Crawl monthly index (not just the newest) for both Ashby hosts."""
    # Once you know the API host has banned the egress IP, probing it again just burns the full
    # backoff (~3.5 min) before every run falls back anyway. ASHBY_CC_S3=1 skips straight to the
    # mirror.
    if os.environ.get("ASHBY_CC_S3") == "1":
        print("  [cc] ASHBY_CC_S3=1 — using the S3 mirror directly", flush=True)
        commoncrawl_s3(seen, done, [{"id": c} for c in _known_crawl_ids()])
        return
    info = get(CC_COLLINFO)
    if not info:
        # The API host is blocked/unreachable; the S3 mirror is a different host, so fall back
        # to it rather than losing the whole feeder. Crawl ids are derivable without collinfo.
        print(
            "  [cc] index.commoncrawl.org unreachable — falling back to the S3 mirror",
            flush=True,
        )
        crawls = [{"id": c} for c in _known_crawl_ids()]
        commoncrawl_s3(seen, done, crawls)
        return
    crawls = [c for c in json.loads(info) if c["id"] >= CC_SINCE]
    print(f"  [cc] {len(crawls)} crawls >= {CC_SINCE}", flush=True)
    for crawl in crawls:
        cdx, cid = crawl["cdx-api"], crawl["id"]
        for host in ("jobs.ashbyhq.com", "api.ashbyhq.com"):
            # Ask how many pages exist rather than walking until it errors: a past-the-end page
            # answers 400, and treating that as a block used to abort the entire sweep.
            time.sleep(CC_PACE)
            npages_txt = get(f"{cdx}?url={host}&matchType=domain&showNumPages=true")
            if npages_txt is None:
                print(
                    f"  [cc] {cid} {host}: BLOCKED on page count — skipped, resume next run",
                    flush=True,
                )
                continue
            try:
                npages = int(json.loads(npages_txt or "{}").get("pages", 0))
            except Exception:  # noqa: BLE001
                npages = int(npages_txt.strip()) if npages_txt.strip().isdigit() else 0
            if not npages:
                print(f"  [cc] {cid} {host}: 0 pages", flush=True)
                continue
            for page in range(npages):
                key = f"cc|{cid}|{host}|{page}"
                if key in done:
                    continue
                time.sleep(CC_PACE)
                text = get(
                    f"{cdx}?url={host}&matchType=domain&output=json&fl=url&limit=20000&page={page}"
                )
                if text is None:
                    # One blocked page must not kill the other 38 crawls; leave it unmarked so a
                    # later run retries exactly it.
                    print(
                        f"  [cc] {cid} {host} p{page}: BLOCKED — retry next run",
                        flush=True,
                    )
                    continue
                if not text.strip():
                    mark(key)
                    continue
                found = slugs_from(text)
                with _lock:
                    new = found - seen
                    seen.update(new)
                    if new:
                        append(new)
                    mark(key)
                print(
                    f"  [cc] {cid} {host} p{page}: +{len(new)} new (total {len(seen)})",
                    flush=True,
                )


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    WB.mkdir(parents=True, exist_ok=True)
    if not OUT.exists():
        OUT.write_text("ats,tenant,url\n", encoding="utf-8")
    seen, done = load_existing(), done_keys()
    print(
        f"start: {len(seen)} slugs in {OUT.name}, {len(done)} pages already done",
        flush=True,
    )
    if which in ("all", "wayback"):
        wayback(seen, done)
    if which in ("all", "cc"):
        commoncrawl(seen, done)
    print(f"DONE: {len(seen)} unique ashby slugs in {OUT}", flush=True)


if __name__ == "__main__":
    main()
