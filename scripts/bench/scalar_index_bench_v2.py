#!/usr/bin/env python3
"""v2: high-fidelity scalar-index bench, built to survive an adversarial review of v1.

Three corrections over ``scalar_index_bench.py``:

1. **Real filter strings.** Every clause is produced by :func:`headstart.search.build_filter`
   itself, not hand-written SQL — so ``max_years`` compiles to its actual
   ``(min_years <= N OR min_years IS NULL)`` shape, not the bare ``<=`` v1 used.
2. **ABAB, not AB.** v1 measured every no-index case, then built indexes, then measured every
   indexed case — so warm-cache drift over the run's lifetime is confounded with the index
   effect. This does no-index / indexed / no-index / indexed for each filter and reports both
   deltas, so a monotonic drift (not caused by the index) shows up as two *different* deltas
   rather than one number that looks clean.
3. **Reports the control.** The unfiltered case is measured the same ABAB way; if it moves as
   much as an indexed case, that case's "win" is cache noise, not the index.

    python -m scripts.bench.scalar_index_bench_v2 --db data/lancedb --table jobs
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb
import numpy as np

from headstart.search import RESULT_COLUMNS, build_filter

# name -> build_filter() kwargs, chosen to mirror real UI filter combinations
FILTER_CASES: list[tuple[str, dict]] = [
    ("none", {}),
    ("remote", {"remote": True}),
    ("ats=greenhouse", {"ats": "greenhouse"}),
    ("max_years=3 (real OR-IS-NULL shape)", {"max_years": 3}),
    (
        "salary_min=100k USD",
        {"has_salary": True, "salary_min": 100_000, "salary_currency": "USD"},
    ),
    (
        "combined(ats+remote+years)",
        {"ats": "greenhouse", "remote": True, "max_years": 3},
    ),
    ("posted_within=30d", {"posted_within": 30, "posted_sortable": True}),
    ("etype=full-time (LIKE, control)", {"etype": "full-time"}),
    ("location LIKE bangalore (LIKE, control)", {"location": "bangalore"}),
    # ADR-0120: the country-level case is what the materialized `country` column targets —
    # `has_country` (below) picks the fast `country = 'IN'` path when the table carries it,
    # falling back to `geo.where("india")`'s regex alternation otherwise. The city-level case is
    # the control proving the scoping is right: it should cost the same either way, since only
    # the country-level alternation was ever measured as expensive.
    ("india=india (country-level)", {"india": "india"}),
    ("india=bengaluru (city-level, control)", {"india": "bengaluru"}),
]

SCALAR_INDEXES: list[tuple[str, str]] = [
    ("ats", "BITMAP"),
    ("remote", "BITMAP"),
    ("min_salary_annual", "BTREE"),
    ("posted_at", "BTREE"),
    ("first_seen", "BTREE"),
    (
        "min_years",
        "BTREE",
    ),  # kept in v2 deliberately — v1 flagged it as a regression; re-test
    # `country` (ADR-0120) is deliberately absent: that change ships with no index, so its case
    # above is measured as a plain unindexed equality scan in every pass.
]


def _median_ms(fn, reps: int, warmup: int = 2) -> float:
    for _ in range(warmup):
        fn()
    return statistics.median([_time(fn) for _ in range(reps)])


def _time(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def make_query_fn(table, projection, vector, where: str | None):
    def fn():
        s = table.search(vector).metric("cosine")
        if where:
            s = s.where(where, prefilter=True)
        return s.select([*projection, "_distance"]).limit(20).to_list()

    return fn


def measure_pass(
    table,
    projection,
    vector,
    atses,
    currencies,
    has_fs,
    has_msa,
    has_country,
    reps,
    tag,
):
    rows = []
    for label, kwargs in FILTER_CASES:
        where = build_filter(
            atses=atses,
            currencies=currencies,
            has_first_seen=has_fs,
            has_min_salary_annual=has_msa,
            has_country=has_country,
            **kwargs,
        )
        n = table.count_rows(filter=where) if where else table.count_rows()
        ms = _median_ms(make_query_fn(table, projection, vector, where), reps)
        rows.append(
            {"filter": label, "where": where, "matched": n, "page_ms": round(ms, 1)}
        )
        print(
            f"[{tag}] {label:<40} n={n:>7}  page={ms:>7.1f} ms  where={where}",
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

    db = lancedb.connect(args.db)
    table = db.open_table(args.table)
    schema_names = set(table.schema.names)
    projection = [c for c in RESULT_COLUMNS if c in schema_names]

    atses = sorted(
        {r["ats"] for r in table.search().select(["ats"]).limit(1_000_000).to_list()}
    )
    currencies = sorted(
        {
            r["salary_currency"]
            for r in table.search()
            .select(["salary_currency"])
            .limit(1_000_000)
            .to_list()
            if r["salary_currency"]
        }
    )
    has_fs = "first_seen" in schema_names
    has_msa = "min_salary_annual" in schema_names
    has_country = "country" in schema_names

    rng = np.random.default_rng(0)
    vector = rng.standard_normal(768).astype("float32")
    vector /= np.linalg.norm(vector)

    print(
        f"table: {table.count_rows():,} rows, indices={table.list_indices()}",
        flush=True,
    )
    print(f"atses={atses}\ncurrencies={currencies}\n", flush=True)

    pass_a1 = measure_pass(
        table,
        projection,
        vector,
        atses,
        currencies,
        has_fs,
        has_msa,
        has_country,
        args.reps,
        "A1 no-index",
    )

    print("\n--- building indexes ---", flush=True)
    for col, kind in SCALAR_INDEXES:
        table.create_scalar_index(col, index_type=kind, replace=True)
        print(f"  {col} ({kind}) built", flush=True)

    pass_b1 = measure_pass(
        table,
        projection,
        vector,
        atses,
        currencies,
        has_fs,
        has_msa,
        has_country,
        args.reps,
        "B1 indexed",
    )

    for i in list(table.list_indices()):
        table.drop_index(i.name)
    print("\n--- indexes dropped ---", flush=True)

    pass_a2 = measure_pass(
        table,
        projection,
        vector,
        atses,
        currencies,
        has_fs,
        has_msa,
        has_country,
        args.reps,
        "A2 no-index",
    )

    for col, kind in SCALAR_INDEXES:
        table.create_scalar_index(col, index_type=kind, replace=True)
    pass_b2 = measure_pass(
        table,
        projection,
        vector,
        atses,
        currencies,
        has_fs,
        has_msa,
        has_country,
        args.reps,
        "B2 indexed",
    )

    for i in list(table.list_indices()):
        table.drop_index(i.name)
    print("\n--- final cleanup: all indexes dropped ---", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    payload = {
        "measured_at": stamp,
        "rows": table.count_rows(),
        "reps": args.reps,
        "A1_no_index": pass_a1,
        "B1_indexed": pass_b1,
        "A2_no_index": pass_a2,
        "B2_indexed": pass_b2,
    }
    dest = out / f"{stamp}_scalar-index-bench-v2-abab.json"
    dest.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {dest}", flush=True)

    print("\n=== ABAB summary (page ms) ===", flush=True)
    print(
        f"{'filter':<40} {'A1':>7} {'B1':>7} {'A2':>7} {'B2':>7}   drift(A2-A1) effect(B-A avg)",
        flush=True,
    )
    for r1, r2, r3, r4 in zip(pass_a1, pass_b1, pass_a2, pass_b2):
        a1, b1, a2, b2 = r1["page_ms"], r2["page_ms"], r3["page_ms"], r4["page_ms"]
        drift = a2 - a1
        effect = ((b1 - a1) + (b2 - a2)) / 2
        print(
            f"{r1['filter']:<40} {a1:>7.1f} {b1:>7.1f} {a2:>7.1f} {b2:>7.1f}   "
            f"drift={drift:>+6.1f}  effect={effect:>+7.1f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
