"""How senior a set of Boards' served jobs are: their Trends level bands (ADR-0323).

A company profile used to read seniority off the Search rail's `max_years` facet, whose options
are ceilings — "open to someone with at most 2 years" — where a job stating no experience counts
at every one, so a company's share of senior roles could not be read from it. This counts each
served job once, in the band the Trends Level view puts it in (`trends.role_taxonomy.band`: an
internship by its title or type, else by the served `min_years`: 0–1, 2–4, 5–7, 8+, or not
stated), so a profile's levels read as the site's Level view does.

One scan of the Boards' rows, as :mod:`headstart.serving.location_counts` makes, reading the three
columns the band needs.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from headstart.serving.location_counts import MAX_ROWS
from headstart.trends.role_taxonomy import BAND_LABELS, band

_COLUMNS = ["min_years", "title", "employment_type"]


def bands(table: Any, where: str) -> dict[str, Any]:
    """Every Trends level band's count of the rows ``where`` selects, in the Level view's order,
    with its label. ``jobs`` is how many rows were counted; ``capped`` says the scan reached
    :data:`~headstart.serving.location_counts.MAX_ROWS`."""
    rows = (
        table.search()
        .where(where, prefilter=True)
        .select(_COLUMNS)
        .limit(MAX_ROWS)
        .to_list()
    )
    counted = Counter(
        band(row.get("min_years"), row.get("title"), row.get("employment_type"))
        for row in rows
    )
    return {
        "jobs": len(rows),
        "capped": len(rows) >= MAX_ROWS,
        "bands": [
            {"band": name, "label": label, "count": counted.get(name, 0)}
            for name, label in BAND_LABELS.items()
        ],
    }
