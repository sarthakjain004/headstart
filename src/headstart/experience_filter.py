"""Materialized verdicts for the experience ceilings the Search facets offer."""

from __future__ import annotations

CEILINGS = (0, 2, 5, 10)


def column(ceiling: int) -> str:
    return f"experience_at_most_{ceiling}"


def flags(min_years: int | None) -> dict[str, bool]:
    """The four indexed verdicts; unknown experience remains eligible, as before."""
    return {
        column(ceiling): min_years is None or min_years <= ceiling
        for ceiling in CEILINGS
    }
