#!/usr/bin/env python3
"""Re-run the full fingerprint over only the companies with no ATS in data/resolve/coverage.csv.

The miss bucket is inflated by transient misses: a company whose board is found *only* by the
careers-page embed scan (a non-derivable greenhouse slug like Razorpay ->
razorpaysoftwareprivatelimited) drops to "unfound" if that one fetch blips, and verify_misses.py
never recovers it because verify doesn't re-run the careers scan. This pass re-runs the complete
run() (slug-probe + careers scan) on just the unfound, separating real in-house from blips.

Writes data/resolve/recovered_unfound.csv (name,domain,hits). Incremental + budgeted like the main run.
Run:  python scripts/resolve/recover_unfound.py
"""

import asyncio
import csv
import sys
from pathlib import Path

from curl_cffi.requests import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fingerprint import COMPANY_BUDGET, CONCURRENCY, SEED, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
COVERAGE = ROOT / "data" / "resolve" / "coverage.csv"
OUT = ROOT / "data" / "resolve" / "recovered_unfound.csv"


async def main():
    covered = {r["domain"] for r in csv.DictReader(COVERAGE.open(encoding="utf-8"))}
    seed = list(csv.DictReader(SEED.open(encoding="utf-8")))
    unfound = [r for r in seed if r["domain"] not in covered]
    print(
        f"{len(seed)} seed, {len(covered)} covered, {len(unfound)} to recover",
        flush=True,
    )

    cf = OUT.open("w", newline="", encoding="utf-8")
    cw = csv.writer(cf)
    cw.writerow(["name", "domain", "hits"])
    sem = asyncio.Semaphore(CONCURRENCY)
    found = done = 0

    async def worker(row):
        async with sem:
            try:
                return await asyncio.wait_for(run(session, row), COMPANY_BUDGET)
            except Exception:
                return row["name"], row["domain"], set()

    async with AsyncSession() as session:
        for fut in asyncio.as_completed([worker(r) for r in unfound]):
            name, domain, hits = await fut
            done += 1
            label = ";".join(f"{a}:{t}" for a, t in sorted(hits))
            cw.writerow([name, domain, label])
            cf.flush()
            if hits:
                found += 1
                print(
                    f"  [{done}/{len(unfound)}] RECOVERED {name} ({domain}): {label}",
                    flush=True,
                )
    cf.close()
    print(
        f"\n{found}/{len(unfound)} unfound companies recovered -> {OUT.relative_to(ROOT)}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
