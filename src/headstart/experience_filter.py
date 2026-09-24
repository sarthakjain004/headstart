"""The experience-ceiling Search filter (``max_years``) and its materialized verdicts (ADR-0193).

Everything the filter restated across modules lives here once: the ceilings the Facet offers and
their labels, the four ``experience_at_most_{N}`` columns, the Python verdict the index writes,
the SQL an old table is migrated with, and the clause :func:`headstart.search_filters.build_filter`
compiles — the flag column where it can, the legacy ``min_years`` comparison where it cannot.
"""

from __future__ import annotations

from collections.abc import Collection

CEILINGS = (0, 2, 5, 10)

#: The Facet's options, as ``(ceiling, label)``. ``max_years`` is a "no more than" filter, so
#: these read as "roles open to someone with N years".
FACET_OPTIONS = tuple(
    (ceiling, "Entry level" if ceiling == 0 else f"{ceiling} years or less")
    for ceiling in CEILINGS
)


def column(ceiling: int) -> str:
    return f"experience_at_most_{ceiling}"


COLUMNS = tuple(column(ceiling) for ceiling in CEILINGS)


def _raw_sql(ceiling: int) -> str:
    """The verdict computed from ``min_years`` itself; unknown experience stays eligible."""
    return f"min_years <= {ceiling} OR min_years IS NULL"


#: The SQL each flag column is computed with on a table that predates it (ADR-0173).
MIGRATION_SQL = {column(ceiling): _raw_sql(ceiling) for ceiling in CEILINGS}


def flags(min_years: int | None) -> dict[str, bool]:
    """The four indexed verdicts; unknown experience remains eligible, as before."""
    return {
        column(ceiling): min_years is None or min_years <= ceiling
        for ceiling in CEILINGS
    }


def has_flags(schema_names: Collection[str]) -> bool:
    """Whether a table carries every flag column — a partial migration uses none of them."""
    return all(name in schema_names for name in COLUMNS)


def clause(max_years: int, materialized: bool) -> str:
    """The where-clause for "no more than ``max_years``"; only offered ceilings have a flag.

    ``materialized`` is :func:`has_flags` of the open table, carried by ``IndexCapabilities``.
    """
    return (
        f"{column(max_years)} = true"
        if materialized and max_years in CEILINGS
        else f"({_raw_sql(max_years)})"
    )
