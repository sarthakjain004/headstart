#!/usr/bin/env python3
"""Keka Slug miner over public Telegram job channels' web previews.

Indian job-alert channels on Telegram (off-campus drives, fresher alerts, referral groups) repost
openings with their apply link, and a Keka Board's apply link carries its Slug in the host:
``https://{slug}.keka.com/careers/jobdetails/{id}``. A public channel's history is readable
without an account at ``https://t.me/s/{channel}``, twenty-odd posts a page, walked backwards with
``?before={post_id}``. So one channel's whole archive is a few hundred GETs, and it names Boards
by the Slug the Company actually posts — the invented brand labels (``xyram``, ``qloron``) that no
wordlist or DNS sieve can guess.

Web search found the channels (``site:t.me "keka.com/careers/jobdetails"``); pass them as
arguments. Output is one ``channel,slug`` row per first sighting, appended as found, so a killed
run loses nothing already printed. Also records ``*.kekahire.com`` labels, which 302 to the same
``{slug}.keka.com`` Board (docs/discovery/2026-09-08_wayback-host-coverage-audit.md §7.3).

Politeness (ADR-0026): one request per second per channel, channels walked concurrently but
capped, and a 429 pauses that channel for a minute.

Run:  python scripts/discover/mine_keka_telegram.py OUT_CSV CHANNEL [CHANNEL ...]
"""

import csv
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart.network import http  # needs src on sys.path first

# Keka's own hosts under keka.com, never a tenant's Board.
KEKA_INFRA = {
    "www",
    "help",
    "cdn",
    "app",
    "academy",
    "status",
    "developers",
    "api",
    "login",
}
SLUG_RE = re.compile(
    r"\b([a-z0-9][a-z0-9-]{0,61})\.(?:keka|kekahire)\.com", re.IGNORECASE
)
POST_RE = re.compile(r'data-post="[^/"]+/(\d+)"')
CHANNEL_WORKERS = 4
PAGE_DELAY = 1.0

_lock = threading.Lock()


def walk_channel(channel: str, seen: set[str], writer, fh) -> tuple[str, int, int]:
    """Walk one channel backwards to its first post; returns (channel, pages, new slugs)."""
    before: int | None = None
    pages = found = 0
    while True:
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        try:
            r = http.fetch("GET", url, timeout=30, attempts=2)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [{channel}] error {type(exc).__name__} at before={before}",
                flush=True,
            )
            break
        if r.status_code == 429:
            print(f"  [{channel}] 429 — pausing 60s", flush=True)
            time.sleep(60)
            continue
        if r.status_code != 200:
            print(f"  [{channel}] HTTP {r.status_code}, stopping", flush=True)
            break
        pages += 1
        body = r.text
        for label in SLUG_RE.findall(body):
            label = label.lower()
            if label in KEKA_INFRA:
                continue
            with _lock:
                if label in seen:
                    continue
                seen.add(label)
                writer.writerow([channel, label])
                fh.flush()
            found += 1
            print(f"  [{channel}] {label}", flush=True)
        posts = [int(p) for p in POST_RE.findall(body)]
        if (
            not posts
            or min(posts) <= 1
            or (before is not None and min(posts) >= before)
        ):
            break
        before = min(posts)
        if pages % 50 == 0:
            print(
                f"  [{channel}] {pages} pages, before={before}, {found} new", flush=True
            )
        time.sleep(PAGE_DELAY)
    return channel, pages, found


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, channels = Path(argv[1]), argv[2:]
    seen: set[str] = set()
    if out.exists():
        seen = {
            row[1] for row in csv.reader(out.open(encoding="utf-8")) if len(row) > 1
        }
    fh = out.open("a", encoding="utf-8", newline="")
    writer = csv.writer(fh)
    with ThreadPoolExecutor(max_workers=CHANNEL_WORKERS) as ex:
        futures = [ex.submit(walk_channel, c, seen, writer, fh) for c in channels]
        for fut in as_completed(futures):
            channel, pages, found = fut.result()
            print(f"done {channel}: {pages} pages, {found} new slugs", flush=True)
    fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
