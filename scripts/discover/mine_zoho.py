#!/usr/bin/env python3
"""Full Zoho Recruit miner — harvest every Zoho data-center domain from Wayback.

Zoho Recruit runs many regional data centers, each its OWN domain: zohorecruit.com (US),
.in (India), .eu, .com.au, .ca, etc. Tenants live at {tenant}.zohorecruit.{tld} and each
region is a SEPARATE namespace. We had only mined .com, missing the entire .in / .eu /
.com.au / .ca cohorts — the .in one being Indian companies on Zoho's India data center.

The list below is the *complete* set of live Zoho Recruit data centers, enumerated 2026-07-27
by DNS + HTTP probe rather than assumed: .jp, .uk, .sa, .sg and .com.cn also answer, and each
serves the identical careers stack (a bogus tenant returns HTTP 200 with `cl-error-block`, the
soft-404 check_liveness.p_zoho already treats as DEAD). Only .com.br resolves without being a
tenant namespace — its root 400s and the wildcard subdomain doesn't resolve — so it is excluded.
.co.uk/.nl/.de/.fr/.za/.mx have no DNS at all.

This page-mines every data-center domain with a real cohort and folds the deduped tenants
into data/wayback-ats/zoho.csv. Dedup is by full host (url), not the bare label, because
"acme.zohorecruit.com" and "acme.zohorecruit.in" can be different companies. NB the liveness
ledger and data/ats-tenants-merged/ are keyed on the bare *tenant*, so a label present on two
data centers still collapses downstream (44 such hosts measured 2026-07-27) — a full host is
the only collision-free key for Zoho.

After running, re-run scripts/merge/merge_tenants.py.

Run:  python scripts/discover/mine_zoho.py
"""

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGES = ROOT / "scripts" / "discover" / "wayback_pages.py"
WORKERS = "4"

CANON = ("zoho", "zohorecruit.com")  # writes zoho.csv directly (resumes prior state)
REGIONAL = [
    ("zoho_in", "zohorecruit.in"),  # India data center  <- the big India cohort
    ("zoho_eu", "zohorecruit.eu"),  # EU
    ("zoho_au", "zohorecruit.com.au"),  # Australia
    ("zoho_ca", "zohorecruit.ca"),  # Canada
    ("zoho_jp", "zohorecruit.jp"),  # Japan
    ("zoho_sa", "zohorecruit.sa"),  # Saudi Arabia
    ("zoho_cn", "zohorecruit.com.cn"),  # China
    ("zoho_uk", "zohorecruit.uk"),  # UK
    ("zoho_sg", "zohorecruit.sg"),  # Singapore
]


def mine(label, host):
    print(f"=== mining {host} ===", flush=True)
    subprocess.run(
        [sys.executable, str(PAGES), label, host, "sub", WORKERS], check=False
    )


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
