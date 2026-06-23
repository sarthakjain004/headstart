#!/usr/bin/env python3
"""Verify a scraper by running it on N companies from the candidate pool.

Usage:  python scripts/validate/verify_scraper.py <ats> [N=100]

Loads an evenly-strided sample of N tenants from data/ats-tenants-merged/<ats>.csv, runs the
scraper on each concurrently, streams per-company results, and reports: reachable / returned-jobs
/ total jobs / jobs-with-description, plus a couple of sample jobs.

The pool is candidate-grade (NOT liveness-validated), so many tenants will be dead — that's
expected. What we check: every *live* board parses cleanly and descriptions are populated.
"""
from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers.registry import SCRAPERS, get_scraper  # noqa: E402

MERGED = ROOT / "data" / "ats-tenants-merged"
WORKERS = 16


def load_pool(ats: str, n: int) -> list[tuple[str, str]]:
    """An evenly-strided sample of (slug, company) from the pool CSV."""
    cls = SCRAPERS[ats]
    rows: list[tuple[str, str]] = []
    with (MERGED / f"{ats}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t, u = (r.get("tenant") or "").strip(), (r.get("url") or "").strip()
            if t:
                rows.append((cls.slug_from(t, u), t))
    if len(rows) <= n:
        return rows
    stride = len(rows) / n
    return [rows[int(i * stride)] for i in range(n)]


def run_one(ats: str, slug: str, company: str):
    jobs = get_scraper(ats, slug, company).fetch()
    return len(jobs), sum(1 for j in jobs if j.description), jobs[:1]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in SCRAPERS:
        print(f"usage: verify_scraper.py <ats> [N=100]   known: {sorted(SCRAPERS)}")
        return 2
    ats = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    pool = load_pool(ats, n)
    print(f"verifying {ats} on {len(pool)} pool companies (workers={WORKERS}) ...\n", flush=True)

    reachable = with_jobs = total_jobs = total_desc = errors = 0
    samples = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(run_one, ats, s, c): c for s, c in pool}
        for fut in as_completed(futs):
            company = futs[fut]
            try:
                njobs, ndesc, sample = fut.result()
            except Exception as exc:  # noqa: BLE001 - dead board / timeout / parse fail
                errors += 1
                if errors <= 8:
                    print(f"  x {company:<26} {type(exc).__name__}: {str(exc)[:55]}", flush=True)
                continue
            reachable += 1
            total_jobs += njobs
            total_desc += ndesc
            if njobs:
                with_jobs += 1
                if len(samples) < 2 and sample and sample[0].description:
                    samples.append(sample[0])
                print(f"  + {company:<26} {njobs:>4} jobs  {ndesc:>4} w/desc", flush=True)

    pct = 100 * total_desc // max(total_jobs, 1)
    print(f"\n=== {ats}: {len(pool)} tried | {reachable} reachable | {with_jobs} returned jobs "
          f"| {total_jobs} jobs | {total_desc} w/desc ({pct}%) | {errors} dead/error ===")
    for s in samples:
        print(f"\n  sample: {s.title}  @ {s.company}  [{s.location}]  remote={s.remote}")
        print(f"    url:  {s.url}")
        print(f"    dept: {s.department}   type: {s.employment_type}")
        print(f"    desc: {(s.description or '')[:170]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
