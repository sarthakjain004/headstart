"""The "include non-tech roles" Search switch (``include_non_tech``) and the column that serves it
(``is_confident_non_tech``, ADR-0349, ADR-0193).

The Tech filter (ADR-0017) is recall-biased on purpose, so about one served Job in four is not a tech
role. The role-family head (ADR-0220, ADR-0224) reads each row's title and description vector on
every Trends tick; where it calls a row ``non-tech`` with a top probability of at least
:data:`PROBABILITY`, the tick stamps ``is_confident_non_tech`` on it, and Search leaves those rows
out unless the caller asks for them. The rows stay in the table, in Trends (which counts non-tech
apart) and behind ``get_job``; only Search, Browse and the counts read the column.

Like every materialized Search filter the module holds the column name, the verdict, the SQL an old
table is migrated with and the clause :func:`headstart.search_filters.compiler.build_filter`
compiles. Unlike the others its verdict is not derived from the row's own meta: the tick decides it
(:mod:`headstart.ingest.confident_non_tech_stamp` writes it), so a new row is *not* confident
until the tick that follows it in the same run says so, and a table from before the column has no
filter at all.
"""

from __future__ import annotations

from collections.abc import Collection

from headstart.trends.role_taxonomy import NON_TECH

COLUMN = "is_confident_non_tech"

#: The head's top probability a ``non-tech`` call needs to hide a row. 0.9 hides 59,523 of 500,167
#: rows (11.9%) on the 2026-09-29 served table. Of 101 of them read, 94 were clearly non-tech, none
#: tech and 7 borderline; of 60 read again, 53, none and 7 (ADR-0349). At 0.5 it would hide 115,075
#: rows, at 0.99 only 11,100.
PROBABILITY = 0.9

#: The classifier head's version (``config/role_family_classifier/manifest.json``) whose
#: probabilities :data:`PROBABILITY` was measured on. A new head is calibrated differently, so a
#: test fails on its version bump until the threshold is measured again.
MEASURED_ON_HEAD_VERSION = 3

#: The SQL the column is computed with on a table that predates it (ADR-0173): every row visible,
#: until the next tick stamps the confident ones.
MIGRATION_SQL = {COLUMN: "false"}


def is_confident(family: str, probability: float) -> bool:
    """Whether the head's call for a row (its family and top probability) hides it."""
    return family == NON_TECH and probability >= PROBABILITY


def flags() -> dict[str, bool]:
    """The indexed verdict a row is added with: visible. The tick stamps the confident ones."""
    return {COLUMN: False}


def has_flags(schema_names: Collection[str]) -> bool:
    return COLUMN in schema_names


def clause(include_non_tech: bool, materialized: bool) -> str | None:
    """The where-clause hiding confident non-tech rows, or None when it hides nothing: the caller
    asked for them, or the table has no column to read (``materialized`` is :func:`has_flags` of
    the open table, carried by ``IndexCapabilities``).

    A row with no verdict (NULL) is kept: nothing is hidden on no evidence."""
    if include_non_tech or not materialized:
        return None
    return f"({COLUMN} IS NULL OR {COLUMN} = false)"
