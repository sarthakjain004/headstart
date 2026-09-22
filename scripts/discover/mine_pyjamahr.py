#!/usr/bin/env python3
"""PyjamaHR Board miner — the vendor's own cross-tenant jobs sitemap.

PyjamaHR publishes every tenant's postings on one shared host, ``jobs.pyjamahr.com/{slug}/
{job-slug}``, and lists them all in a single sitemap:

    GET https://jobs.pyjamahr.com/sitemap-jobs.xml

On 2026-09-22 that was 7,801 ``<loc>`` entries naming **680 tenants** in one 2.4 MB fetch — a
roster the platform maintains for us, refreshed daily (``<lastmod>`` values are minutes old). The
first path segment of each URL is the tenant slug, which is exactly the ``company_slug`` the API
takes and the ``slug`` ``headstart.scrapers.pyjamahr`` is keyed by, so nothing has to be resolved.

Two things the sitemap is **not**. It is not a job count: it lags the API on 164 of 680 tenants
(always low, by a handful each), so the liveness prober reads the count from the API, never from
here. And it is not complete: a tenant with no posting in the sitemap is absent, which is why the
Wayback sweep (``wayback_feeder.py``, ``path`` style on this host) runs beside it — it found 77
more tenants on the same day, 3 of them hiring.

Folds the slugs into ``data/ats-tenants-merged/pyjamahr.csv`` (``ats,tenant,url,source``) under
the same additive contract as ``scripts/merge/merge_wayback_into_tenants.py``: a row the sitemap
re-confirms gains the ``sitemap`` tag, a tenant the pool lacks is appended, and no row is ever
dropped. Every slug in the sitemap is lowercase ``[a-z0-9-]``; nothing is normalised.

Run:   python scripts/discover/mine_pyjamahr.py
Then:  python scripts/validate/check_liveness.py pyjamahr      # lands the ledger
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart import http

SITEMAP = "https://jobs.pyjamahr.com/sitemap-jobs.xml"
POOL = ROOT / "data" / "ats-tenants-merged" / "pyjamahr.csv"
TAG = "sitemap"
UA = "headstart/0.1"

_LOC = re.compile(r"<loc>https://jobs\.pyjamahr\.com/([^/<]+)/[^<]+</loc>")


def tenants_in(xml: str) -> dict[str, int]:
    """Tenant slug -> number of postings the sitemap lists for it."""
    counts: dict[str, int] = {}
    for slug in _LOC.findall(xml):
        counts[slug] = counts.get(slug, 0) + 1
    return counts


def fold(pool: Path, tenants: dict[str, int]) -> tuple[int, int, int]:
    """Additive fold into the pool CSV; returns (existing, re-tagged, added)."""
    rows: list[list[str]] = []
    if pool.exists():
        with pool.open(encoding="utf-8") as f:
            rows = [
                [r.get("tenant", ""), r.get("url", ""), r.get("source", "")]
                for r in csv.DictReader(f)
            ]
    existing = {t for t, _, _ in rows}
    retagged = 0
    for row in rows:
        if row[0] in tenants and TAG not in row[2].split("+"):
            row[2] = f"{row[2]}+{TAG}" if row[2] else TAG
            retagged += 1
    added = 0
    for slug in sorted(tenants):
        if slug not in existing:
            rows.append([slug, f"https://jobs.pyjamahr.com/{slug}", TAG])
            added += 1
    pool.parent.mkdir(parents=True, exist_ok=True)
    with pool.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url", "source"])
        for tenant, url, source in sorted(rows):
            w.writerow(["pyjamahr", tenant, url, source])
    return len(existing), retagged, added


def main() -> int:
    response = http.fetch("GET", SITEMAP, headers={"User-Agent": UA}, timeout=60)
    response.raise_for_status()
    tenants = tenants_in(response.text)
    if not tenants:
        print("sitemap parsed to zero tenants — not writing the pool", flush=True)
        return 1
    print(
        f"sitemap: {sum(tenants.values()):,} postings across {len(tenants):,} tenants",
        flush=True,
    )
    existing, retagged, added = fold(POOL, tenants)
    print(
        f"{POOL.relative_to(ROOT)}: {existing} existing, {retagged} re-tagged, "
        f"{added} added, {existing + added} total",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
