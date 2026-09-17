#!/usr/bin/env python3
"""Reproduce the measurements behind `LOG.md` — is Apple's detail pass stream- or connection-bound?

Every number in `LOG.md` that is not read off a pipeline log comes from here. Run it from the repo
root; it makes real requests to `jobs.apple.com` and takes a few minutes.

    python experiment/apple-detail-transport/measure_transport.py --all
    python experiment/apple-detail-transport/measure_transport.py --settings   # cheap, one socket

The four measurements, each answering one question:

``--settings``   Is the server capping streams? Reads its SETTINGS frame directly.
``--streams``    Does widening the multiplexing width help? (async, one AsyncSession.)
``--threads``    Does adding connections help? (sync fan_out, one connection per worker.)
``--ab``         The two, interleaved on fresh ids, to control for network drift.

Output is printed per row as it lands, never buffered to the end (CLAUDE.md, Repo Conventions).
"""

from __future__ import annotations

import argparse
import random
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart.scrapers.apple import AppleScraper

HOST = "jobs.apple.com"
#: Enough distinct ids that no round reuses one — a repeated id would measure a warm cache rather
#: than the origin. A short pool silently produced a 0.00 req/s round once.
POOL_PAGES = 60


def _pool(s: AppleScraper, pages: int = POOL_PAGES) -> list[str]:
    chosen = sorted(random.sample(range(1, 310), pages))

    def page(p: int) -> list[dict]:
        try:
            return s._search_page(p).get("searchResults") or []
        except Exception:  # noqa: BLE001 - a harness round must survive one bad page
            return []

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(page, p) for p in chosen]):
            rows += f.result()
    ids = list({r["id"] for r in rows if r.get("id")})
    random.shuffle(ids)
    print(f"pool: {len(ids)} distinct ids", flush=True)
    return ids


def settings() -> None:
    """The server's own view of how many streams it will carry."""
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    with (
        socket.create_connection((HOST, 443), timeout=20) as raw,
        ctx.wrap_socket(raw, server_hostname=HOST) as tls,
    ):
        print(f"ALPN: {tls.selected_alpn_protocol()}", flush=True)
        tls.sendall(
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + bytes([0, 0, 0, 4, 0, 0, 0, 0, 0])
        )
        data = tls.recv(4096)
    names = {
        1: "HEADER_TABLE_SIZE",
        2: "ENABLE_PUSH",
        3: "MAX_CONCURRENT_STREAMS",
        4: "INITIAL_WINDOW_SIZE",
        5: "MAX_FRAME_SIZE",
        6: "MAX_HEADER_LIST_SIZE",
    }
    i = 0
    while i + 9 <= len(data):
        ln = int.from_bytes(data[i : i + 3], "big")
        typ, payload = data[i + 3], data[i + 9 : i + 9 + ln]
        if typ == 4 and payload:
            for j in range(0, len(payload), 6):
                k = int.from_bytes(payload[j : j + 2], "big")
                v = int.from_bytes(payload[j + 2 : j + 6], "big")
                print(f"   {names.get(k, k)} = {v}", flush=True)
        i += 9 + ln


def _threads(s: AppleScraper, ids: list[str], width: int) -> list:
    def one(i: str):
        try:
            return s._detail(i)
        except Exception:  # noqa: BLE001 - a miss is data here, not a reason to abort
            return None

    with ThreadPoolExecutor(max_workers=width) as ex:
        return list(ex.map(one, ids))


def ladder(s: AppleScraper, ids: list[str], mode: str, n: int = 160) -> None:
    cur = 0
    print(
        f"\n{mode}:\n{'width':>6} {'n':>5} {'wall_s':>8} {'req/s':>7} {'missing':>8}",
        flush=True,
    )
    for width in (16, 32, 64):
        sample, cur = ids[cur : cur + n], cur + n
        if len(sample) < n:
            print("   (pool exhausted — raise POOL_PAGES)", flush=True)
            return
        t0 = time.time()
        out = (
            s.fan_out_async(sample, s._detail_async, concurrency=width)
            if mode == "streams"
            else _threads(s, sample, width)
        )
        wall = time.time() - t0
        print(
            f"{width:>6} {len(sample):>5} {wall:>8.1f} {len(sample) / wall:>7.2f} "
            f"{sum(1 for o in out if not o):>8}",
            flush=True,
        )
        time.sleep(5)


def ab(s: AppleScraper, ids: list[str], rounds: int = 3, n: int = 150) -> None:
    """Interleaved, fresh ids per round: the two transports see the same network conditions."""
    cur = 0
    print(
        f"\ninterleaved A/B:\n{'round':>6} {'mode':>18} {'req/s':>7} {'missing':>8}",
        flush=True,
    )
    for rnd in range(1, rounds + 1):
        for label, fn in (
            (
                "async 32 streams",
                lambda smp: s.fan_out_async(smp, s._detail_async, concurrency=32),
            ),
            ("threads 32 conns", lambda smp: _threads(s, smp, 32)),
        ):
            sample, cur = ids[cur : cur + n], cur + n
            if len(sample) < n:
                print("   (pool exhausted — raise POOL_PAGES)", flush=True)
                return
            t0 = time.time()
            out = fn(sample)
            wall = time.time() - t0
            print(
                f"{rnd:>6} {label:>18} {len(sample) / wall:>7.2f} "
                f"{sum(1 for o in out if not o):>8}",
                flush=True,
            )
            time.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for flag in ("settings", "streams", "threads", "ab", "all"):
        ap.add_argument(f"--{flag}", action="store_true")
    ap.add_argument("--seed", type=int, default=101)
    args = ap.parse_args()
    if not any((args.settings, args.streams, args.threads, args.ab, args.all)):
        ap.error("pick at least one measurement, or --all")
    random.seed(args.seed)

    if args.settings or args.all:
        settings()
    if not (args.streams or args.threads or args.ab or args.all):
        return
    s = AppleScraper(HOST)
    ids = _pool(s)
    if args.streams or args.all:
        ladder(s, ids, "streams")
    if args.threads or args.all:
        ladder(s, ids[480:], "threads")
    if args.ab or args.all:
        ab(s, ids[960:])


if __name__ == "__main__":
    main()
