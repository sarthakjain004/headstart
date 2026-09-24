"""The "shows salary" Search filter (``has_salary``) and its materialized flag (ADR-0173, ADR-0193).

``salary_known`` mirrors ``min_salary_annual IS NOT NULL`` — the reconciled ADR-0082 salary, Tier 1
or Tier 2 — so a bitmap index can answer the switch without a null scan. The column, the Python
verdict the index writes, the SQL an old table is migrated with, and the clause
:func:`headstart.search.build_filter` and the Data tab's coverage count compile all live here.
The salary *bracket* is not materialized and stays with the compiler.
"""

from __future__ import annotations

from collections.abc import Collection

COLUMN = "salary_known"

#: The SQL the flag column is computed with on a table that predates it (ADR-0173).
MIGRATION_SQL = {COLUMN: "min_salary_annual IS NOT NULL"}


def flags(min_salary_annual: int | None) -> dict[str, bool]:
    """The indexed verdict for one row's reconciled annual salary floor."""
    return {COLUMN: min_salary_annual is not None}


def has_flags(schema_names: Collection[str]) -> bool:
    return COLUMN in schema_names


def clause(materialized: bool) -> str:
    """The where-clause for "carries a salary".

    ``materialized`` is :func:`has_flags` of the open table, carried by ``IndexCapabilities``.
    """
    return f"{COLUMN} = true" if materialized else MIGRATION_SQL[COLUMN]
