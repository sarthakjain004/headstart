#!/usr/bin/env python3
"""Test whether one materialized filter column earns its schema/storage cost."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import lancedb
import numpy as np
from lancedb.index import Bitmap, BTree, Fm

from headstart import fx
from headstart.employment_type_filter import RULES as EMPLOYMENT_TYPE_RULES
from headstart.search import RESULT_COLUMNS


def _salary_case(column: str) -> str:
    rates = fx.table()["rates"]
    arms = " ".join(
        f"WHEN salary_currency = '{currency}' THEN CAST({column} AS DOUBLE) / {rate!r}"
        for currency, rate in sorted(rates.items())
    )
    return f"CASE {arms} ELSE NULL END"


PROFILES = {
    "experience": {
        "transforms": {
            "filter_min_years": "CAST(coalesce(min_years, 0) AS INT)",
        },
        "indexes": (("filter_min_years", BTree()),),
        "old": "(min_years <= 3 OR min_years IS NULL)",
        "new": "filter_min_years <= 3",
    },
    "full_time": {
        "transforms": {
            "is_full_time": EMPLOYMENT_TYPE_RULES["full-time"].raw_clause(),
        },
        "indexes": (("is_full_time", Bitmap()),),
        "old": EMPLOYMENT_TYPE_RULES["full-time"].raw_clause(),
        "new": "is_full_time = true",
    },
    "title": {
        "transforms": {"title_search": "lower(title)"},
        "indexes": (("title_search", Fm()),),
        "old": "lower(title) LIKE '%kubernetes%'",
        "new": "contains(title_search, 'kubernetes')",
    },
    "location": {
        "transforms": {"location_search": "lower(location)"},
        "indexes": (("location_search", Fm()),),
        "old": "lower(location) LIKE '%bangalore%'",
        "new": "contains(location_search, 'bangalore')",
    },
    "company": {
        "transforms": {"company_search": "lower(company)"},
        "indexes": (("company_search", Fm()),),
        "old": "lower(company) LIKE '%google%'",
        "new": "contains(company_search, 'google')",
    },
    "description": {
        "transforms": {"description_search": "lower(description)"},
        "indexes": (("description_search", Fm()),),
        "old": "lower(description) LIKE '%kubernetes%'",
        "new": "contains(description_search, 'kubernetes')",
    },
    "posted_date": {
        "transforms": {
            "posted_date": (
                "CASE WHEN posted_at LIKE '____-__-__%' "
                "THEN substr(posted_at, 1, 10) ELSE NULL END"
            ),
        },
        "indexes": (("posted_date", BTree()),),
        "old": ("posted_at >= '2026-08-22' AND posted_at LIKE '____-__-__%'"),
        "new": "posted_date >= '2026-08-22'",
    },
    "salary_usd": {
        "transforms": {
            "min_salary_usd": _salary_case("min_salary_annual"),
            "max_salary_usd": _salary_case("max_salary_annual"),
        },
        "indexes": (
            ("min_salary_usd", BTree()),
            ("max_salary_usd", BTree()),
        ),
        "old": None,
        "new": "coalesce(max_salary_usd, min_salary_usd) >= 100000",
    },
}


def _bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _save(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")


def _time(fn, reps: int = 11) -> tuple[list[float], list[dict]]:
    fn()
    fn()
    samples = []
    rows = []
    for _ in range(reps):
        started = time.perf_counter()
        rows = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples, rows


def _fingerprint(rows: list[dict]) -> str:
    ids = sorted(row["id"] for row in rows)
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--vector", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-index", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = lancedb.connect(db_path).open_table("jobs")
    profile = dict(PROFILES[args.profile])
    if args.profile == "salary_usd":
        # The reference clause is produced from the same committed rate table rather than
        # duplicated here; it is stable because this benchmark's date and repo revision are.
        rates = fx.table()["rates"]
        arms = []
        for currency, rate in sorted(rates.items()):
            if currency not in {
                "AED",
                "AUD",
                "CAD",
                "CHF",
                "EUR",
                "GBP",
                "HKD",
                "INR",
                "PLN",
                "SEK",
                "USD",
            }:
                continue
            bound = 100_000 if currency == "USD" else int(100_000 * rate)
            arms.append(
                f"(salary_currency = '{currency}' AND "
                f"coalesce(max_salary_annual, min_salary_annual) >= {bound})"
            )
        profile["old"] = "(" + " OR ".join(arms) + ")"

    before = _bytes(db_path)
    payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "lancedb": lancedb.__version__,
        "profile": args.profile,
        "skip_index": args.skip_index,
        "rows": table.count_rows(),
        "bytes_before": before,
        "status": "materializing",
    }
    _save(dest, payload)
    started = time.perf_counter()
    table.add_columns(profile["transforms"])
    materialize_seconds = time.perf_counter() - started
    builds = []
    for column, config in () if args.skip_index else profile["indexes"]:
        started = time.perf_counter()
        table.create_index(column, config=config, replace=True)
        builds.append(
            {
                "column": column,
                "type": type(config).__name__,
                "seconds": round(time.perf_counter() - started, 3),
            }
        )
    payload.update(
        {
            "status": "measuring",
            "materialize_seconds": round(materialize_seconds, 3),
            "builds": builds,
            "bytes_after": _bytes(db_path),
        }
    )
    _save(dest, payload)

    vector = np.load(args.vector).astype("float32")
    projection = [c for c in RESULT_COLUMNS if c in table.schema.names]

    def query(where: str) -> list[dict]:
        return (
            table.search(vector)
            .metric("cosine")
            .where(where, prefilter=True)
            .select([*projection, "_distance"])
            .limit(20)
            .to_list()
        )

    old_where, new_where = profile["old"], profile["new"]
    old_count = table.count_rows(filter=old_where)
    new_count = table.count_rows(filter=new_where)
    old_samples, old_rows = _time(lambda: query(old_where))
    payload["old"] = {
        "where": old_where,
        "count": old_count,
        "median_ms": round(statistics.median(old_samples), 2),
        "samples_ms": [round(v, 2) for v in old_samples],
        "fingerprint": _fingerprint(old_rows),
    }
    _save(dest, payload)
    new_samples, new_rows = _time(lambda: query(new_where))
    payload["status"] = "complete"
    payload["new"] = {
        "where": new_where,
        "count": new_count,
        "median_ms": round(statistics.median(new_samples), 2),
        "samples_ms": [round(v, 2) for v in new_samples],
        "fingerprint": _fingerprint(new_rows),
    }
    _save(dest, payload)
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    if (
        old_count != new_count
        or payload["old"]["fingerprint"] != payload["new"]["fingerprint"]
    ):
        print("WARNING: candidate changed results", flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
