#!/usr/bin/env python3
"""Gauge how well ``headstart.experience`` covers the tech corpus, per ATS (ADR-0009, ADR-0018).

Runs ``experience.extract(field, description, title)`` over ``data/jobs/tech/{ats}.jsonl`` and reports,
per ATS, how many jobs got a years number and from which tier (field / regex / seniority) vs ``none``.
This is the quick gauge to run after changing the extractor's patterns, and ``--misses`` dumps a
sample of the still-missed descriptions to *read manually* (the pattern-finding half of the loop).

    .venv/bin/python scripts/enrich/experience_coverage.py                 # the coverage table
    .venv/bin/python scripts/enrich/experience_coverage.py --misses lever  # read missed descriptions
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import sys
from pathlib import Path

from headstart.experience import extract

ROOT = Path(__file__).resolve().parents[2]
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def _jobs(jobs_dir: Path):
    for f in sorted(jobs_dir.glob("*.jsonl")):
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield f.stem, json.loads(line)


def _coverage(jobs_dir: Path) -> None:
    per: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ats, j in _jobs(jobs_dir):
        span = extract(j.get("experience"), j.get("description"), j.get("title"))
        c = per[ats]
        c["n"] += 1
        c[span.source if span else "none"] += 1

    header = f"{'ATS':<16}{'jobs':>7}{'field':>7}{'regex':>7}{'senior':>7}{'none':>7}{'cov%':>6}"
    print(header)
    tot: collections.Counter = collections.Counter()
    for ats in sorted(per):
        c = per[ats]
        for k in c:
            tot[k] += c[k]
        cov = c["field"] + c["regex"] + c["seniority"]
        print(
            f"{ats:<16}{c['n']:>7}{c['field']:>7}{c['regex']:>7}{c['seniority']:>7}"
            f"{c['none']:>7}{100 * cov / c['n']:>5.0f}%"
        )
    cov = tot["field"] + tot["regex"] + tot["seniority"]
    print(
        f"{'TOTAL':<16}{tot['n']:>7}{tot['field']:>7}{tot['regex']:>7}{tot['seniority']:>7}"
        f"{tot['none']:>7}{100 * cov / tot['n']:>5.0f}%"
    )


def _misses(jobs_dir: Path, ats: str, n: int, seed: int) -> None:
    rows = [
        j
        for a, j in _jobs(jobs_dir)
        if a == ats
        and not extract(j.get("experience"), j.get("description"), j.get("title"))
        and len(j.get("description") or "") >= 400
    ]
    print(f"{ats}: {len(rows)} substantial misses — sampling {min(n, len(rows))}")
    for j in random.Random(seed).sample(rows, min(n, len(rows))):
        print(f"\n### {j.get('title', '')[:75]}")
        print("   ", re.sub(r"\s+", " ", j.get("description") or "")[:1200])


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "jobs" / "tech")
    ap.add_argument(
        "--misses",
        metavar="ATS",
        help="sample missed descriptions for one ATS instead of the table",
    )
    ap.add_argument("--n", type=int, default=8, help="miss samples to show (--misses)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if not args.dir.is_dir():
        raise SystemExit(f"no jobs dir at {args.dir}")
    if args.misses:
        _misses(args.dir, args.misses, args.n, args.seed)
    else:
        _coverage(args.dir)


if __name__ == "__main__":
    main()
