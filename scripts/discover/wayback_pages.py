#!/usr/bin/env python3
"""Page-based Wayback harvester for one ATS — the method for dense domains.

The CDX index is split into N pages (see ?showNumPages=true). Unlike the flat limit (stuck on
early tenants) or filters/host-collapse (which time out), every page is directly addressable
with &page=K, so we fetch ALL pages CONCURRENTLY, extract hosts, and dedup to the full tenant
set in one bounded pass.

Sweeps every board host the ATS serves from (`wayback_common.PROVIDERS`), not just one: Zoho
alone spreads 8,197 known tenants over 8 TLDs. Writes new tenants to data/wayback-ats/{ats}.csv
as pages complete. Resumable: completed page numbers are recorded per host in
data/wayback-ats/.{ats}_{host}_pages_done, so re-running skips finished pages.

Usage:  python scripts/discover/wayback_pages.py zoho
        python scripts/discover/wayback_pages.py zoho --workers 20
        python scripts/discover/wayback_pages.py zoho --domain zohorecruit.in   # one host only
"""

import argparse
import csv
import socket
import ssl
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from wayback_common import PROVIDERS, extract, targets

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)


def get(url):
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            pass
    return None


def sweep(ats, domain, style, workers, seen, writer, out_file):
    """Harvest every CDX page for one host, appending new tenants as pages land."""
    cdx = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(domain)}&matchType=domain"
    base = cdx + "&fl=original&collapse=urlkey"  # showNumPages needs the clean url

    npages_txt = get(cdx + "&showNumPages=true")
    if not npages_txt or not npages_txt.strip().isdigit():
        print(f"{ats}/{domain}: could not get page count: {npages_txt!r}", flush=True)
        return
    npages = int(npages_txt.strip())

    state = WB / f".{ats}_{domain}_pages_done"
    done = set()
    if state.exists():
        done = {int(x) for x in state.read_text().split() if x.strip().isdigit()}
    todo = [p for p in range(npages) if p not in done]
    print(
        f"{ats}/{domain}: {npages} pages, {len(done)} done, {len(todo)} to fetch, "
        f"{len(seen)} tenants so far",
        flush=True,
    )

    lock = threading.Lock()
    counter = {"n": 0}
    with state.open("a", encoding="utf-8") as sf:

        def do(page):
            text = get(f"{base}&page={page}")
            if text is None:
                return
            urls = [ln for ln in text.split("\n") if ln]
            with lock:
                for u in urls:
                    found = extract(u, domain, style)
                    if found and found[0].lower() not in seen:
                        seen.add(found[0].lower())
                        writer.writerow([ats, found[0], found[1]])
                out_file.flush()
                sf.write(f"{page}\n")
                sf.flush()
                counter["n"] += 1
                if counter["n"] % 25 == 0:
                    print(
                        f"  {ats}/{domain}: {counter['n']}/{len(todo)} pages, "
                        f"{len(seen)} tenants",
                        flush=True,
                    )

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(do, p) for p in todo]):
                fut.result()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ats", help=f"one of: {', '.join(sorted(PROVIDERS))}")
    ap.add_argument("--domain", help="sweep only this host instead of all of the ATS's")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    WB.mkdir(parents=True, exist_ok=True)
    out = WB / f"{args.ats}.csv"
    seen = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["tenant"].lower())
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")

    with out.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for domain, style in targets(args.ats, args.domain):
            sweep(args.ats, domain, style, args.workers, seen, writer, f)
    print(
        f"DONE: {len(seen)} unique {args.ats} tenants in data/wayback-ats/{args.ats}.csv",
        flush=True,
    )


if __name__ == "__main__":
    main()
