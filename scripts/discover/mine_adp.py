#!/usr/bin/env python3
"""Fold ADP Workforce Now career centers found in local text into the `adp` candidate pool.

ADP publishes no cross-tenant roster, and a Board's key lives in a query string (`cid` + `ccId`
on `workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html`), so discovery is a URL
scan: any text that links a career-center page names a Board. This reads files — the upstream
seed list (`kalil0321/ats-scrapers`' `ats-companies/adp.csv`), third-party company lists, the
Indeed harvest's job pages — pulls every such URL out, and reads each one with the same
`wayback_feeder.extract` the Wayback and Common Crawl sweeps use, so all three sources agree on
what a Board's identity is.

What it covers: only links written out in full. A careers page that embeds the board through
ADP's JavaScript widget, or links it through a redirector, names no `cid` in its text and is
invisible here — the careers-page fingerprinter is the channel for those.

Rows are unioned into ``data/ats-tenants-merged/adp.csv`` (``ats,tenant,url,source``): a Board
already present gains the source tag, a new one is appended. Candidate-grade by design — the
liveness prober decides what is real.

Usage:
    PYTHONPATH=src python scripts/discover/mine_adp.py TAG FILE [FILE ...]
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wayback_feeder import ADP_HOST, ADP_PAGE_URL, extract

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "data" / "ats-tenants-merged" / "adp.csv"


def boards_in(text: str) -> dict[str, str]:
    """``tenant -> board url`` for every career center linked in ``text``."""
    found: dict[str, str] = {}
    for match in ADP_PAGE_URL.finditer(text):
        got = extract(match.group(0).replace("&amp;", "&"), ADP_HOST, "adp")
        if got:
            found.setdefault(*got)
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    tag, files = argv[0], argv[1:]
    found: dict[str, str] = {}
    for name in files:
        with open(name, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "workforcenow" in line:
                    for tenant, url in boards_in(line).items():
                        found.setdefault(tenant, url)
        print(f"{name}: {len(found)} Boards so far", flush=True)

    rows: dict[str, list[str]] = {}
    if POOL.exists():
        with POOL.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["tenant"]] = [r["url"], r["source"]]
    before = len(rows)
    for tenant, url in found.items():
        if tenant in rows:
            if tag not in rows[tenant][1].split("+"):
                rows[tenant][1] = f"{rows[tenant][1]}+{tag}" if rows[tenant][1] else tag
        else:
            rows[tenant] = [url, tag]
    POOL.parent.mkdir(parents=True, exist_ok=True)
    with POOL.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url", "source"])
        for tenant in sorted(rows):
            w.writerow(["adp", tenant, *rows[tenant]])
    print(
        f"{tag}: {len(found)} Boards read, {len(rows) - before} new to the pool, "
        f"{len(rows)} in the pool",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
