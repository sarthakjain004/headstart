#!/usr/bin/env python3
"""Measure what BITMAP/BTREE scalar indexes buy the served query paths.

Runs the three shapes :meth:`headstart.search.JobSearch.run` issues — a filtered browse, a
filtered vector page, and the sorted 2,000-row window — plus a bare filtered count, against a
spread of filter selectivities. Measures each with no index, creates the scalar indexes, and
measures again.

    python -m scripts.bench.scalar_index_bench --db data/lancedb --table jobs

The point is which *predicates* an index can serve. A `LIKE '%x%'` clause cannot use one, and
several of the served filters (location, company, keyword, employment_type) are exactly that —
so the interesting number is the gap between the indexable rows and those.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path

import lancedb

from headstart.search import RESULT_COLUMNS

# (label, where-clause, which columns the clause could use an index on)
FILTERS: list[tuple[str, str | None, str]] = [
    ("none", None, "-"),
    ("remote=true", "remote = true", "remote"),
    ("ats=greenhouse", "ats = 'greenhouse'", "ats"),
    ("min_years<=3", "min_years <= 3", "min_years"),
    ("salary>=100k", "min_salary_annual >= 100000", "min_salary_annual"),
    (
        "combined(ats+remote+yrs)",
        "ats = 'greenhouse' AND remote = true AND min_years <= 3",
        "ats,remote,min_years",
    ),
    (
        "posted_at>=2026-08",
        "posted_at >= '2026-08-01' AND posted_at LIKE '____-__-__%'",
        "posted_at (prefix LIKE)",
    ),
    # Controls: no scalar index can serve a leading-wildcard LIKE.
    ("employment=full (LIKE)", "lower(employment_type) LIKE '%full%'", "none — LIKE"),
    ("location LIKE bangalore", "lower(location) LIKE '%bangalore%'", "none — LIKE"),
]

SCALAR_INDEXES: list[tuple[str, str]] = [
    ("ats", "BITMAP"),
    ("remote", "BITMAP"),
    ("employment_type", "BITMAP"),
    ("experience_source", "BITMAP"),
    ("min_years", "BTREE"),
    ("min_salary_annual", "BTREE"),
    ("posted_at", "BTREE"),
    ("first_seen", "BTREE"),
]


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _median_ms(fn, reps: int, warmup: int = 2) -> tuple[float, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), max(samples)


def _paths(table, projection: list[str], vector, where: str | None):
    """The four measured shapes, as callables."""

    def _base(with_vector: bool):
        s = table.search(vector).metric("cosine") if with_vector else table.search()
        if where:
            s = s.where(where, prefilter=True)
        cols = [*projection, "_distance"] if with_vector else list(projection)
        return s.select(cols)

    return {
        "count_only": lambda: (
            table.count_rows(filter=where) if where else table.count_rows()
        ),
        "browse_20": lambda: _base(False).limit(20).to_list(),
        "query_page_20": lambda: _base(True).limit(20).to_list(),
        "query_window_2000": lambda: _base(True).limit(2000).to_list(),
    }


def measure(table, projection, vector, reps: int, phase: str) -> list[dict]:
    rows = []
    for label, where, servable in FILTERS:
        matched = table.count_rows(filter=where) if where else table.count_rows()
        rec = {
            "filter": label,
            "where": where,
            "indexable_on": servable,
            "matched": matched,
        }
        for name, fn in _paths(table, projection, vector, where).items():
            med, worst = _median_ms(fn, reps)
            rec[name] = round(med, 1)
            rec[name + "_max"] = round(worst, 1)
        rows.append(rec)
        print(
            f"[{phase}] {label:<26} n={matched:>7}  "
            f"count={rec['count_only']:>7.1f}  browse={rec['browse_20']:>7.1f}  "
            f"page={rec['query_page_20']:>7.1f}  window={rec['query_window_2000']:>7.1f}  (ms)",
            flush=True,
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/lancedb")
    ap.add_argument("--table", default="jobs")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--out", default="experiment/lancedb-scalar-index/artifacts")
    args = ap.parse_args()

    import numpy as np

    db = lancedb.connect(args.db)
    table = db.open_table(args.table)
    lance_dir = Path(args.db) / f"{args.table}.lance"
    schema_names = set(table.schema.names)
    projection = [c for c in RESULT_COLUMNS if c in schema_names]
    dim = 768
    rng = np.random.default_rng(0)
    vector = rng.standard_normal(dim).astype("float32")
    vector /= np.linalg.norm(vector)

    print(
        f"table {args.table}: {table.count_rows():,} rows, "
        f"{len(schema_names)} cols, indices={table.list_indices()}",
        flush=True,
    )
    print(f"projection ({len(projection)}): {projection}\n", flush=True)

    size_before = _dir_bytes(lance_dir)
    before = measure(table, projection, vector, args.reps, "no-index")

    print("\n--- creating scalar indexes ---", flush=True)
    built = []
    for col, kind in SCALAR_INDEXES:
        if col not in schema_names:
            print(f"  skip {col}: not in this table", flush=True)
            continue
        t0 = time.perf_counter()
        table.create_scalar_index(col, index_type=kind, replace=True)
        secs = time.perf_counter() - t0
        built.append({"column": col, "type": kind, "build_s": round(secs, 2)})
        print(f"  {col:<20} {kind:<7} built in {secs:6.2f}s", flush=True)
    size_after = _dir_bytes(lance_dir)
    print(
        f"\nindex disk cost: {(size_after - size_before) / 1e6:.1f} MB "
        f"({size_before / 1e6:.0f} -> {size_after / 1e6:.0f} MB)\n",
        flush=True,
    )

    after = measure(table, projection, vector, args.reps, "indexed")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    payload = {
        "measured_at": stamp,
        "rows": table.count_rows(),
        "columns": sorted(schema_names),
        "reps": args.reps,
        "host": subprocess.run(
            ["uname", "-mrs"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "index_disk_bytes": size_after - size_before,
        "indexes": built,
        "no_index": before,
        "indexed": after,
    }
    dest = out / f"{stamp}_scalar-index-bench.json"
    dest.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {dest}", flush=True)

    print("\n=== delta (median ms, no-index -> indexed) ===", flush=True)
    for b, a in zip(before, after):
        print(
            f"{b['filter']:<26} servable={b['indexable_on']:<22} "
            f"count {b['count_only']:>7.1f}->{a['count_only']:>7.1f}  "
            f"browse {b['browse_20']:>7.1f}->{a['browse_20']:>7.1f}  "
            f"page {b['query_page_20']:>7.1f}->{a['query_page_20']:>7.1f}  "
            f"window {b['query_window_2000']:>7.1f}->{a['query_window_2000']:>7.1f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
