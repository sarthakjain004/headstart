"""Tests for the Search tab's facet counts (headstart.facets, issue #275).

Contracts: a facet's own constraint is lifted before its options are counted, so the numbers
answer "what would I get if I switched" rather than repeating the current total; every other
filter stays applied; the counts never touch the encoder, because a vector search ranks the
filtered set rather than shrinking it; and when nothing matched, `blocking` names the one
filter actually responsible instead of leaving the user to guess.
"""

from __future__ import annotations

import pytest

from headstart import facets
from headstart.search import build_filter


class _CountingTable:
    """A table that answers `count_rows` from a rule, and records every clause it was asked.

    Counting real rows is `test_search.py`'s job; what matters here is *which where-clause*
    each option was counted with, since that is the whole contract.
    """

    def __init__(self, rule=None):
        self.seen: list[str | None] = []
        # a filtered count answers 42 and the unfiltered one 100, so a test can tell the
        # two apart without every filtered option collapsing to a falsy zero
        self._rule = rule or (lambda where: 100 if where is None else 42)

    def count_rows(
        self, filter=None
    ):  # lancedb's own parameter name, shadowing built-in
        self.seen.append(filter)
        return self._rule(filter)


def _kwargs(**overrides):
    base = {
        "remote": False,
        "max_years": None,
        "ats": None,
        "etype": None,
        "india": None,
        "location": None,
        "company": None,
        "has_salary": False,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "posted_within": None,
        "posted_sortable": False,
        "seen_within": None,
        "posted_after": None,
        "posted_before": None,
        "seen_after": None,
        "seen_before": None,
        "first_seen_after": None,
        "kw": None,
        "kw_in": None,
        "has_description": True,
        "atses": ["greenhouse", "lever"],
        "currencies": ["USD"],
        "has_first_seen": True,
        "has_min_salary_annual": True,
    }
    return {**base, **overrides}


def test_every_option_issue_275_asked_for_is_offered():
    # The issue named these explicitly: "In last seen by headstart add options for last 4 hrs,
    # 6hrs, 8hrs, 12 hrs, 18 hrs as well."
    assert {4, 6, 8, 12, 18} <= set(facets.SEEN_HOURS)


def test_a_facet_lifts_its_own_constraint_before_counting_its_options():
    """The counting rule, and the one that makes the numbers worth showing.

    With `seen_within=2` already applied, the 24-hour option must be counted as if the user
    had picked 24 — not intersected with the 2-hour window they currently have, which would
    report a number smaller than the option can ever deliver and make every longer window
    look useless.
    """
    table = _CountingTable()
    out = facets.counts(table, _kwargs(seen_within=2))
    day = next(o for o in out["facets"]["seen_within"] if o["value"] == 24)
    assert day["count"] == 42  # counted on its own terms, not intersected away
    # exactly one first_seen clause per option: the current 2h window was replaced, not ANDed
    for clause in table.seen:
        assert (clause or "").count("first_seen >=") <= 1


def test_other_filters_stay_applied_while_one_dimension_varies():
    table = _CountingTable()
    facets.counts(table, _kwargs(remote=True, seen_within=2))
    ats_clauses = [c for c in table.seen if c and "ats = " in c]
    assert ats_clauses  # the ATS strip was counted
    assert all("remote = true" in c for c in ats_clauses)  # ...with `remote` still on


def test_counts_never_need_the_query_or_the_encoder():
    """A vector search ranks the filtered set rather than shrinking it, so a count is decided
    by the where-clause alone — which is why the request's query never reaches this module at
    all. The signature is the guarantee: there is nowhere to pass one."""
    import inspect

    assert list(inspect.signature(facets.counts).parameters) == [
        "table",
        "filter_kwargs",
    ]
    a, b = _CountingTable(), _CountingTable()
    without = facets.counts(a, _kwargs())
    withq = facets.counts(b, _kwargs())
    assert without == withq

    # Same clauses, compared as a set: the counts go out on a thread pool, so the order they
    # arrive in is not part of the contract — only that the query changed none of them. The
    # `first_seen` windows are `now`-relative and differ between the two calls, so they are
    # compared by shape rather than by value.
    def shape(seen):
        return sorted((c or "").split(" >= ")[0] for c in seen)

    assert a.seen and shape(a.seen) == shape(b.seen)


def test_the_total_is_the_current_filters_unchanged():
    table = _CountingTable(lambda where: 7 if where == "remote = true" else 0)
    out = facets.counts(table, _kwargs(remote=True))
    assert out["total"] == 7


def test_blocking_is_none_while_anything_matched():
    out = facets.counts(_CountingTable(), _kwargs(remote=True))
    assert out["total"] == 42  # something matched...
    assert out["blocking"] is None  # ...so there is nothing to blame


def test_blocking_names_the_filter_that_recovers_the_most():
    """The "why did I get nothing" answer. Only the company filter is ruinous here, so that is
    the one to name — telling the user to loosen `remote` would send them after the wrong one.
    """

    def rule(where):
        if where is None:
            return 5000
        if "company" in where:
            return 0
        return 900 if "remote" in where else 5000

    out = facets.counts(_CountingTable(rule), _kwargs(remote=True, company="nope"))
    assert out["total"] == 0
    assert out["blocking"] == "company"


def test_blocking_stays_silent_when_no_single_filter_is_to_blame():
    # Nothing matched even with every filter dropped, so naming one would be a lie.
    out = facets.counts(_CountingTable(lambda where: 0), _kwargs(remote=True))
    assert out["total"] == 0
    assert out["blocking"] is None


def test_entry_level_can_be_named_as_the_blocker():
    """`max_years=0` is a real constraint, but `0 == False` in Python — a membership test for
    unset values silently calls it inactive, and the Entry-level filter could then never be
    reported as the one ruling everything out."""

    def rule(where):
        return 0 if where and "min_years <= 0" in where else 4000

    out = facets.counts(_CountingTable(rule), _kwargs(max_years=0))
    assert out["total"] == 0
    assert out["blocking"] == "max_years"


def test_the_runtime_facts_of_the_index_are_never_offered_as_droppable():
    def rule(where):
        return 0 if where else 10

    out = facets.counts(_CountingTable(rule), _kwargs(remote=True))
    assert out["blocking"] not in (
        "atses",
        "currencies",
        "has_first_seen",
        "has_min_salary_annual",
    )


def test_the_salary_facet_stays_dark_without_the_salary_columns():
    out = facets.counts(_CountingTable(), _kwargs(has_min_salary_annual=False))
    assert "has_salary" not in out["facets"]


# ---- the Keyword filter's disclaimer (ADR-0104) ----


def test_description_coverage_is_counted_with_the_keyword_lifted():
    table = _CountingTable()
    out = facets.counts(table, _kwargs(remote=True, kw="rust", kw_in="description"))
    # Both numbers come from the other filters alone — the keyword lifted, as a dimension's "Any"
    # row lifts its own. Counted with it intact, a description-scoped keyword makes the covered
    # set and the total the same clause, and the disclaimer reads "N of N".
    unkeyed = build_filter(**_kwargs(remote=True))
    assert unkeyed in table.seen
    assert f"({unkeyed}) AND description IS NOT NULL" in table.seen
    # ...and no coverage count carries the keyword. Only `description IS NOT NULL`: the salary
    # facet's own `min_salary_annual IS NOT NULL` option keeps the keyword, as every facet does.
    assert not any(
        w and "rust" in w and "description IS NOT NULL" in w for w in table.seen
    )
    assert out["description_coverage"] == {"covered": 42, "total": 42}


def test_description_coverage_is_null_not_zero_without_the_column():
    table = _CountingTable()
    out = facets.counts(table, _kwargs(has_description=False))
    assert out["description_coverage"] is None
    assert not any(w and "description IS NOT NULL" in w for w in table.seen)


def test_description_coverage_with_no_filters_is_a_bare_not_null_count():
    table = _CountingTable()
    facets.counts(table, _kwargs())
    assert "description IS NOT NULL" in table.seen


def test_a_keyword_can_be_named_as_the_blocker_but_its_scope_never_can():
    # Everything matches until the keyword is applied; dropping it recovers 100.
    def rule(where):
        return 0 if where and "LIKE '%rust%'" in where else 100

    out = facets.counts(_CountingTable(rule), _kwargs(kw="rust", kw_in="title"))
    assert out["total"] == 0
    assert out["blocking"] == "kw"


def test_every_option_carries_what_the_ui_needs_to_draw_it():
    out = facets.counts(_CountingTable(), _kwargs())
    for options in out["facets"].values():
        for option in options:
            assert set(option) == {"value", "label", "count"}
            assert isinstance(option["count"], int)
            assert option["label"]


@pytest.mark.parametrize(
    ("hours", "label"),
    [
        (2, "Last 2 hours"),
        (18, "Last 18 hours"),
        (24, "Last 24 hours"),
        (168, "Last 7 days"),
    ],
)
def test_windows_read_in_the_unit_a_person_thinks_in(hours, label):
    out = facets.counts(_CountingTable(), _kwargs())
    assert (
        next(o for o in out["facets"]["seen_within"] if o["value"] == hours)["label"]
        == label
    )


def test_counts_compile_the_same_clauses_the_search_would():
    """The count and the list it counts must never describe different queries — which is why
    both go through `build_filter` on the same parsed kwargs."""
    table = _CountingTable()
    kwargs = _kwargs(remote=True, ats="lever")
    facets.counts(table, kwargs)
    assert build_filter(**kwargs) in table.seen  # the total was counted with exactly it


def test_the_any_row_is_counted_with_its_own_dimension_lifted():
    """The mistake this rule exists to prevent, in the one place it is easy to make.

    With a 2-hour window active, counting "Any time" against the filters as they stand reports
    the 2-hour total — so the unconstrained option reads SMALLER than the 24-hour option nested
    inside it. It has to be counted with its dimension removed, exactly like every other option.
    """
    table = _CountingTable(lambda where: 5 if where and "first_seen" in where else 5000)
    out = facets.counts(table, _kwargs(seen_within=2))
    any_row = next(o for o in out["facets"]["seen_within"] if o["value"] is None)
    assert any_row["label"] == "Any"
    assert any_row["count"] == 5000  # the dimension lifted, not the current 5
    assert (
        out["total"] == 5
    )  # ...while the total keeps every filter, including that window


def test_the_switches_get_no_any_row():
    # A checkbox's "off" is the absence of the row, not another row to draw.
    out = facets.counts(_CountingTable(), _kwargs())
    for dimension in ("remote", "has_salary"):
        assert all(o["value"] is not None for o in out["facets"][dimension])


def test_sorting_by_posted_narrows_the_counts_the_same_way_it_narrows_the_list():
    """`run` sorts only rows with a readable posting date, so the count must exclude them too —
    otherwise the header overstates the result set by the 8.4% carrying no such date."""
    table = _CountingTable()
    facets.counts(table, _kwargs(posted_sortable=True))
    assert all("posted_at LIKE" in (c or "") for c in table.seen if c)
