#!/usr/bin/env python3
"""Interleave baseline/candidate calls so cache or thermal drift cannot choose the winner."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import lancedb
from search_table import (
    FACET_CASES,
    SEARCH_CASES,
    _fingerprint,
    _FixedEncoder,
    _load_vector,
)

from headstart.search import JobSearch


def _clear_response_caches(searcher: JobSearch) -> None:
    """Measure the table path, not a nanosecond-scale response-cache lookup."""
    with searcher._browse_cache_lock:
        searcher._browse_cache.clear()
    with searcher._facet_cache_lock:
        searcher._facet_cache.clear()


def _one(fn, searcher: JobSearch):
    _clear_response_caches(searcher)
    started = time.perf_counter()
    value = fn()
    return (time.perf_counter() - started) * 1000, value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-db", required=True)
    ap.add_argument("--candidate-db", required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--cycles", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    base_table = lancedb.connect(args.baseline_db).open_table("jobs")
    candidate_table = lancedb.connect(args.candidate_db).open_table("jobs")
    vector = _load_vector(base_table, args.vector)
    baseline = JobSearch(_FixedEncoder(vector), base_table)
    candidate = JobSearch(_FixedEncoder(vector), candidate_table)
    cases = [
        (name, "search", lambda p=p: baseline.run(p), lambda p=p: candidate.run(p))
        for name, p in SEARCH_CASES
    ] + [
        (
            name,
            "facets",
            lambda p=p: baseline.facets(p),
            lambda p=p: candidate.facets(p),
        )
        for name, p in FACET_CASES
    ]
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        for name, kind, fn_a, fn_b in cases:
            fn_a()
            fn_b()
            a, b = [], []
            value_a = value_b = None
            for _ in range(args.cycles):
                ms, value_a = _one(fn_a, baseline)
                a.append(ms)
                ms, value_b = _one(fn_b, candidate)
                b.append(ms)
                ms, value_b = _one(fn_b, candidate)
                b.append(ms)
                ms, value_a = _one(fn_a, baseline)
                a.append(ms)
            row = {
                "case": name,
                "kind": kind,
                "baseline_ms": round(statistics.median(a), 2),
                "candidate_ms": round(statistics.median(b), 2),
                "delta_percent": round(
                    (statistics.median(b) / statistics.median(a) - 1) * 100, 1
                ),
                "same_fingerprint": _fingerprint(value_a) == _fingerprint(value_b),
                "baseline_samples_ms": [round(v, 2) for v in a],
                "candidate_samples_ms": [round(v, 2) for v in b],
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
