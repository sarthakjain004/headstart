"""How senior a set of Boards' served jobs are: their Trends level bands (ADR-0323).

A company profile used to read seniority off the Search rail's `max_years` facet, whose options
are ceilings — "open to someone with at most 2 years" — where a job stating no experience counts
at every one, so a company's share of senior roles could not be read from it. This counts each
served job once, in the band the Trends Level view puts it in (`trends.role_taxonomy.band`: an
internship by its title or type, else by the served `min_years`: 0–1, 2–4, 5–7, 8+, or not
stated), so a profile's levels read as the site's Level view does.

One scan of the Boards' rows (`location_counts.scoped_rows`), reading the three columns the band
needs. The internship band is beyond the 0–1, 2–4, 5–7, 8+ and not-stated bands the round-2
critique named; it is kept because the critique asked for the Trends level bands, and Trends has
it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from headstart.serving import location_counts
from headstart.trends.role_taxonomy import BAND_LABELS, band

_COLUMNS = ["min_years", "title", "employment_type"]


def bands(table: Any, where: str) -> dict[str, Any]:
    """Every Trends level band's count of the rows ``where`` selects, in the Level view's order,
    with its label. ``jobs`` is how many rows were counted; ``capped`` says the scan reached
    :data:`~headstart.serving.location_counts.MAX_ROWS`."""
    rows = location_counts.scoped_rows(table, where, _COLUMNS)
    counted = Counter(
        band(row.get("min_years"), row.get("title"), row.get("employment_type"))
        for row in rows
    )
    return {
        "jobs": len(rows),
        "capped": len(rows) >= location_counts.MAX_ROWS,
        "bands": [
            {"band": name, "label": label, "count": counted.get(name, 0)}
            for name, label in BAND_LABELS.items()
        ],
    }
