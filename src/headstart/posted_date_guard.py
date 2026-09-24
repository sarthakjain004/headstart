"""The shape guard every posting-date Search filter carries, and its materialized flag (ADR-0193).

``posted_at`` is a raw string the ATSes write, and the 3% that are not ISO (darwinbox's legacy
``21-Apr-2026``) sort lexicographically above any ISO cutoff. So every clause keyed on it — the
``posted_within`` window, the ``posted_after``/``posted_before`` range and the posted-date sort —
ANDs in the guard ``posted_at LIKE '____-__-__%'``. ADR-0173 materialized that verdict as
``posted_at_comparable``. The column, the Python verdict the index writes, the SQL an old table is
migrated with, and the guard :func:`headstart.search.build_filter` compiles live here; the date
clauses themselves stay with the compiler, which shares their date arithmetic with ``first_seen``.
It is a guard rather than a filter, which is why the module is not called ``posted_date_filter``.
"""

from __future__ import annotations

from collections.abc import Collection

COLUMN = "posted_at_comparable"

#: The SQL the flag column is computed with on a table that predates it (ADR-0173).
MIGRATION_SQL = {COLUMN: "posted_at LIKE '____-__-__%'"}


def is_comparable(posted_at: str | None) -> bool:
    """Whether the raw date matches Lance's legacy ``LIKE '____-__-__%'`` guard."""
    return bool(
        posted_at
        and len(posted_at) >= 10
        and posted_at[4] == "-"
        and posted_at[7] == "-"
    )


def flags(posted_at: str | None) -> dict[str, bool]:
    """The indexed verdict for one row's raw ``posted_at``."""
    return {COLUMN: is_comparable(posted_at)}


def has_flags(schema_names: Collection[str]) -> bool:
    return COLUMN in schema_names


def clause(materialized: bool) -> str:
    """The guard itself, unparenthesized — each caller wraps it with its own comparison.

    ``materialized`` is :func:`has_flags` of the open table, carried by ``IndexCapabilities``.
    """
    return f"{COLUMN} = true" if materialized else MIGRATION_SQL[COLUMN]
