#!/usr/bin/env python3
"""urlscan.io miner — harvest ATS tenant hosts from public scan submissions.

urlscan.io records the hosts people submit for scanning, which makes it a discovery source
the archive-based feeders miss entirely: it sees boards that were never crawled by Wayback
or Common Crawl. On the Zoho sweep it was the single highest-yield source — 127 of 269 new
Boards came from it and nothing else (2026-07-27).

**The cap is the whole trick.** The free search API silently truncates at 100 results per
query, so a plain `domain:{d}` query looks exhausted while hiding most of the cohort. Slicing
the same query by scan date walks past it: half-year buckets took one Zoho sweep from 117 to
434 hosts. Widen the buckets only if a domain returns far fewer than 100 per slice.

Emits one host per line, appending. Re-running resumes: hosts already in the output file are
loaded first and never re-emitted, so a run killed mid-sweep costs only its current query.

Only direct subdomains are kept — `acme.zohorecruit.in` yes, `a.b.zohorecruit.in` no — since
an ATS tenant sits exactly one label above the provider domain.

After running, fold the hosts into the ATS's candidate list and re-run
scripts/merge/merge_tenants.py.

Run:  python scripts/discover/urlscan_miner.py OUT_FILE DOMAIN [DOMAIN ...]
  e.g. python scripts/discover/urlscan_miner.py data/scratch/zoho_hosts.txt \\
           zohorecruit.com zohorecruit.in zohorecruit.eu
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
YEARS = range(2017, 2027)  # urlscan's useful history; earlier years are near-empty
HALVES = [("01-01", "06-30"), ("07-01", "12-31")]
PACE = 2.5  # seconds between queries — urlscan 429s a faster free-tier caller


def get(url: str) -> dict | None:
    """One search call, with exponential backoff on rate limits. None if it never succeeds."""
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
        except Exception:
            time.sleep(delay)
            delay = min(delay * 2, 180)
    return None


def hosts_in(result: dict, domain: str) -> set[str]:
    """The direct subdomains of `domain` named by one search result (page and task hosts)."""
    found = set()
    for h in {
        (result.get("page") or {}).get("domain", ""),
        (result.get("task") or {}).get("domain", ""),
    }:
        h = (h or "").lower().strip(".")
        if h.endswith("." + domain) and h.count(".") == domain.count(".") + 1:
            found.add(h)
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, domains = Path(argv[1]), argv[2:]
    out.parent.mkdir(parents=True, exist_ok=True)

    seen = (
        {ln.strip() for ln in out.read_text().splitlines() if ln.strip()}
        if out.exists()
        else set()
    )
    total = len(domains) * len(YEARS) * len(HALVES)
    print(
        f"urlscan: {total} queries over {len(domains)} domains, {len(seen)} known",
        flush=True,
    )

    n = 0
    with out.open("a") as f:
        for d in domains:
            for y in YEARS:
                for i, (a, b) in enumerate(HALVES, start=1):
                    n += 1
                    q = urllib.parse.quote(f"domain:{d} AND date:[{y}-{a} TO {y}-{b}]")
                    data = get(f"https://urlscan.io/api/v1/search/?q={q}&size=100")
                    time.sleep(PACE)
                    if not data:
                        continue
                    new = 0
                    for r in data.get("results", []):
                        for h in hosts_in(r, d) - seen:
                            seen.add(h)
                            f.write(h + "\n")
                            new += 1
                    f.flush()  # stream: a killed run keeps everything already found
                    if new:
                        print(
                            f"  [{n}/{total}] {d} {y}H{i}: +{new} (total {len(seen)})",
                            flush=True,
                        )
    print(f"DONE {len(seen)} hosts -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
