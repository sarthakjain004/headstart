#!/usr/bin/env python3
"""SuccessFactors RMK board discovery via urlscan.io — mine the *referring* pages, not a namespace.

SuccessFactors is the one supported ATS with no enumerable namespace: an RMK board's slug is the
customer's own vanity host (careers.wipro.com, jobs.sap.com), so there is nothing to sweep. But
every RMK board betrays itself two ways, and urlscan.io indexes both:

  1. **Subresource** — every RMK page loads assets from ``rmkcdn.successfactors.com``. urlscan
     records every host a scanned page contacted, so ``domain:rmkcdn.successfactors.com`` returns
     scans *of the boards themselves*; each result's ``page.domain`` is a vanity host.
  2. **Address** — a vanity host is a CNAME to ``{key}.jobs2web.com`` -> ``rmkNN.jobs2web.com``,
     which lands on one of ~17 SAP pod IPs. ``page.ip:{ip}`` finds boards whose scan predates the
     CDN, or whose scan never resolved the subresources.

The free search API caps a *page* at 100 results, but ``search_after`` deep-paginates past the
10k window (verified 2026-07-27), so each query is walked to exhaustion rather than date-bucketed.

Output is candidate-grade: a host that merely *loaded* RMK assets may be a corporate careers page
that iframes the board. Confirm with the DNS oracle (a CNAME into jobs2web.com) and the liveness
probe before adding to the pool.

Run:  python -u scripts/discover/mine_successfactors_urlscan.py [OUT_FILE]
      default OUT_FILE = data/scratch/sf/urlscan_hosts.txt  (append-only, resumable)
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CTX = ssl._create_unverified_context()
UA = "HeadStart-discovery/0.1 (ATS board discovery; polite)"
PACE = 2.0  # seconds between calls — urlscan 429s a faster free-tier caller
MAX_PAGES = 400  # 400 x 100 covers any one query's cohort

# The RMK pod IPs every vanity host's CNAME chain lands on (rmk01-99.jobs2web.com, resolved
# 2026-07-27 -> experiment/ats-gap-successfactors/artifacts/2026-07-27_rmk_pods_dns.txt).
POD_IPS = [
    "130.214.193.81",
    "130.214.251.104",
    "121.91.105.76",
    "20.200.113.11",
    "20.201.47.58",
    "20.250.82.26",
    "20.53.137.97",
    "20.72.77.70",
    "34.143.247.97",
    "34.166.153.97",
    "34.166.173.235",
    "34.84.36.126",
    "34.89.238.75",
    "34.90.160.40",
    "35.207.216.49",
    "48.200.29.57",
    "52.139.5.79",
]

QUERIES = [
    "domain:rmkcdn.successfactors.com",  # the RMK asset CDN — the strongest signal
    "domain:jobs2web.com",  # the CNAME target, sometimes a direct subresource
    "domain:dsp.successfactors.com",  # RMK's tracking pixel on some tenants
    *[f'page.ip:"{ip}"' for ip in POD_IPS],
]

# Hosts that are SAP's own surfaces or scan noise, never a customer board.
_SKIP_SUFFIX = (
    ".successfactors.com",
    ".successfactors.eu",
    ".sapsf.com",
    ".sapsf.eu",
    ".jobs2web.com",
    ".sap.com",
    ".google.com",
    ".gstatic.com",
    ".doubleclick.net",
    ".cloudfront.net",
    ".akamaized.net",
)


def get(url: str) -> dict | None:
    """One search call with backoff on rate limits. None if it never succeeds."""
    delay = 10
    for _ in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(delay)
                delay = min(delay * 2, 240)
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(delay)
            delay = min(delay * 2, 240)
    return None


def hosts_in(result: dict) -> set[str]:
    """Candidate board hosts named by one scan result."""
    found = set()
    for h in (
        (result.get("page") or {}).get("domain"),
        (result.get("task") or {}).get("domain"),
        (result.get("page") or {}).get("apexDomain"),
    ):
        h = (h or "").lower().strip(".")
        if not h or "." not in h or h.endswith(_SKIP_SUFFIX):
            continue
        found.add(h)
    return found


def walk(query: str, seen: set[str], out) -> int:
    """Deep-paginate one query with search_after, streaming new hosts. Returns hosts added."""
    base = f"https://urlscan.io/api/v1/search/?q={urllib.parse.quote(query)}&size=100"
    added = 0
    after = None
    for page in range(MAX_PAGES):
        url = base + (f"&search_after={urllib.parse.quote(after)}" if after else "")
        data = get(url)
        time.sleep(PACE)
        if not data:
            print(f"    [{query}] page {page}: no data, stopping", flush=True)
            break
        results = data.get("results") or []
        if not results:
            break
        new = 0
        for r in results:
            for h in hosts_in(r) - seen:
                seen.add(h)
                out.write(h + "\n")
                new += 1
        out.flush()  # stream: a killed run keeps everything already found
        added += new
        if new:
            print(
                f"    [{query}] page {page}: +{new} (query {added}, total {len(seen)})",
                flush=True,
            )
        sort = (results[-1] or {}).get("sort")
        if not data.get("has_more") or not sort:
            break
        after = ",".join(str(x) for x in sort)
    return added


def main(argv: list[str]) -> int:
    out_path = (
        Path(argv[1]) if len(argv) > 1 else ROOT / "data/scratch/sf/urlscan_hosts.txt"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = (
        {ln.strip() for ln in out_path.read_text().splitlines() if ln.strip()}
        if out_path.exists()
        else set()
    )
    print(f"urlscan: {len(QUERIES)} queries, {len(seen)} hosts known", flush=True)
    with out_path.open("a") as f:
        for i, q in enumerate(QUERIES, 1):
            print(f"  [{i}/{len(QUERIES)}] {q}", flush=True)
            n = walk(q, seen, f)
            print(f"  [{i}/{len(QUERIES)}] {q}: +{n} hosts", flush=True)
    print(f"DONE {len(seen)} candidate hosts -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
