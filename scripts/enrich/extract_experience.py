"""Run years-of-experience extraction over the Wellfound jobs and report coverage (Tiers 1+2).

Reads ``data/jobs/wellfound.csv``, runs ``headstart.experience.extract(field, description)`` per Job,
writes the results to ``data/enrich/wellfound_experience.jsonl`` (``id`` -> min/max/source), and
prints coverage: how many Jobs got a number, split by source (structured field vs description regex),
plus sample snippets to eyeball regex quality.
"""

from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path

from headstart.experience import extract

_ROOT = Path(__file__).resolve().parents[2]
_INPUT = _ROOT / "data" / "jobs" / "wellfound.csv"
_OUT = _ROOT / "data" / "enrich" / "wellfound_experience.jsonl"
_SNIPPET = re.compile(r".{0,18}\d{1,2}\s*\+?\s*years?.{0,28}", re.I)  # for eyeballing matches only


def main() -> None:
    csv.field_size_limit(10**8)
    rows = list(csv.DictReader(_INPUT.open(newline="", encoding="utf-8")))
    _OUT.parent.mkdir(parents=True, exist_ok=True)

    by_source: collections.Counter[str] = collections.Counter()
    mins: list[int] = []
    samples: list[str] = []
    field_empty = field_empty_recovered = 0

    with _OUT.open("w", encoding="utf-8") as out:
        for row in rows:
            field = (row.get("years_experience") or "").strip() or None
            description = row.get("description") or ""
            if field is None:
                field_empty += 1
            span = extract(field, description)
            if not span:
                continue
            by_source[span.source] += 1
            mins.append(span.min_years)
            if span.source == "regex":
                if field is None:
                    field_empty_recovered += 1
                if len(samples) < 12:
                    snip = _SNIPPET.search(description)
                    samples.append(f"{span.min_years}-{span.max_years}  «{(snip.group() if snip else '').strip()}»")
            out.write(json.dumps({"id": row["id"], "min_years": span.min_years,
                                  "max_years": span.max_years, "source": span.source}) + "\n")

    n = len(rows)
    covered = sum(by_source.values())
    print(f"jobs: {n}")
    print(f"got a number: {covered} ({100 * covered / n:.1f}%)  |  none: {n - covered} ({100 * (n - covered) / n:.1f}%)")
    print(f"  field (Tier 1): {by_source['field']} ({100 * by_source['field'] / n:.1f}%)")
    print(f"  regex (Tier 2): {by_source['regex']} ({100 * by_source['regex'] / n:.1f}%)")
    print(f"  of {field_empty} field-empty jobs, regex recovered {field_empty_recovered} "
          f"({100 * field_empty_recovered / max(field_empty, 1):.0f}%)")
    print(f"min_years distribution: {sorted(collections.Counter(mins).items())}")
    print("\nregex match samples (eyeball for false positives):")
    for s in samples:
        print("  ", s)
    print(f"\nwrote {covered} records -> {_OUT}")


if __name__ == "__main__":
    main()
