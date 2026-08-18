#!/usr/bin/env python3
"""Enumerate Eightfold customers through the shared portal's ``domain`` param.

``app.eightfold.ai`` is Eightfold's multi-tenant careers portal, and its PCSX API answers for
**any** customer, not just its own board::

    GET https://app.eightfold.ai/api/pcsx/search?domain=qualcomm.com  -> 200, count=1918
    GET https://app.eightfold.ai/api/pcsx/search?domain=<non-customer> -> 403

So a company-domain wordlist enumerates customers directly, and — unlike the DNS sweep — it
finds Boards that never got a ``{slug}.eightfold.ai`` host of their own. The regional portals
(``app-eu``/``app-gov``/``app-wu``) answer for customers the US portal 403s, so each domain is
tried against every portal before it is called a miss.

One caveat this encodes: ``volkscience.com`` is the portal's own default group (Eightfold's
pre-rename identity). Any lookup that lands there is Eightfold's own board, not the queried
company — such rows are recorded with ``customer=no``.

Writes ``domain,portal,jobs,first_title`` per hit to ``--out`` as it goes. Concurrency is low
and 429/5xx trip a shared backoff: this is one host absorbing the whole sweep (ADR-0026).

Run:  python -u scripts/discover/eightfold_portal_sweep.py domains.txt --out hits.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http  # needs src on sys.path first

UA = "headstart/0.1 (job-board reader)"
PORTALS = ["app.eightfold.ai", "app-eu.eightfold.ai", "app-wu.eightfold.ai"]
_OWN_GROUP = "volkscience.com"  # portal default -> a hit here is Eightfold's own board
_LOCK = threading.Lock()
_BACKOFF_UNTIL = [0.0]


def _wait() -> None:
    with _LOCK:
        delay = _BACKOFF_UNTIL[0] - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _trip(seconds: float) -> None:
    with _LOCK:
        if _BACKOFF_UNTIL[0] < time.monotonic() + seconds:
            _BACKOFF_UNTIL[0] = time.monotonic() + seconds
            print(f"  [backoff] rate-limited — pausing {seconds:.0f}s", flush=True)


def _ask(portal: str, domain: str) -> tuple[int, int | None, str | None]:
    q = urllib.parse.urlencode(
        {"domain": domain, "query": "", "location": "", "start": 0}
    )
    _wait()
    try:
        r = http.fetch(
            "GET",
            f"https://{portal}/api/pcsx/search?{q}",
            attempts=1,
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Referer": f"https://{portal}/careers",
            },
            timeout=25,
        )
    except http.RequestsError:
        return (0, None, None)
    if r.status_code in (429, 503):
        _trip(30.0)
        return (r.status_code, None, None)
    if r.status_code != 200:
        return (r.status_code, None, None)
    try:
        data = (r.json() or {}).get("data") or {}
    except ValueError:
        return (200, None, None)
    positions = data.get("positions") or []
    title = positions[0].get("name") if positions else None
    return (200, int(data.get("count") or 0), title)


def probe(domain: str) -> dict[str, object] | None:
    """The first portal that answers 200 for this domain, with its Board total. None = miss."""
    for portal in PORTALS:
        status, count, title = _ask(portal, domain)
        if status == 200 and count is not None:
            return {
                "domain": domain,
                "portal": portal,
                "jobs": count,
                "first_title": title or "",
            }
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="domain list files (default: stdin)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    raw: list[str] = []
    for f in args.files:
        raw.extend(open(f, encoding="utf-8", errors="replace").read().split())  # noqa: SIM115
    if not args.files:
        raw.extend(sys.stdin.read().split())
    domains = [d for d in dict.fromkeys(x.strip().lower() for x in raw if x.strip())]
    print(f"sweeping {len(domains)} domains over {len(PORTALS)} portals", flush=True)

    out = open(args.out, "w", newline="", encoding="utf-8")  # noqa: SIM115
    writer = csv.DictWriter(out, fieldnames=["domain", "portal", "jobs", "first_title"])
    writer.writeheader()
    out.flush()
    hits = 0
    done = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(probe, d) for d in domains]
        for fut in as_completed(futures):
            row = fut.result()
            done += 1
            if row and row["domain"] != _OWN_GROUP:
                hits += 1
                writer.writerow(row)
                out.flush()
                print(
                    f"HIT {row['domain']} @{row['portal']} jobs={row['jobs']}",
                    flush=True,
                )
            if done % 500 == 0:
                rate = done / max(time.monotonic() - started, 1e-9)
                print(
                    f"  ... {done}/{len(domains)} probed, {hits} hits, {rate:.1f}/s",
                    flush=True,
                )
    out.close()
    print(f"done: {hits} customer domains -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
