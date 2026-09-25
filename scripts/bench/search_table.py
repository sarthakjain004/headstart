#!/usr/bin/env python3
"""Benchmark the real JobSearch/facet path against one local production-table candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lancedb
import numpy as np

from headstart.serving.job_search import JobSearch

SEARCH_CASES: tuple[tuple[str, dict[str, str]], ...] = (
    ("browse", {}),
    ("browse_ats", {"ats": "greenhouse"}),
    ("browse_seen", {"seen_after": "2026-09-14"}),
    ("browse_posted", {"posted_within": "30", "sort": "posted"}),
    ("semantic", {"q": "backend engineer"}),
    ("semantic_ats", {"q": "backend engineer", "ats": "greenhouse"}),
    ("semantic_remote", {"q": "backend engineer", "remote": "true"}),
    ("semantic_full_time", {"q": "backend engineer", "etype": "full-time"}),
    ("semantic_years", {"q": "backend engineer", "max_years": "3"}),
    (
        "semantic_salary",
        {
            "q": "backend engineer",
            "has_salary": "true",
            "salary_min": "100000",
            "salary_currency": "USD",
        },
    ),
    (
        "semantic_posted",
        {"q": "backend engineer", "posted_within": "30"},
    ),
    ("semantic_country", {"q": "backend engineer", "india": "india"}),
    (
        "semantic_location",
        {"q": "backend engineer", "location": "bangalore"},
    ),
    (
        "semantic_keyword_title",
        {"q": "backend engineer", "kw": "kubernetes", "kw_in": "title"},
    ),
    (
        "semantic_combined",
        {
            "q": "backend engineer",
            "ats": "greenhouse",
            "remote": "true",
            "max_years": "5",
            "seen_after": "2026-09-14",
        },
    ),
)

FACET_CASES: tuple[tuple[str, dict[str, str]], ...] = (
    ("facets_none", {}),
    (
        "facets_combined",
        {
            "ats": "greenhouse",
            "remote": "true",
            "max_years": "5",
            "seen_after": "2026-09-14",
        },
    ),
    ("facets_country", {"india": "india"}),
    ("facets_keyword_title", {"kw": "kubernetes", "kw_in": "title"}),
)


class _FixedEncoder:
    def __init__(self, vector: np.ndarray):
        self.vector = vector.astype("float32")

    def encode(self, _texts: list[str], *, normalize_embeddings: bool) -> np.ndarray:
        assert normalize_embeddings
        return self.vector.reshape(1, -1)


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]


def _measure(fn: Callable[[], Any], warmups: int, reps: int) -> tuple[list[float], Any]:
    for _ in range(warmups):
        fn()
    samples = []
    value = None
    for _ in range(reps):
        started = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples, value


def _load_vector(table: Any, path: str | None) -> np.ndarray:
    if path:
        vector = np.load(path)
    else:
        vector = np.asarray(
            table.search().select(["vector"]).limit(1).to_list()[0]["vector"],
            dtype="float32",
        )
    if vector.shape != (768,):
        raise ValueError(f"expected a 768-vector, got {vector.shape}")
    return vector


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--vector")
    ap.add_argument("--reps", type=int, default=9)
    ap.add_argument("--warmups", type=int, default=2)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    db_path = Path(args.db)
    table = lancedb.connect(db_path).open_table("jobs")
    searcher = JobSearch(_FixedEncoder(_load_vector(table, args.vector)), table)
    bytes_on_disk = sum(p.stat().st_size for p in db_path.rglob("*") if p.is_file())
    header = {
        "type": "header",
        "label": args.label,
        "measured_at": datetime.now(UTC).isoformat(),
        "rows": table.count_rows(),
        "bytes": bytes_on_disk,
        "indices": [
            {"name": i.name, "type": i.index_type, "columns": i.columns}
            for i in table.list_indices()
        ],
        "lancedb": lancedb.__version__,
        "reps": args.reps,
        "warmups": args.warmups,
    }

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as out:
        out.write(json.dumps(header, sort_keys=True) + "\n")
        out.flush()
        print(json.dumps(header, indent=2, sort_keys=True), flush=True)

        for name, params in SEARCH_CASES:
            samples, value = _measure(
                lambda params=params: searcher.run(params), args.warmups, args.reps
            )
            row = {
                "type": "result",
                "case": name,
                "kind": "search",
                "samples_ms": [round(v, 2) for v in samples],
                "median_ms": round(statistics.median(samples), 2),
                "p95_ms": round(_p95(samples), 2),
                "fingerprint": _fingerprint(value),
                "result_count": len(value),
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(
                f"{name:<28} median={row['median_ms']:>8.2f} ms "
                f"p95={row['p95_ms']:>8.2f} ms n={len(value):>3}",
                flush=True,
            )

        for name, params in FACET_CASES:
            samples, value = _measure(
                lambda params=params: searcher.facets(params), args.warmups, args.reps
            )
            row = {
                "type": "result",
                "case": name,
                "kind": "facets",
                "samples_ms": [round(v, 2) for v in samples],
                "median_ms": round(statistics.median(samples), 2),
                "p95_ms": round(_p95(samples), 2),
                "fingerprint": _fingerprint(value),
                "total": value["total"],
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            print(
                f"{name:<28} median={row['median_ms']:>8.2f} ms "
                f"p95={row['p95_ms']:>8.2f} ms total={value['total']:>7}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
