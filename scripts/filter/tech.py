#!/usr/bin/env python3
"""Filter the scraped jobs down to the tech subset (ADR-0017).

``data/jobs/{ats}.jsonl`` (every scraped job) -> ``data/jobs/tech/{ats}.jsonl`` (software/tech only).

The scrapers keep writing the full set; this stage keeps only software/tech roles
(``headstart.tech_filter``, recall-biased — a non-tech job creeping in is fine, dropping a tech job
is not) into ``data/jobs/tech/``, which is what the embedding / index / UI consume. Dropping the
non-tech ~83% means the embedding model only ever works on the jobs the product actually serves.

Run from repo root:
    .venv/bin/python scripts/filter/tech.py
Verify recall afterwards with ``scripts/filter/verify_tech.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from headstart.tech_filter import filter_jobs

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "data" / "jobs")
    ap.add_argument("--dst", type=Path, default=ROOT / "data" / "jobs" / "tech")
    args = ap.parse_args()
    if not args.src.is_dir():
        raise SystemExit(f"no source dir at {args.src}")

    stats = filter_jobs(args.src, args.dst)
    print(f"{'ATS':<16}{'kept':>9}{'total':>9}{'kept%':>8}")
    kept = total = 0
    for ats, (k, t) in sorted(stats.items()):
        kept += k
        total += t
        if t:
            print(f"{ats:<16}{k:>9}{t:>9}{100 * k / t:>7.1f}%")
    if total:
        print(
            f"{'TOTAL':<16}{kept:>9}{total:>9}{100 * kept / total:>7.1f}%"
            f"  (dropped {total - kept} non-tech) -> {args.dst}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
