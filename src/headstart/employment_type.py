"""Normalize the ATSes' free-text employment types into filterable flags.

The raw value stays untouched for display.  These flags are the exact materialized form of the
Search filter's long-standing substring rules, including the ``intern``/``international`` guard.
They exist so a bitmap index can serve the filter without lowercasing and scanning every row.
"""

from __future__ import annotations

from typing import NamedTuple


class EmploymentTypeFilter(NamedTuple):
    column: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...] = ()

    def matches(self, value: str | None) -> bool:
        text = (value or "").lower()
        return any(term in text for term in self.includes) and not any(
            term in text for term in self.excludes
        )

    def raw_clause(self, column: str = "employment_type") -> str:
        lowered = f"lower({column})"
        included = " OR ".join(f"{lowered} LIKE '%{term}%'" for term in self.includes)
        excluded = " AND ".join(
            f"{lowered} NOT LIKE '%{term}%'" for term in self.excludes
        )
        clause = f"({included})" if len(self.includes) > 1 else included
        if excluded:
            clause = f"({clause} AND {excluded})"
        return clause


FILTERS = {
    "full-time": EmploymentTypeFilter("is_full_time", ("full", "permanent")),
    "part-time": EmploymentTypeFilter("is_part_time", ("part",)),
    "contract": EmploymentTypeFilter("is_contract", ("contract", "freelance")),
    "internship": EmploymentTypeFilter(
        "is_internship", ("intern",), ("international",)
    ),
}

FLAG_COLUMNS = tuple(rule.column for rule in FILTERS.values())


def flags(value: str | None) -> dict[str, bool]:
    """The four served boolean columns for one raw employment-type value."""
    return {rule.column: rule.matches(value) for rule in FILTERS.values()}
