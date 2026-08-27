#!/usr/bin/env python3
"""urlscan.io miner for Zwayam tenants — the boards live on customer domains, so mine the *caller*.

Zwayam career sites are SPAs whose every page calls `public.zwayam.com`. urlscan records every
subresource request a scanned page makes, and indexes them under the `domain:` field — so
`domain:zwayam.com` returns scans of *third-party* hosts that talked to Zwayam, and each result's
`page.domain` is a tenant hostname. This inverts the usual host-mining direction (there is no
`*.zwayam.com` namespace to enumerate) and is the only corpus that records subresource calls at all.

The free search API truncates at 100 results per query, so the sweep slices by scan date
(half-years) to walk past the cap — same trick as `urlscan_miner.py`.

Emits one candidate hostname per line, appending and resuming. Feed the output to
`scripts/discover/zwayam_probe.py` for ground truth.

Run:  python -u scripts/discover/mine_zwayam_urlscan.py OUT_FILE [QUERY ...]
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CTX = ssl._create_unverified_context()
UA = "HeadStart-discovery/0.1 (ATS tenant discovery; polite)"
YEARS = range(2016, 2027)
HALVES = [("01-01", "06-30"), ("07-01", "12-31")]
PACE = 2.5
QUERIES = ["domain:zwayam.com", "page.url:zwayam", "task.url:zwayam"]
SKIP = {"zwayam.com"}  # provider's own hosts are not tenants


def get(url: str) -> dict | None:
    delay = 10
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(delay)
                delay = min(delay * 2, 180)
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(delay)
            delay = min(delay * 2, 180)
    return None


def hosts_in(r: dict) -> set[str]:
    found = set()
    for h in {
        (r.get("page") or {}).get("domain", ""),
        (r.get("task") or {}).get("domain", ""),
        (r.get("page") or {}).get("apexDomain", ""),
    }:
        h = (h or "").lower().strip(".")
        if h and "." in h and not h.endswith("zwayam.com"):
            found.add(h)
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1])
    queries = argv[2:] or QUERIES
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = (
        {ln.strip() for ln in out.read_text().splitlines()} if out.exists() else set()
    )
    seen.discard("")
    total = len(queries) * len(YEARS) * len(HALVES)
    print(f"urlscan-zwayam: {total} queries, {len(seen)} known", flush=True)

    n = 0
    with out.open("a") as f:
        for q in queries:
            for y in YEARS:
                for i, (a, b) in enumerate(HALVES, start=1):
                    n += 1
                    qq = urllib.parse.quote(f"{q} AND date:[{y}-{a} TO {y}-{b}]")
                    data = get(f"https://urlscan.io/api/v1/search/?q={qq}&size=100")
                    time.sleep(PACE)
                    if not data:
                        continue
                    res = data.get("results", [])
                    new = 0
                    for r in res:
                        for h in hosts_in(r) - seen:
                            seen.add(h)
                            f.write(h + "\n")
                            new += 1
                    f.flush()
                    if new or len(res) >= 100:
                        cap = " CAPPED" if len(res) >= 100 else ""
                        print(
                            f"  [{n}/{total}] {q} {y}H{i}: {len(res)} results +{new}{cap} (total {len(seen)})",
                            flush=True,
                        )
    print(f"DONE {len(seen)} candidate hosts -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
