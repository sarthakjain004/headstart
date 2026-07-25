#!/usr/bin/env python3
"""Fold the Wellfound-resolved tenants into the pipeline's candidate pool.

Reads ``data/discover/wellfound_resolved_tenants.csv`` (companies Wellfound told us sit on a
supported ATS, whose slug wellfound_slug_probe.py then confirmed live) and folds it into
``data/ats-tenants-merged/{ats}.csv`` — the pool the liveness checker consumes — reshaping to that
pool's ``ats,tenant,url,source`` schema, same contract as merge_harvest_into_tenants.py:

- a tenant already in the pool gets ``wellfound`` appended to its source,
- a tenant new to the pool is added with source ``wellfound``,
- ``active/`` (the committed, liveness-validated subset) is left untouched.

Idempotent / re-runnable. Run:  python scripts/merge/merge_wellfound_into_tenants.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "discover" / "wellfound_resolved_tenants.csv"
MERGED = ROOT / "data" / "ats-tenants-merged"
COLS = ["ats", "tenant", "url", "source"]
TAG = "wellfound"


def main() -> int:
    by_ats: dict[str, list[dict]] = {}
    for r in csv.DictReader(SRC.open(encoding="utf-8")):
        by_ats.setdefault(r["ats"], []).append(r)

    for ats, incoming in sorted(by_ats.items()):
        path = MERGED / f"{ats}.csv"
        rows: dict[str, list[str]] = {}  # tenant -> [url, source]
        if path.exists():
            for r in csv.DictReader(path.open(encoding="utf-8")):
                t = (r.get("tenant") or "").strip()
                if t:
                    rows[t] = [
                        (r.get("url") or "").strip(),
                        (r.get("source") or "").strip(),
                    ]

        added = tagged = 0
        for r in incoming:
            t = r["tenant"]
            if t in rows:
                src = rows[t][1]
                if TAG not in src.split("+"):
                    rows[t][1] = f"{src}+{TAG}" if src else TAG
                    tagged += 1
                if not rows[t][0]:
                    rows[t][0] = r["url"]
            else:
                rows[t] = [r["url"], TAG]
                added += 1

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(COLS)
            for tenant in sorted(rows):
                url, source = rows[tenant]
                w.writerow([ats, tenant, url, source])
        print(
            f"{ats:12s} +{added} new, {tagged} re-tagged -> {len(rows)} tenants in pool",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
