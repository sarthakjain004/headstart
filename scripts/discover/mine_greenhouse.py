#!/usr/bin/env python3
"""Full Greenhouse miner — harvest every Greenhouse board-host variant from Wayback.

Greenhouse serves boards from several hosts (legacy US, current US, and per-region data
centers like EU). Mining only the US bare hosts misses the regional cohort entirely — that's
how NK Securities Research (on job-boards.eu.greenhouse.io) was absent: the EU data center was
never crawled. This page-mines all known host variants via wayback_pages.py and folds the
deduped tokens into data/wayback-ats/greenhouse.csv.

After running, re-run scripts/merge/merge_tenants.py to refresh the merged set.

Embedded Boards (`greenhouse.io/embed/job_board?for={slug}` on a Company's own careers page)
have no `{host}/{slug}` URL, so `extract()` here skips them — they are mined separately by
mine_greenhouse_embeds.py, which reads the `for=` parameter and also picks up the
custom-domain cohort. The unified API `boards-api.greenhouse.io/v1/boards/{slug}` is
region-agnostic, so once a Slug is found on any host it resolves the same way.

Run:  python scripts/discover/mine_greenhouse.py
"""

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGES = ROOT / "scripts" / "discover" / "wayback_pages.py"
WORKERS = "4"

# Fail loudly if the helper moves again. This exact path went stale in the scripts/ reorg and
# `check=False` below swallowed the exit-2, so all three wayback miners reported success while
# mining nothing — zoho and lever for weeks, greenhouse until 2026-07-27. A missing helper is a
# broken miner, not a page that happened to fail.
if not PAGES.exists():
    raise SystemExit(f"helper not found: {PAGES} — did scripts/ move?")

# (label, host). Add new regional data-center hosts here as Greenhouse adds them.
HOSTS = [
    ("greenhouse_b", "boards.greenhouse.io"),  # US legacy
    ("greenhouse_jb", "job-boards.greenhouse.io"),  # US current
    (
        "greenhouse_eu_jb",
        "job-boards.eu.greenhouse.io",
    ),  # EU  <- the blind spot (NK lives here)
    ("greenhouse_eu_b", "boards.eu.greenhouse.io"),  # EU legacy
    ("greenhouse_us_jb", "job-boards.us.greenhouse.io"),  # US explicit (usually empty)
    ("greenhouse_us_b", "boards.us.greenhouse.io"),
]


def main():
    # 1. page-mine each host variant (resumable) into its own temp file
    for label, host in HOSTS:
        print(f"=== mining {host} ===", flush=True)
        subprocess.run(
            [sys.executable, str(PAGES), label, host, "path", WORKERS], check=False
        )

    # 2. fold all tokens into data/wayback-ats/greenhouse.csv (dedupe by token)
    gh = WB / "greenhouse.csv"
    rows = {}
    if gh.exists():
        with gh.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["tenant"].lower()] = r["url"]
    before = len(rows)
    for label, _ in HOSTS:
        fp = WB / f"{label}.csv"
        if fp.exists():
            with fp.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.setdefault(r["tenant"].lower(), r["url"])
    with gh.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url"])
        for t in sorted(rows):
            w.writerow(["greenhouse", t, rows[t]])
    print(
        f"greenhouse.csv: {before} -> {len(rows)} (+{len(rows) - before})", flush=True
    )

    # 3. clean up the per-host temp files
    for label, _ in HOSTS:
        (WB / f"{label}.csv").unlink(missing_ok=True)
        (WB / f".{label}_pages_done").unlink(missing_ok=True)
    print(
        "done — re-run scripts/merge/merge_tenants.py to refresh the merged set.",
        flush=True,
    )


if __name__ == "__main__":
    main()
