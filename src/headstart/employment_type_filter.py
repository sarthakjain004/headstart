"""The employment-type Search filter (``etype``) and its materialized flags (ADR-0173, ADR-0193).

Normalizes the ATSes' free-text employment types into filterable flags. The raw value stays
untouched for display. These flags are the exact materialized form of the Search filter's
long-standing substring rules, including the ``intern``/``international`` guard. They exist so a
bitmap index can serve the filter without lowercasing and scanning every row.

Everything the filter restated across modules lives here once: the canonical values and their
Facet labels, the four ``is_*`` columns, the Python verdict the index writes, the SQL an old
table is migrated with, and the clause :func:`headstart.search_filters.build_filter` compiles.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import NamedTuple


class EmploymentTypeRule(NamedTuple):
    column: str
    #: The Facet's label for this canonical value.
    label: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    #: ``(term, veto)`` pairs: ``term`` includes only where ``veto`` is absent.
    includes_unless: tuple[tuple[str, str], ...] = ()

    def matches(self, value: str | None) -> bool:
        text = (value or "").lower()
        included = any(term in text for term in self.includes) or any(
            term in text and veto not in text for term, veto in self.includes_unless
        )
        return included and not any(term in text for term in self.excludes)

    def raw_clause(self, column: str = "employment_type") -> str:
        lowered = f"lower({column})"
        arms = [f"{lowered} LIKE '%{term}%'" for term in self.includes] + [
            f"({lowered} LIKE '%{term}%' AND {lowered} NOT LIKE '%{veto}%')"
            for term, veto in self.includes_unless
        ]
        included = " OR ".join(arms)
        excluded = " AND ".join(
            f"{lowered} NOT LIKE '%{term}%'" for term in self.excludes
        )
        clause = f"({included})" if len(arms) > 1 else included
        if excluded:
            clause = f"({clause} AND {excluded})"
        return clause


RULES = {
    # "permanent" is contract duration, not hours: Recruitee's "parttime_permanent" and
    # Personio's "permanent / part-time" are part-time jobs. Every "permanent" value carrying
    # "part" but not "full" in a 228k-row corpus (2026-07) was one of those, so "part" vetoes it.
    "full-time": EmploymentTypeRule(
        "is_full_time",
        "Full-time",
        ("full",),
        includes_unless=(("permanent", "part"),),
    ),
    "part-time": EmploymentTypeRule("is_part_time", "Part-time", ("part",)),
    "contract": EmploymentTypeRule(
        "is_contract", "Contract", ("contract", "freelance")
    ),
    "internship": EmploymentTypeRule(
        "is_internship", "Internship", ("intern",), ("international",)
    ),
}

COLUMNS = tuple(rule.column for rule in RULES.values())

#: The Facet's options, as ``(canonical value, label)``, in :data:`RULES` order.
FACET_OPTIONS = tuple((value, rule.label) for value, rule in RULES.items())

#: The SQL each flag column is computed with on a table that predates it (ADR-0173).
MIGRATION_SQL = {rule.column: rule.raw_clause() for rule in RULES.values()}


def flags(value: str | None) -> dict[str, bool]:
    """The four served boolean columns for one raw employment-type value."""
    return {rule.column: rule.matches(value) for rule in RULES.values()}


def has_flags(schema_names: Collection[str]) -> bool:
    """Whether a table carries every flag column — a partial migration uses none of them."""
    return all(name in schema_names for name in COLUMNS)


def clause(etype: str | None, materialized: bool) -> str | None:
    """The where-clause for one canonical value, or None for a value the filter does not know.

    ``materialized`` is :func:`has_flags` of the open table, carried by ``IndexCapabilities``.
    """
    rule = RULES.get(etype) if etype else None
    if rule is None:
        return None
    return f"{rule.column} = true" if materialized else rule.raw_clause()
