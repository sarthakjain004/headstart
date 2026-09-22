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


FILTERS = {
    # "permanent" is contract duration, not hours: Recruitee's "parttime_permanent" and
    # Personio's "permanent / part-time" are part-time jobs. Every "permanent" value carrying
    # "part" but not "full" in a 228k-row corpus (2026-07) was one of those, so "part" vetoes it.
    "full-time": EmploymentTypeFilter(
        "is_full_time", ("full",), includes_unless=(("permanent", "part"),)
    ),
    "part-time": EmploymentTypeFilter("is_part_time", ("part",)),
    "contract": EmploymentTypeFilter("is_contract", ("contract", "freelance")),
    "internship": EmploymentTypeFilter(
        "is_internship", ("intern",), ("international",)
    ),
}


def flags(value: str | None) -> dict[str, bool]:
    """The four served boolean columns for one raw employment-type value."""
    return {rule.column: rule.matches(value) for rule in FILTERS.values()}
