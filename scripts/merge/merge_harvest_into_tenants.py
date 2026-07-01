#!/usr/bin/env python3
"""Fold the harvested per-provider lists into the pipeline's candidate pool.

Reads ``data/ats-company-lists/by-provider/{ats}.csv`` (the deduped union of every harvested
source, slug+url) and folds it into ``data/ats-tenants-merged/{ats}.csv`` (the cc/wayback
candidate pool the liveness checker consumes), reshaping to that pool's schema
``ats,tenant,url,source``:

- a tenant already in the pool (from cc/wayback) gets ``harvest`` appended to its source
  (e.g. ``wayback`` -> ``wayback+harvest``); its URL is kept (filled only if it was blank),
- a tenant new to the pool is added with source ``harvest``,
- a provider the pool doesn't have yet gets a brand-new ``{ats}.csv``.

Leaves ``active/`` (the committed, liveness-validated subset) untouched. Idempotent /
re-runnable. Run:  python scripts/merge/merge_harvest_into_tenants.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BYP = ROOT / "data" / "ats-company-lists" / "by-provider"
MERGED = ROOT / "data" / "ats-tenants-merged"

# by-provider name -> the ATS name the pool / scrapers use
RECONCILE = {"zohorecruit": "zoho", "sense": "sensehq", "recruiterbox": "trakstar"}


def load_existing(path: Path) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}  # tenant -> [url, source]
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("tenant") or "").strip().lower()
                if t:
                    rows[t] = [
                        (r.get("url") or "").strip(),
                        (r.get("source") or "").strip(),
                    ]
    return rows


def main() -> int:
    if not BYP.is_dir():
        raise SystemExit(f"no by-provider dir at {BYP}")
    MERGED.mkdir(parents=True, exist_ok=True)

    # accumulate harvest per target ATS (several by-provider files can map to one ats)
    harvest: dict[str, dict[str, str]] = {}
    for p in sorted(BYP.glob("*.csv")):
        if p.stem.startswith("_"):
            continue
        ats = RECONCILE.get(p.stem, p.stem)
        bucket = harvest.setdefault(ats, {})
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("slug") or "").strip().strip("/").lower()
                if t:
                    bucket.setdefault(t, (r.get("url") or "").strip())

    print(f"{'ATS':<20}{'existing':>9}{'+new':>7}{'total':>8}  new-file")
    new_files = added_total = 0
    for ats in sorted(harvest):
        path = MERGED / f"{ats}.csv"
        is_new = not path.exists()
        pool = load_existing(path)
        added = 0
        for tenant, url in harvest[ats].items():
            if tenant in pool:
                cur_url, src = pool[tenant]
                if "harvest" not in src.split("+"):
                    pool[tenant][1] = f"{src}+harvest" if src else "harvest"
                if not cur_url and url:
                    pool[tenant][0] = url
            else:
                pool[tenant] = [url, "harvest"]
                added += 1
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ats", "tenant", "url", "source"])
            for t in sorted(pool):
                w.writerow([ats, t, pool[t][0], pool[t][1]])
        new_files += is_new
        added_total += added
        print(
            f"{ats:<20}{len(pool) - added:>9}{added:>7}{len(pool):>8}  {'NEW' if is_new else ''}"
        )

    print(
        f"\nfolded harvest into {len(harvest)} providers "
        f"({new_files} new CSVs, +{added_total} tenants new to the pool). active/ untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
