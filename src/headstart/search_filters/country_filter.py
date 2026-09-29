"""The ``country`` Search filter: one ISO 3166-1 alpha-2 code, matched on ``location`` (ADR-0273).

The places live in :mod:`headstart.search_filters.country_gazetteer`; this module is the filter's
face: the codes it accepts, the labels the page and the MCP tool show, and the clause
:func:`headstart.search_filters.compiler.build_filter` compiles. India is the India filter's
whole-country rule (:func:`headstart.search_filters.india_filter.clause`), materialized column
and all, so ``country=IN`` and ``india=india`` always return the same rows.
"""

from __future__ import annotations

from headstart.search_filters import country_gazetteer, india_filter

INDIA = "IN"

#: Every code the filter accepts, by served Jobs (measured 2026-09-29): India second, after the
#: United States, as the served table counts them.
CODES: tuple[str, ...] = (
    "US",
    INDIA,
    *(code for code in country_gazetteer.COUNTRIES if code != "US"),
)

_NAMES = {code: c.name for code, c in country_gazetteer.COUNTRIES.items()} | {
    INDIA: "India"
}


def name(code: str) -> str:
    """The country's English name, for a label or an answer line."""
    return _NAMES[code]


def options() -> list[tuple[str, str]]:
    """(code, name) pairs in :data:`CODES` order: the page's dropdown and the MCP enum."""
    return [(code, _NAMES[code]) for code in CODES]


def clause(code: str, materialized: bool) -> str | None:
    """The where-clause for one code, or None for a code the filter does not know.

    ``materialized`` is :func:`headstart.search_filters.india_filter.has_column` of the open
    table, which only India's rule reads.
    """
    if code == INDIA:
        return india_filter.clause(india_filter.WHOLE_COUNTRY, materialized)
    return country_gazetteer.where(code)
