"""Where a set of Boards' served jobs are: the `location` values they carry most (ADR-0275).

The Search rail has no location facet, and :mod:`headstart.serving.facets` cannot give one: it
counts a filter's fixed options, and `location` is free text that each employer writes, with no
option list. A company profile asks something narrower — which few places one company's own jobs
name most often — and that is cheap because it is scoped to the company's Boards: one
single-column scan of their rows. Measured through :func:`top` on a local 514,163-row snapshot of
the served table (2026-09-29, third of three runs): Amazon's 9,651 rows in 28 ms, Deloitte South
Asia's 884 in 21 ms, Stripe's 221 in 21 ms.

A location is the served string, with its whitespace collapsed; nothing else is merged, so
"Seattle, WA" and "Seattle, Washington, USA" are two places, as the employers wrote them. Merging
them needs a place gazetteer beyond India's (`search_filters.india_gazetteer`).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

#: The most rows one answer reads. A company's Boards are bounded (`job_search.MAX_SCOPED_BOARDS`)
#: and the largest measured is Amazon's 9,651 rows, so this is five of those; past it the counts
#: cover the first rows read, and ``capped`` says so.
MAX_ROWS = 50_000


def top(table: Any, where: str, limit: int) -> dict[str, Any]:
    """The ``limit`` locations the rows ``where`` selects carry most, most first, ties by name.

    ``jobs`` is how many rows were counted, ``unstated`` how many of them name no location, and
    ``distinct`` how many different locations they name."""
    rows = (
        table.search()
        .where(where, prefilter=True)
        .select(["location"])
        .limit(MAX_ROWS)
        .to_list()
    )
    counted: Counter[str] = Counter()
    for row in rows:
        if place := " ".join(str(row.get("location") or "").split()):
            counted[place] += 1
    ranked = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return {
        "jobs": len(rows),
        "unstated": len(rows) - sum(counted.values()),
        "distinct": len(counted),
        "capped": len(rows) >= MAX_ROWS,
        "locations": [
            {"location": place, "count": count} for place, count in ranked[:limit]
        ],
    }
