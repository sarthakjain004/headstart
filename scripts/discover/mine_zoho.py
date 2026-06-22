#!/usr/bin/env python3
"""Full Zoho Recruit miner — harvest every Zoho data-center domain from Wayback.

Zoho Recruit runs many regional data centers, each its OWN domain: zohorecruit.com (US),
.in (India), .eu, .com.au, .ca, etc. Tenants live at {tenant}.zohorecruit.{tld} and each
region is a SEPARATE namespace. We had only mined .com, missing the entire .in / .eu /
.com.au / .ca cohorts — the .in one being Indian companies on Zoho's India data center.

This page-mines every data-center domain with a real cohort and folds the deduped tenants
into data/wayback-ats/zoho.csv. Dedup is by full host (url), not the bare label, because
"acme.zohorecruit.com" and "acme.zohorecruit.in" can be different companies.

After running, re-run scripts/merge/merge_tenants.py.

Run:  python scripts/discover/mine_zoho.py
"""
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGES = ROOT / "scripts" / "wayback_pages.py"
WORKERS = "4"

CANON = ("zoho", "zohorecruit.com")        # writes zoho.csv directly (resumes prior state)
REGIONAL = [
    ("zoho_in", "zohorecruit.in"),         # India data center  <- the big India cohort
    ("zoho_eu", "zohorecruit.eu"),         # EU
    ("zoho_au", "zohorecruit.com.au"),     # Australia
    ("zoho_ca", "zohorecruit.ca"),         # Canada
]


def mine(label, host):
    print(f"=== mining {host} ===", flush=True)
    subprocess.run([sys.executable, str(PAGES), label, host, "sub", WORKERS], check=False)


def main():
    mine(*CANON)
    for label, host in REGIONAL:
        mine(label, host)

    zoho = WB / "zoho.csv"
    rows = {}  # key = url (full host)
    if zoho.exists():
        with zoho.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["url"]] = (r["tenant"], r["url"])
    before = len(rows)
    for label, _ in REGIONAL:
        fp = WB / f"{label}.csv"
        if fp.exists():
            with fp.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.setdefault(r["url"], (r["tenant"], r["url"]))
    with zoho.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url"])
        for url in sorted(rows):
            tenant, u = rows[url]
            w.writerow(["zoho", tenant, u])
    print(f"zoho.csv: {before} -> {len(rows)} (+{len(rows) - before})", flush=True)

    for label, _ in REGIONAL:
        (WB / f"{label}.csv").unlink(missing_ok=True)
        (WB / f".{label}_pages_done").unlink(missing_ok=True)
    print("done -- re-run scripts/merge/merge_tenants.py to refresh.", flush=True)


if __name__ == "__main__":
    main()
