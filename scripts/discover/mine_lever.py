#!/usr/bin/env python3
"""Full Lever miner — harvest every Lever board-host variant from Wayback.

Lever runs a global instance (jobs.lever.co) and a separate EU instance (jobs.eu.lever.co,
with its own api.eu.lever.co). Mining only the global host misses the EU cohort — the same
blind spot greenhouse had with its EU data center. This page-mines all host variants and folds
the deduped tokens into data/wayback-ats/lever.csv.

The global host writes to lever.csv directly (resuming prior page state); the regional hosts
are mined under their own labels and folded in.

After running, re-run scripts/merge/merge_tenants.py to refresh the merged set.

Note: EU tenants resolve via api.eu.lever.co, not api.lever.co — the url column carries the
host, so a liveness/feed check must derive the API host from the url, not assume the global.

Run:  python scripts/discover/mine_lever.py
"""
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGES = ROOT / "scripts" / "wayback_pages.py"
WORKERS = "4"

CANON = ("lever", "jobs.lever.co")          # writes lever.csv directly, resumes prior state
REGIONAL = [
    ("lever_eu", "jobs.eu.lever.co"),       # EU instance  <- the blind spot
    ("lever_us", "jobs.us.lever.co"),       # US explicit (usually empty)
]


def mine(label, host):
    print(f"=== mining {host} ===", flush=True)
    subprocess.run([sys.executable, str(PAGES), label, host, "path", WORKERS], check=False)


def main():
    mine(*CANON)
    for label, host in REGIONAL:
        mine(label, host)

    lever = WB / "lever.csv"
    rows = {}
    if lever.exists():
        with lever.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["tenant"].lower()] = r["url"]
    before = len(rows)
    for label, _ in REGIONAL:
        fp = WB / f"{label}.csv"
        if fp.exists():
            with fp.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.setdefault(r["tenant"].lower(), r["url"])
    with lever.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url"])
        for t in sorted(rows):
            w.writerow(["lever", t, rows[t]])
    print(f"lever.csv: {before} -> {len(rows)} (+{len(rows) - before})", flush=True)

    for label, _ in REGIONAL:
        (WB / f"{label}.csv").unlink(missing_ok=True)
        (WB / f".{label}_pages_done").unlink(missing_ok=True)
    print("done — re-run scripts/merge/merge_tenants.py to refresh the merged set.", flush=True)


if __name__ == "__main__":
    main()
