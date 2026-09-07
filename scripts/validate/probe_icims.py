"""Liveness for iCIMS Boards, probed through the surface the scraper actually reads.

One `GET /sitemap.xml` per tenant — the same request `ICIMSScraper.fetch_raw` makes, so a Board
marked live here is a Board the scraper can read, and nothing else is guessed at.

A 403 is `dead` on purpose and is not a failure to retry: on 380 boards the set returning 403 was
*exactly* the set serving `Disallow: /` (47/47), so it is iCIMS enforcing a tenant's own opt-out.
Those Boards should never enter a Slice.

Writes `ats,tenant,url,status,jobs,checked_at`, streamed per row.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from headstart.scrapers.base import USER_AGENT

_JOB_LOC = re.compile(r"<loc>[^<]*/jobs/(\d+)/[^<]*/job[^<]*</loc>", re.IGNORECASE)
#: Status codes that answer the question. Everything else is retried rather than believed.
_SETTLED = frozenset({403, 404})
_local = threading.local()

# The prior census saw 827 ConnectionErrors (53%) at concurrency 6 with no retries, every one of
# which answered cleanly on retry — client-side churn, not blocks. Marking those `dead` would
# quietly delete half the roster, so a row is only allowed to mean something after this many tries.
_ATTEMPTS = 4


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def _row(tenant: str, status: str, jobs: object, why: str = "") -> dict[str, object]:
    row: dict[str, object] = {
        "tenant": tenant,
        "url": f"https://{tenant}",
        "status": status,
        "jobs": jobs,
    }
    if why:
        row["why"] = why
    return row


def probe(tenant: str) -> dict[str, object]:
    url = f"https://{tenant}/sitemap.xml"
    last = ""
    for attempt in range(_ATTEMPTS):
        try:
            r = _session().get(url, timeout=45, allow_redirects=True)
        except (requests.RequestException, OSError) as exc:
            last = type(exc).__name__
            if attempt + 1 < _ATTEMPTS:
                time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            jobs = len(set(_JOB_LOC.findall(r.text)))
            return _row(tenant, "live", jobs)
        # 403 and 404 are settled answers: 403 is the tenant's own `Disallow: /` (47/47 measured)
        # and 404 is a retired tenant. Anything else — 429, 5xx, a gateway hiccup — says nothing
        # about the Board and is retried, because a status code does not imply its mechanism and
        # writing `dead` on one transient 503 would evict a live Board from every future Slice.
        if r.status_code in _SETTLED:
            return _row(tenant, "dead", "")
        last = f"HTTP {r.status_code}"
        if attempt + 1 < _ATTEMPTS:
            time.sleep(1.5 * (attempt + 1))
    # Never seen a settled answer. `unknown` keeps the Board out of the scrape list without
    # asserting it is gone — `liveness.load` treats it as not-live, and the next probe re-decides.
    return _row(tenant, "unknown", "", last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", required=True, help="file of hosts, one per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--checked-at", required=True, help="ISO date stamped on every row")
    args = ap.parse_args()

    with open(args.tenants) as fh:
        tenants = sorted({ln.strip().lower() for ln in fh if ln.strip()})
    print(
        f"probing {len(tenants)} iCIMS tenants at concurrency {args.workers}",
        flush=True,
    )

    done = live = 0
    with (
        open(args.out, "w", newline="") as fh,
        ThreadPoolExecutor(args.workers) as pool,
    ):
        writer = csv.writer(fh)
        writer.writerow(["ats", "tenant", "url", "status", "jobs", "checked_at"])
        futures = {pool.submit(probe, t): t for t in tenants}
        for fut in as_completed(
            futures
        ):  # not map(): one slow host must not stall the rest
            row = fut.result()
            writer.writerow(
                [
                    "icims",
                    row["tenant"],
                    row["url"],
                    row["status"],
                    row["jobs"],
                    args.checked_at,
                ]
            )
            fh.flush()
            done += 1
            live += row["status"] == "live"
            if done % 100 == 0 or row["status"] == "live":
                print(
                    f"[{done}/{len(tenants)}] live={live} {row['tenant']} "
                    f"{row['status']} jobs={row['jobs']}",
                    flush=True,
                )
    print(f"done: {live} live of {len(tenants)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
