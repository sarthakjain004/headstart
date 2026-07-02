"""Tests for the shared search conventions — the filter-then-rank where-clause builder.

``build_filter`` is the one place user input reaches the LanceDB where-clause, so its
validation (reject unknown employment_type rather than interpolate it) is worth locking down.
Pure — no model load — so these run in the standard test env.
"""

from __future__ import annotations

import pytest

from headstart.search import EMPLOYMENT_TYPES, build_filter


def test_no_filters_returns_none():
    assert build_filter() is None


def test_remote_only():
    assert build_filter(remote=True) == "remote = true"


def test_employment_type_must_be_known():
    for value in EMPLOYMENT_TYPES:
        assert build_filter(employment_type=value) == f"employment_type = '{value}'"


def test_unknown_employment_type_rejected():
    with pytest.raises(ValueError):
        build_filter(employment_type="full-time'; DROP TABLE wellfound; --")


def test_max_years_keeps_unknown_experience():
    assert build_filter(max_years=5) == "(min_years <= 5 OR min_years IS NULL)"


def test_filters_combine_with_and():
    clause = build_filter(remote=True, employment_type="contract", max_years=3)
    assert clause == (
        "remote = true AND employment_type = 'contract' "
        "AND (min_years <= 3 OR min_years IS NULL)"
    )
