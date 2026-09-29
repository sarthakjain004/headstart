"""The employment-type Search filter (``etype``) and its materialized flags (ADR-0173, ADR-0193).

Normalizes the ATSes' free-text employment types into filterable flags. The raw value stays
untouched for display. These flags are the exact materialized form of the Search filter's
long-standing substring rules, including the ``intern``/``international`` guard. They exist so a
bitmap index can serve the filter without lowercasing and scanning every row.

Everything the filter restated across modules lives here once: the canonical values and their
Facet labels, the four ``is_*`` columns, the Python verdict the index writes, the SQL an old
table is migrated with, and the clause :func:`headstart.search_filters.compiler.build_filter` compiles.
"""

from __future__ import annotations

import re
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
    #: Whole values (lowercased, not trimmed: Lance SQL has no `trim`) that count on their own. For codes too short to be a
    #: substring: "f" or "ft" would match "soft", "left" and "effort".
    equals: tuple[str, ...] = ()
    #: Words in the *title* that count when the raw value says nothing of the kind. Read only by
    #: the materialized flag: the SQL fallback for a table without the columns cannot pattern-match
    #: a title, so it keeps the raw-value clause.
    title_pattern: re.Pattern[str] | None = None

    def matches(self, value: str | None, title: str | None = None) -> bool:
        text = (value or "").lower()
        included = (
            any(term in text for term in self.includes)
            or any(
                term in text and veto not in text for term, veto in self.includes_unless
            )
            or text in self.equals
        )
        if included and not any(term in text for term in self.excludes):
            return True
        return bool(self.title_pattern and title and self.title_pattern.search(title))

    def raw_clause(self, column: str = "employment_type") -> str:
        lowered = f"lower({column})"
        arms = (
            [f"{lowered} LIKE '%{term}%'" for term in self.includes]
            + [
                f"({lowered} LIKE '%{term}%' AND {lowered} NOT LIKE '%{veto}%')"
                for term, veto in self.includes_unless
            ]
            + (
                [f"{lowered} IN ({', '.join(repr(v) for v in self.equals)})"]
                if self.equals
                else []
            )
        )
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
    #
    # Measured on the served table 2026-09-29: 10,790 rows held a value that read as full time
    # and set no flag. "regular" is Radancy's, TikTok's and ByteDance's word for it (4,910 rows,
    # "Regular Part-time" is vetoed like "permanent"), "salaried_ft"/"hourly_ft" Rippling's (2,902),
    # "F" Applied Materials' (1,009), and "CDI" (French permanent contract), "Tiempo completo" and
    # "全职" follow the "permanent" convention. Their descriptions say "part-time" as rarely as
    # stated full-time rows' do (0.0-0.2% against 0.8%).
    "full-time": EmploymentTypeRule(
        "is_full_time",
        "Full-time",
        ("full",),
        includes_unless=(("permanent", "part"), ("regular", "part")),
        equals=(
            "salaried_ft",
            "hourly_ft",
            "f",
            "ft",
            "fte",
            "cdi",
            "tiempo completo",
            "全职",
        ),
    ),
    "part-time": EmploymentTypeRule(
        "is_part_time", "Part-time", ("part",), equals=("salaried_pt", "hourly_pt")
    ),
    # Temporary and fixed-term jobs are time-limited like contracts. The two kinds stack with
    # hours: "fulltime_fixed_term" is full-time and contract.
    "contract": EmploymentTypeRule(
        "is_contract", "Contract", ("contract", "freelance", "temporary", "fixed")
    ),
    # The title says "Intern" where Workday's timeType says "Full time" and Greenhouse says
    # nothing: 9,354 of 11,993 intern-titled rows were unflagged (2026-09-29). A whole word, so
    # "International", "Internal" and "Internet" never match; 40 of 40 unflagged titles read
    # were real internships, and "Internship Program" titles are the postings themselves.
    "internship": EmploymentTypeRule(
        "is_internship",
        "Internship",
        ("intern",),
        ("international",),
        title_pattern=re.compile(r"\bintern(?:ship)?s?\b", re.IGNORECASE),
    ),
}

COLUMNS = tuple(rule.column for rule in RULES.values())

#: The Facet's options, as ``(canonical value, label)``, in :data:`RULES` order.
FACET_OPTIONS = tuple((value, rule.label) for value, rule in RULES.items())

#: The SQL each flag column is computed with on a table that predates it (ADR-0173).
MIGRATION_SQL = {rule.column: rule.raw_clause() for rule in RULES.values()}


def flags(value: str | None, title: str | None = None) -> dict[str, bool]:
    """The four served boolean columns for one raw employment-type value and the Job's title."""
    return {rule.column: rule.matches(value, title) for rule in RULES.values()}


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
