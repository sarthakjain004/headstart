"""Measures India-located rows in the served LanceDB table for taleo_enterprise/taleo_be.

Backing artifact for ADR-0144's Consequences section and docs/code-review/
2026-09-15_last-5-prs-retrospective-critique.md finding 4 — run again with `data/lancedb`
freshly pulled (`scripts/fetch/pull_lancedb.py`) to reproduce or update the numbers.

Matches "India" as a whole word in `location` (word-boundary anchored) rather than a bare
substring: the first pass at this measurement used `"india" in location`, which matched
"Indiana"/"Indianapolis" and produced a false population before this fix.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import lancedb

_INDIA = re.compile(r"(?<![A-Za-z])india(?![A-Za-z])", re.IGNORECASE)
_ATS = ("taleo_enterprise", "taleo_be")


def main() -> None:
    db = lancedb.connect("data/lancedb")
    table = db.open_table("jobs")
    rows_by_ats = {
        ats: table.search()
        .where(f"ats = '{ats}'")
        .select(["id", "company", "location"])
        .limit(50_000)
        .to_list()
        for ats in _ATS
    }
    india_by_ats = {
        ats: [
            {"id": r["id"], "company": r["company"], "location": r["location"]}
            for r in rows
            if r["location"] and _INDIA.search(r["location"])
        ]
        for ats, rows in rows_by_ats.items()
    }
    summary = {
        "served_table_total_rows": table.count_rows(),
        "served_table_version": table.version,
        **{f"{ats}_total_rows": len(rows_by_ats[ats]) for ats in _ATS},
        **{f"{ats}_india_rows": len(india_by_ats[ats]) for ats in _ATS},
        **{
            f"{ats}_india_by_company": dict(
                Counter(r["company"] for r in india_by_ats[ats])
            )
            for ats in _ATS
        },
    }
    print(json.dumps(summary, indent=2))
    out_dir = Path(__file__).parent / "artifacts"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "india_located_rows.json"
    out_path.write_text(
        json.dumps({"summary": summary, **india_by_ats}, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
