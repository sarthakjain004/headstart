#!/usr/bin/env python3
"""Resume-key Wayback harvester for one ATS — the patient alternative to page-based sweeping.

Walks the ENTIRE CDX result set with resume-key pagination and appends newly-found unique
tenants to data/wayback-ats/{ats}.csv after every page. On dense domains `wayback_pages.py` is
faster (random access to every page, concurrently); this one earns its keep where the walk has
to be filtered server-side, via --filter.

Sweeps every board host the ATS serves from (`wayback_common.PROVIDERS`), not just one.
Resumable: the cursor is saved per host to data/wayback-ats/.{ats}_{host}_resume after each
page, so you can stop (Ctrl+C) any time and re-run to continue.

Usage:  python scripts/discover/wayback_paginate.py zoho
        python scripts/discover/wayback_paginate.py zoho --max-pages 5      # just 5 (test)
        python scripts/discover/wayback_paginate.py eightfold --filter 'urlkey:ai,eightfold,.*'
"""

import argparse
import csv
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wayback_common import PROVIDERS, extract, targets

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGE = 15000  # urls per CDX page
SLEEP = 1.0  # politeness between pages (seconds)
TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)


def fetch_page(domain, resume, cdx_filter):
    url = (
        f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(domain)}"
        f"&matchType=domain&fl=original&collapse=urlkey&limit={PAGE}&showResumeKey=true"
    )
    if cdx_filter:
        url += "&filter=" + urllib.parse.quote(cdx_filter, safe="")
    if resume:
        url += "&resumeKey=" + urllib.parse.quote(resume, safe="")
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                text = r.read().decode("utf-8", "replace")
            lines = text.split("\n")
            while lines and lines[-1] == "":
                lines.pop()
            nxt = None
            if len(lines) >= 2 and lines[-2] == "":  # blank line then resume key
                nxt = lines[-1].strip() or None
                lines = lines[:-2]
            return [ln for ln in lines if ln], nxt
        except Exception as e:
            print(f"  fetch attempt {attempt} failed: {e}", flush=True)
            time.sleep(5 * attempt)
    return None, resume  # failed; caller stops, state preserved


def sweep(ats, domain, style, max_pages, cdx_filter, seen, writer, out_file):
    """Walk one host's CDX result set from its saved cursor to the end."""
    state = WB / f".{ats}_{domain}_resume"
    resume = state.read_text(encoding="utf-8").strip() if state.exists() else ""
    print(
        f"start: {ats}/{domain} ({style}) | already have {len(seen)} tenants"
        + (f" | filter={cdx_filter}" if cdx_filter else "")
        + (" | resuming" if resume else ""),
        flush=True,
    )

    pages = total = 0
    while True:
        urls, nxt = fetch_page(domain, resume, cdx_filter)
        if urls is None:
            print(
                "fetch failed repeatedly — cursor saved, re-run to resume", flush=True
            )
            return
        pages += 1
        total += len(urls)
        new = 0
        for u in urls:
            found = extract(u, domain, style)
            if found and found[0].lower() not in seen:
                seen.add(found[0].lower())
                writer.writerow([ats, found[0], found[1]])
                new += 1
        out_file.flush()
        print(
            f"{ats}/{domain} page {pages}: scanned {len(urls)} urls, +{new} new "
            f"(tenants={len(seen)}, urls_total={total})",
            flush=True,
        )
        if nxt and (max_pages == 0 or pages < max_pages):
            resume = nxt
            state.write_text(resume, encoding="utf-8")
            time.sleep(SLEEP)
            continue
        if not nxt:
            print(f"{ats}/{domain}: reached end of results", flush=True)
            state.unlink(missing_ok=True)
        else:
            # Save the cursor we stopped *before*, so resuming picks up at the next page rather
            # than re-walking this one — the message claimed this before the write existed.
            state.write_text(nxt, encoding="utf-8")
            print(
                f"{ats}/{domain}: stopped at max_pages={max_pages} (cursor saved)",
                flush=True,
            )
        return


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ats", help=f"one of: {', '.join(sorted(PROVIDERS))}")
    ap.add_argument("--domain", help="sweep only this host instead of all of the ATS's")
    ap.add_argument("--max-pages", type=int, default=0, help="0 = walk to the end")
    ap.add_argument(
        "--filter",
        dest="cdx_filter",
        help="server-side CDX filter, to skip a dense apex whose captures sort before the "
        "subdomains (e.g. 'urlkey:ai,eightfold,.*')",
    )
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
            sweep(
                args.ats,
                domain,
                style,
                args.max_pages,
                args.cdx_filter,
                seen,
                writer,
                f,
            )
    print(
        f"DONE: {len(seen)} unique {args.ats} tenants in data/wayback-ats/{args.ats}.csv",
        flush=True,
    )


if __name__ == "__main__":
    main()
