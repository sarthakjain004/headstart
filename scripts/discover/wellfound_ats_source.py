#!/usr/bin/env python3
"""Extract the company -> ATS provider hints Wellfound leaks in its job listings.

Wellfound's JobListingSearchResult carries `atsSource` (e.g. `AtsIntegration::Greenhouse::Listing`)
when the startup syndicates its board from an ATS; run_wellfound.py already normalizes that to the
`ats_source` column of data/jobs/wellfound.csv. This rolls those per-job values up to one row per
company so they can seed the fingerprinter.

It is a *provider* hint, not a resolution: Wellfound never exposes the tenant slug, so a row says
"this company is on Greenhouse", not which Greenhouse board. That still collapses the fingerprinter's
per-company probe from every ATS down to one.

Usage:  python scripts/discover/wellfound_ats_source.py
"""

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "data" / "jobs" / "wellfound.csv"
OUT = ROOT / "data" / "discover" / "wellfound_ats_source.csv"

COLS = ["company", "wellfound_slug", "ats", "jobs"]


def main() -> int:
    # company -> ats -> [job count, wellfound slug]. Keyed by company because that is what the
    # fingerprinter looks up; the slug rides along as the Wellfound-side identifier.
    rows: dict[tuple[str, str], list] = defaultdict(lambda: [0, ""])
    total = 0
    with SRC.open(encoding="utf-8") as f:
        for job in csv.DictReader(f):
            total += 1
            ats = (job.get("ats_source") or "").strip()
            if not ats:
                continue
            entry = rows[(job["company"], ats)]
            entry[0] += 1
            # id is `wellfound:{startup_slug}:{listing_id}`
            parts = job["id"].split(":")
            if not entry[1] and len(parts) >= 3:
                entry[1] = parts[1]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLS)
        for (company, ats), (count, slug) in sorted(rows.items()):
            writer.writerow([company, slug, ats, count])
            print(f"{company}\t{slug}\t{ats}\t{count}", flush=True)

    print(
        f"\n{len(rows)} company/ATS pairs from {total} jobs -> {OUT.relative_to(ROOT)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
