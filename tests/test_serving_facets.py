"""Tests for the Search tab's facet counts (headstart.serving.facets, issue #275).

Contracts: a facet's own constraint is lifted before its options are counted, so the numbers
answer "what would I get if I switched" rather than repeating the current total; every other
filter stays applied; the counts never touch the encoder, because a vector search ranks the
filtered set rather than shrinking it; and when nothing matched, `blocking` names the one
filter actually responsible instead of leaving the user to guess.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lancedb
import pyarrow as pa
import pytest

from headstart.ingest.index import _schema
from headstart.search_filters.compiler import (
    KEYWORD_DEFAULT_SCOPE,
    KEYWORD_SCOPES,
    IndexCapabilities,
    SearchFilters,
    account_clause,
    build_filter,
    with_extra,
)
from headstart.serving import facets


class _CountingTable:
    """A table that answers `count_rows` from a rule, and records every clause it was asked.

    Counting real rows is `test_serving_job_search.py`'s job; what matters here is *which where-clause*
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

    # The Keyword filter's one read of the table (#834) needs the served columns to project, and
    # finds no rows here: this fake has a rule, not rows, so every count that keeps a keyword is 0.
    schema = _schema(2)

    def search(self):
        return _NoRows(self)


class _NoRows:
    """`_CountingTable.search()`: records the keyword's clause and reads back no rows."""

    def __init__(self, table: _CountingTable):
        self._table = table
        self._columns: list[str] = []

    def where(self, clause):
        self._table.seen.append(clause)
        return self

    def select(self, columns):
        self._columns = columns
        return self

    def with_row_id(self, _asked):
        return self

    def to_arrow(self):
        empty = _schema(2).empty_table().select(self._columns)
        return empty.append_column("_rowid", pa.array([], pa.uint64()))


_CAPABILITY_FIELDS = set(IndexCapabilities.__dataclass_fields__)


def _kwargs(**overrides) -> tuple[SearchFilters, IndexCapabilities]:
    """A ``(SearchFilters, IndexCapabilities)`` pair from one flat kwargs blob (ADR-0149).

    Every call site below splats this straight into :func:`facets.counts` — ``facets.counts(table,
    *_kwargs(...))`` — so an override routes to whichever of the two objects actually owns that
    field, and the bulk of this file's tests need no other change.
    """
    caps_base = {
        "atses": ["greenhouse", "lever"],
        "currencies": ["USD"],
        "has_first_seen": True,
        "has_min_salary_annual": True,
        "has_description": True,
        "has_country": True,
    }
    caps = {k: v for k, v in overrides.items() if k in _CAPABILITY_FIELDS}
    filters = {k: v for k, v in overrides.items() if k not in _CAPABILITY_FIELDS}
    return SearchFilters(**filters), IndexCapabilities(**{**caps_base, **caps})


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
    out = facets.counts(table, *_kwargs(seen_within=2))
    day = next(o for o in out["facets"]["seen_within"] if o["value"] == 24)
    assert day["count"] == 42  # counted on its own terms, not intersected away
    # exactly one first_seen clause per option: the current 2h window was replaced, not ANDed
    for clause in table.seen:
        assert (clause or "").count("first_seen >=") <= 1


def test_other_filters_stay_applied_while_one_dimension_varies():
    table = _CountingTable()
    facets.counts(table, *_kwargs(remote=True, seen_within=2))
    ats_clauses = [c for c in table.seen if c and "ats = " in c]
    assert ats_clauses  # the ATS strip was counted
    assert all("remote = true" in c for c in ats_clauses)  # ...with `remote` still on


def test_counts_never_need_the_query_or_the_encoder():
    """A vector search ranks the filtered set rather than shrinking it, so a count is decided
    by the where-clause alone — which is why the request's query never reaches this module at
    all. The signature is the guarantee: there is nowhere to pass one.

    `extra_where` (ADR-0171) is keyword-only and is a *clause*, not a query — it exists so the
    Account's follow/hide narrowing reaches the counts as well as the list they describe.
    `only_total` (ADR-0274) is keyword-only too, and says how much to count, not what.
    `table_where` (ADR-0320) compiles filters to a clause keeping the same rows, not a query.
    """
    import inspect

    params = inspect.signature(facets.counts).parameters
    assert list(params) == [
        "table",
        "filters",
        "capabilities",
        "extra_where",
        "only_total",
        "table_where",
    ]
    assert params["extra_where"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["only_total"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["table_where"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not {"q", "query", "model", "encoder"} & set(params), "no query, no encoder"
    a, b = _CountingTable(), _CountingTable()
    without = facets.counts(a, *_kwargs())
    withq = facets.counts(b, *_kwargs())
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
    out = facets.counts(table, *_kwargs(remote=True))
    assert out["total"] == 7


def test_blocking_is_none_while_anything_matched():
    out = facets.counts(_CountingTable(), *_kwargs(remote=True))
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

    out = facets.counts(_CountingTable(rule), *_kwargs(remote=True, company="nope"))
    assert out["total"] == 0
    assert out["blocking"] == "company"


def test_blocking_stays_silent_when_no_single_filter_is_to_blame():
    # Nothing matched even with every filter dropped, so naming one would be a lie.
    out = facets.counts(_CountingTable(lambda where: 0), *_kwargs(remote=True))
    assert out["total"] == 0
    assert out["blocking"] is None


def test_entry_level_can_be_named_as_the_blocker():
    """`max_years=0` is a real constraint, but `0 == False` in Python — a membership test for
    unset values silently calls it inactive, and the Entry-level filter could then never be
    reported as the one ruling everything out."""

    def rule(where):
        return 0 if where and "min_years <= 0" in where else 4000

    out = facets.counts(_CountingTable(rule), *_kwargs(max_years=0))
    assert out["total"] == 0
    assert out["blocking"] == "max_years"


def test_the_runtime_facts_of_the_index_are_never_offered_as_droppable():
    """Structural now, not denylist-based (ADR-0149): these are `IndexCapabilities` fields, and
    `_blocking` only ever walks `SearchFilters`'s — so none of them can even be considered, let
    alone named as the blocker."""
    capability_names = set(IndexCapabilities.__dataclass_fields__)
    assert not capability_names & set(SearchFilters.__dataclass_fields__)

    def rule(where):
        return 0 if where else 10

    out = facets.counts(_CountingTable(rule), *_kwargs(remote=True))
    assert out["blocking"] not in capability_names


def test_a_date_range_is_never_offered_as_droppable():
    """The Matches tab's ranges reach `/search` under keys the Search tab has no control for,
    so naming one would print a raw key beside a "remove it" button that removes nothing —
    `dropFilter` clears through `CONTROL`, which holds no id for them. The generic "try
    loosening a filter" fallback is the honest answer instead."""

    def rule(where):
        return 0 if where and "posted_at >=" in where else 10

    out = facets.counts(_CountingTable(rule), *_kwargs(posted_after="2026-08-01"))
    assert out["total"] == 0
    assert out["blocking"] is None


def test_the_salary_facet_stays_dark_without_the_salary_columns():
    out = facets.counts(_CountingTable(), *_kwargs(has_min_salary_annual=False))
    assert "has_salary" not in out["facets"]


def test_the_recency_facet_stays_dark_without_the_first_seen_column():
    """The same dark-until-migrated rule the salary facet follows, on the recency window.

    `build_filter` compiles nothing for `seen_within` without the column, so counting its
    options there gives all eight windows AND their "Any" row the identical unfiltered total —
    a dropdown whose every entry claims to cost nothing, and eight counts nobody can use.
    """
    table = _CountingTable()
    out = facets.counts(table, *_kwargs(has_first_seen=False))
    assert "seen_within" not in out["facets"]  # not even a lone "Any" row
    assert not any(w and "first_seen" in w for w in table.seen)


# ---- the Keyword filter's disclaimer (ADR-0104) ----


def test_only_the_total_counts_no_option_but_keeps_the_totals_three_answers():
    """ADR-0274: an agent printing only the total asks for it alone. Under a description
    keyword each option re-scans the matches, so the full strip took 98.7 s to the page's
    10.6 s; the count-only answer is the total, `blocking` and the coverage, counted as ever."""
    table = _CountingTable()
    args = _kwargs(remote=True, kw="visa", kw_in="description")
    out = facets.counts(table, *args, only_total=True)
    unkeyed = build_filter(*_kwargs(remote=True))
    assert sorted(table.seen, key=str) == sorted(
        [build_filter(*args), f"({unkeyed}) AND description IS NOT NULL", unkeyed],
        key=str,
    )
    assert out == {
        "total": 42,
        "facets": {},
        "blocking": None,
        "description_coverage": {"covered": 42, "total": 42},
    }
    # That these equal the full strip's is `test_a_lone_total_is_the_full_strips`, on a real
    # table: the full strip counts over the keyword's rows (#834), which this fake has none of.


def test_only_the_total_still_names_the_blocking_filter_when_nothing_matched():
    def rule(where):
        return 0 if where and "company" in where else 5000

    out = facets.counts(
        _CountingTable(rule), *_kwargs(remote=True, company="nope"), only_total=True
    )
    assert out["total"] == 0 and out["blocking"] == "company"


def test_description_coverage_is_counted_with_the_keyword_lifted():
    table = _CountingTable()
    out = facets.counts(table, *_kwargs(remote=True, kw="rust", kw_in="description"))
    # Both numbers come from the other filters alone — the keyword lifted, as a dimension's "Any"
    # row lifts its own. Counted with it intact, a description-scoped keyword makes the covered
    # set and the total the same clause, and the disclaimer reads "N of N".
    unkeyed = build_filter(*_kwargs(remote=True))
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
    out = facets.counts(table, *_kwargs(has_description=False))
    assert out["description_coverage"] is None
    assert not any(w and "description IS NOT NULL" in w for w in table.seen)


def test_description_coverage_is_not_counted_when_the_ui_will_not_show_it():
    table = _CountingTable()
    facets.counts(table, *_kwargs())
    assert not any(where and "description IS NOT NULL" in where for where in table.seen)

    title = _CountingTable()
    facets.counts(title, *_kwargs(kw="rust", kw_in="title"))
    assert not any(where and "description IS NOT NULL" in where for where in title.seen)


def test_description_coverage_uses_the_materialized_presence_flag():
    table = _CountingTable()
    out = facets.counts(
        table,
        *_kwargs(
            kw="rust",
            kw_in="description",
            has_description_stored=True,
        ),
    )
    assert any(where and "description_stored = true" in where for where in table.seen)
    assert out["description_coverage"] == {"covered": 42, "total": 100}


def test_a_keyword_can_be_named_as_the_blocker_but_its_scope_never_can():
    # Everything matches until the keyword is applied; dropping it recovers 100.
    def rule(where):
        return 0 if where and "[^a-z0-9])rust'" in where else 100

    out = facets.counts(_CountingTable(rule), *_kwargs(kw="rust", kw_in="title"))
    assert out["total"] == 0
    assert out["blocking"] == "kw"


def test_every_option_carries_what_the_ui_needs_to_draw_it():
    out = facets.counts(_CountingTable(), *_kwargs())
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
    out = facets.counts(_CountingTable(), *_kwargs())
    assert (
        next(o for o in out["facets"]["seen_within"] if o["value"] == hours)["label"]
        == label
    )


def test_counts_compile_the_same_clauses_the_search_would():
    """The count and the list it counts must never describe different queries — which is why
    both go through `build_filter` on the same parsed filters."""
    table = _CountingTable()
    parsed = _kwargs(remote=True, ats="lever")
    facets.counts(table, *parsed)
    assert build_filter(*parsed) in table.seen  # the total was counted with exactly it


def test_the_any_row_is_counted_with_its_own_dimension_lifted():
    """The mistake this rule exists to prevent, in the one place it is easy to make.

    With a 2-hour window active, counting "Any time" against the filters as they stand reports
    the 2-hour total — so the unconstrained option reads SMALLER than the 24-hour option nested
    inside it. It has to be counted with its dimension removed, exactly like every other option.
    """
    table = _CountingTable(lambda where: 5 if where and "first_seen" in where else 5000)
    out = facets.counts(table, *_kwargs(seen_within=2))
    any_row = next(o for o in out["facets"]["seen_within"] if o["value"] is None)
    assert any_row["label"] == "Any"
    assert any_row["count"] == 5000  # the dimension lifted, not the current 5
    assert (
        out["total"] == 5
    )  # ...while the total keeps every filter, including that window


def test_the_switches_get_no_any_row():
    # A checkbox's "off" is the absence of the row, not another row to draw.
    out = facets.counts(_CountingTable(), *_kwargs())
    for dimension in ("remote", "has_salary"):
        assert all(o["value"] is not None for o in out["facets"][dimension])


def test_sorting_by_posted_narrows_the_counts_the_same_way_it_narrows_the_list():
    """`run` sorts only rows with a readable posting date, so the count must exclude them too —
    otherwise the header overstates the result set by the 8.4% carrying no such date."""
    table = _CountingTable()
    facets.counts(table, *_kwargs(posted_sortable=True))
    assert all("posted_at LIKE" in (c or "") for c in table.seen if c)


APP_JS = Path(__file__).resolve().parents[1] / "src/headstart/ui/static/app.js"


def _js_decl(name: str) -> str:
    """The right-hand side of ``const <name> = …;`` in app.js, as raw text.

    There is no import seam between this module's deny-list and the browser's own maps, and the
    check below spans exactly that gap, so the JS is read rather than run. `tests/js/` does
    evaluate app.js properly, but it cannot see `build_filter`'s parameters — the half that has
    to drive the comparison.
    """
    decl = re.search(
        rf"^const {name} = (.*?);$", APP_JS.read_text(), re.MULTILINE | re.DOTALL
    )
    assert decl, f"app.js no longer declares `const {name}`"
    return decl.group(1)


def test_every_nameable_filter_is_labelled_and_clearable_in_the_ui():
    """A filter `_blocking` can name needs a label AND a control, or the empty state prints a
    raw key beside a "remove it" button that removes nothing.

    The two lists are hand-maintained in different languages, so an omission is otherwise
    silent — which is how the Matches tab's four date ranges came to be nameable with neither.
    """
    labels = set(re.findall(r"(\w+)\s*:", _js_decl("LABELS")))
    control = set(re.findall(r"(\w+)\s*:", _js_decl("CONTROL")))
    # dropFilter() clears the salary bracket through its two bounds, so its members need no
    # control of their own.
    bracket = set(re.findall(r"'([^']+)'", _js_decl("BRACKET")))
    # `SearchFilters`'s own fields (ADR-0149) — not `build_filter`'s signature, which is now
    # just `(filters, capabilities)` and would tell this test nothing about individual names.
    # An agent-only filter (ADR-0322) is nameable, but the page never sends it, so its empty
    # state never has one to name.
    nameable = (
        set(SearchFilters.__dataclass_fields__)
        - set(facets.NEVER_BLOCKING)
        - set(facets.AGENT_ONLY)
    )
    for key in sorted(nameable):
        assert key in labels, (
            f"{key} can be the Blocking filter but has no LABELS entry"
        )
        assert key in control or key in bracket, (
            f"{key} can be the Blocking filter but dropFilter() has nothing to clear"
        )
    # ...and the other way: a filter that gains a Search-tab control has to leave the deny-list,
    # or it stays silently un-nameable. LABELS is deliberately not checked in this direction —
    # `kw_in` is labelled for the active-filter pills while being denied as a blocker.
    assert not control & set(facets.NEVER_BLOCKING)


def test_the_account_clause_narrows_every_count_including_the_total():
    """The UI prints `facets.total` as "Showing 1-N of TOTAL" beside the list `run` returns.

    Counting without the Account's follow/hide clause reported the whole index next to a list
    of ten rows — measured live at 459,291 against 10.
    """
    table = _CountingTable()
    facets.counts(
        table, *_kwargs(remote=True), extra_where="NOT (lower(id) LIKE 'lever:x:%')"
    )
    assert table.seen, "something was counted"
    assert all("NOT (lower(id) LIKE 'lever:x:%')" in (c or "") for c in table.seen), (
        "every count, not just the total"
    )


def test_the_account_clause_narrows_the_blocking_answer_too():
    """A zero total under the "Following" toggle must be blamed on the population the user is
    actually looking at. Recounted over the whole index instead, dropping `max_years` recovers
    50 unfollowed Lever rows — so it was named, and removing it still showed nothing, while
    dropping `ats` would have recovered the 3 followed Greenhouse jobs."""
    followed = "lower(id) LIKE 'greenhouse:acme:%'"

    def rule(where):
        where = where or ""
        if followed in where:
            return 0 if "ats = 'lever'" in where else 3
        return 50 if "ats = 'lever'" in where and "min_years" not in where else 0

    out = facets.counts(
        _CountingTable(rule), *_kwargs(ats="lever", max_years=2), extra_where=followed
    )
    assert out["total"] == 0
    assert out["blocking"] == "ats"


def test_facets_never_imports_job_search():
    """`job_search.py` imports this module at the top (ADR-0194), so an import back would be a cycle.

    Both compile through `headstart.search_filters.compiler`, so the counts and the ranked list they
    describe still share one compiler. That shared compiler is what the old deferred import in
    `JobSearch.facets` protected, and reaching it no longer means importing `job_search`.
    """
    import ast

    tree = ast.parse(Path(facets.__file__).read_text(encoding="utf-8"))
    package = facets.__name__.rpartition(".")[0]
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                node.level
            ):  # `from . import job_search`, `from .job_search import JobSearch`
                base = package.rsplit(".", node.level - 1)[0]
                module = f"{base}.{module}" if module else base
            imported.add(module)
            if module.split(".")[0] == "headstart":
                imported |= {f"{module}.{alias.name}" for alias in node.names}
    assert "headstart.serving.job_search" not in imported


# ---- the Keyword filter read once, over a real table (#834) ----
#
# A keyword is applied by reading its rows once and counting every option over them. These pin
# that each count is still the one the list would show: exactly what counting the compiled
# where-clause over the whole table gives, which is what `counts` did before.

_NOW = datetime.now(UTC)


def _hours_ago(h: float) -> str:
    return (_NOW - timedelta(hours=h)).isoformat(timespec="seconds")


def _days_ago(d: int) -> str:
    return (_NOW - timedelta(days=d)).strftime("%Y-%m-%d")


# (ats, company, title, description, location, remote, employment_type, min_years,
#  min/max salary, currency, posted_at, first_seen). Keyword matches in the title only, the
# description only, both, and neither; null descriptions, titles and locations; India by the
# country column and by a city only the gazetteer knows; salaries in three currencies and none;
# ISO, datetime-shaped, non-ISO and missing posting dates; recent and old first sightings.
_ROWS = [
    (
        "greenhouse",
        "Acme",
        "Senior Golang Engineer",
        "Go services",
        "Berlin, Germany",
        True,
        "Full-time",
        5,
        90000,
        140000,
        "USD",
        _days_ago(1),
        _hours_ago(1),
    ),
    (
        "greenhouse",
        "Acme",
        "Backend Developer",
        "We write golang and rust",
        "Bengaluru, Karnataka",
        False,
        "Full-time",
        2,
        1500000,
        None,
        "INR",
        _days_ago(3),
        _hours_ago(5),
    ),
    (
        "greenhouse",
        "Beta",
        "Rust Developer",
        None,
        "Remote - India",
        True,
        "Contract",
        None,
        None,
        None,
        None,
        "21-Apr-2026",
        _hours_ago(20),
    ),
    (
        "lever",
        "Beta",
        "Data Engineer",
        "python, go, rust",
        "Koramangala",
        False,
        "Permanent / part-time",
        0,
        60000,
        80000,
        "EUR",
        _days_ago(10) + "T12:00:00Z",
        _hours_ago(30),
    ),
    (
        "lever",
        "Gamma",
        "Staff Engineer (C_ lang)",
        "c_ and c++",
        "New York, NY",
        None,
        "Internship",
        10,
        200000,
        260000,
        "USD",
        _days_ago(60),
        _hours_ago(24 * 5),
    ),
    (
        "lever",
        "Gamma",
        "Golang Intern",
        "",
        None,
        False,
        "intern",
        0,
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "workday",
        "Delta",
        None,
        "golang backend",
        "Pune, India",
        False,
        None,
        3,
        45000,
        50000,
        "GBP",
        _days_ago(400),
        _hours_ago(24 * 30),
    ),
    (
        "workday",
        "Delta",
        "Frontend Developer",
        "React",
        "London, UK",
        True,
        "Full-time",
        1,
        70000,
        None,
        "USD",
        _days_ago(2),
        _hours_ago(3),
    ),
    (
        "workday",
        "Acme",
        "Senior Rust Developer",
        "RUST and GOLANG",
        "Berlin",
        False,
        "Full-time",
        7,
        None,
        None,
        None,
        _days_ago(5),
        _hours_ago(7),
    ),
    (
        "greenhouse",
        "Epsilon 100%",
        "Platform Engineer",
        "kubernetes, go",
        "Hyderabad",
        None,
        "Contract",
        None,
        120000,
        150000,
        "USD",
        _days_ago(20),
        _hours_ago(2),
    ),
]


@pytest.fixture(scope="module")
def jobs_table(tmp_path_factory):
    """The served schema, the flags the index derives, and the rows above."""
    from headstart.search_filters import (
        employment_type_filter,
        experience_filter,
        india_filter,
        posted_date_guard,
        salary_known_filter,
    )

    rows = []
    for n, (
        ats,
        company,
        title,
        description,
        location,
        remote,
        etype,
        years,
        lo,
        hi,
        cur,
        posted,
        seen,
    ) in enumerate(_ROWS):
        rows.append(
            {
                "id": f"{ats}:{company.split()[0].lower()}:{n}",
                "ats": ats,
                "company": company,
                "title": title,
                "description": description,
                "description_stored": description is not None,
                "location": location,
                "country": india_filter.country(location),
                "remote": remote,
                "employment_type": etype,
                **employment_type_filter.flags(etype),
                "min_years": years,
                **experience_filter.flags(years),
                "min_salary_annual": lo,
                "max_salary_annual": hi,
                "salary_currency": cur,
                **salary_known_filter.flags(lo),
                "posted_at": posted,
                **posted_date_guard.flags(posted),
                "first_seen": seen,
                "vector": [0.0, 1.0],
            }
        )
    db = lancedb.connect(tmp_path_factory.mktemp("facets_db"))
    return db.create_table("jobs", pa.Table.from_pylist(rows, schema=_schema(2)))


_MATERIALIZED = IndexCapabilities(
    atses=["greenhouse", "lever", "workday"],
    currencies=["EUR", "GBP", "INR", "USD"],
    has_first_seen=True,
    has_min_salary_annual=True,
    has_description=True,
    has_country=True,
    has_employment_type_flags=True,
    has_description_stored=True,
    has_salary_known=True,
    has_posted_at_comparable=True,
    has_experience_filter_flags=True,
)
# The same table read through the raw clauses a table without the ADR-0173 flags compiles to:
# the gazetteer's regex, the `posted_at LIKE` guard, `min_years`, the employment-type LIKEs.
_RAW = replace(
    _MATERIALIZED,
    has_country=False,
    has_employment_type_flags=False,
    has_description_stored=False,
    has_salary_known=False,
    has_posted_at_comparable=False,
    has_experience_filter_flags=False,
)

_KEYWORDS = [
    {"kw": "golang", "kw_in": "both"},
    {"kw": "go rust", "kw_in": "description"},
    {"kw": "c_", "kw_in": "title"},
    {"kw": "developer", "kw_in": "both"},
    {"kw": "zzqx", "kw_in": "both"},
    {},
]
_FILTERS = [
    {},
    {"remote": True, "ats": "lever"},
    {"india": "india", "max_years": 2},
    {"india": "bengaluru"},
    {"country": "DE"},
    {"country": "IN", "max_years": 5},
    {"salary_min": 100000, "has_salary": True},
    {"salary_min": 40000, "salary_max": 90000, "salary_currency": "EUR"},
    {"posted_within": 7, "posted_sortable": True, "seen_within": 24},
    {"posted_after": _days_ago(30), "posted_before": _days_ago(1)},
    {"max_years": 0, "etype": "full-time"},
    {"title_words": "senior", "company": "acme", "location": "berlin"},
    {"ats": "workday", "remote": True, "etype": "internship"},
]


def _whole_table(table, filters, capabilities, extra_where, options):
    """What counting each compiled clause over the whole table gives, as `counts` did before
    #834: the total, every option in ``options``, the blocking filter and the coverage."""

    def n(f):
        where = with_extra(build_filter(f, capabilities), extra_where)
        return table.count_rows(filter=where) if where else table.count_rows()

    total = n(filters)
    blocking, best = None, 0
    for key, value in vars(filters).items() if not total else ():
        if (
            key in facets.NEVER_BLOCKING
            or value is None
            or value is False
            or value == ""
        ):
            continue
        recovered = n(
            replace(filters, **{key: False if isinstance(value, bool) else None})
        )
        if recovered > best:
            blocking, best = key, recovered
    unkeyed = replace(filters, kw=None, kw_in=None)
    covered = facets._with_description(
        with_extra(build_filter(unkeyed, capabilities), extra_where),
        capabilities.has_description_stored,
    )
    scope = KEYWORD_SCOPES[filters.kw_in or KEYWORD_DEFAULT_SCOPE]
    return {
        "total": total,
        "facets": {
            dimension: [
                {**o, "count": n(replace(filters, **{dimension: o["value"]}))}
                for o in opts
            ]
            for dimension, opts in options.items()
        },
        "blocking": blocking,
        "description_coverage": (
            {"covered": table.count_rows(filter=covered), "total": n(unkeyed)}
            if filters.kw and "description" in scope.columns
            else None
        ),
    }


@pytest.mark.parametrize("capabilities", [_MATERIALIZED, _RAW], ids=["flags", "raw"])
@pytest.mark.parametrize(
    "keyword", _KEYWORDS, ids=lambda k: "-".join(k.values()) or "none"
)
@pytest.mark.parametrize("others", _FILTERS, ids=lambda f: "-".join(f) or "none")
def test_every_count_is_the_one_the_whole_table_gives(
    jobs_table, capabilities, keyword, others
):
    filters = SearchFilters(**keyword, **others)
    out = facets.counts(jobs_table, filters, capabilities)
    assert out == _whole_table(jobs_table, filters, capabilities, None, out["facets"])


@pytest.mark.parametrize(
    "extra_where",
    [
        account_clause(["greenhouse:acme"], [], mine=True),
        account_clause([], ["workday:acme", "lever:gamma"], mine=False),
    ],
    ids=["following", "hiding"],
)
@pytest.mark.parametrize(
    "keyword", _KEYWORDS[:2] + _KEYWORDS[4:5], ids=lambda k: k["kw"]
)
def test_the_account_clause_narrows_the_keyword_rows_the_same_way(
    jobs_table, keyword, extra_where
):
    filters = SearchFilters(**keyword, remote=False)
    out = facets.counts(jobs_table, filters, _MATERIALIZED, extra_where=extra_where)
    assert out == _whole_table(
        jobs_table, filters, _MATERIALIZED, extra_where, out["facets"]
    )


def test_a_keyword_reaches_the_table_once_per_request(jobs_table):
    """#834: every option's count carried the keyword's `LIKE` over the description column, so
    one request scanned it about 80 times and took 103 s on the Space. Now the keyword's rows
    are read once, and only the two coverage counts, which lift the keyword, count the table."""
    table = _RecordingTable(jobs_table)
    facets.counts(table, SearchFilters(kw="golang", kw_in="both"), _MATERIALIZED)
    keyed = [w for w in table.seen if w and "golang" in w]
    assert keyed == [
        (
            "(regexp_like(title, '(?i)(^|[^a-z0-9])golang') OR "
            "regexp_like(description, '(?i)(^|[^a-z0-9])golang'))"
        )
    ]
    assert len(table.seen) == 3  # the read, and the coverage's two counts


def test_the_read_keeps_every_filter_no_count_lifts(jobs_table):
    """A filter every count keeps narrows the read, so the description is scanned only where
    it can matter; a listed dimension, whose "Any" row lifts it, cannot."""
    table = _RecordingTable(jobs_table)
    filters = SearchFilters(kw="golang", kw_in="both", remote=True, ats="greenhouse")
    facets.counts(table, filters, _MATERIALIZED)
    (read,) = [w for w in table.seen if w and "golang" in w]
    assert "remote = true" in read
    assert "ats = " not in read


@pytest.mark.parametrize(
    "filters, extra_where",
    [
        # A real Board (ashby:vector): hiding it keeps nearly every row in the read.
        (
            SearchFilters(kw="golang", kw_in="both"),
            account_clause([], ["ashby:vector"], mine=False),
        ),
        (SearchFilters(kw="golang", kw_in="both", company="description"), None),
    ],
    ids=["hidden-board-vector", "company-description"],
)
def test_the_read_never_copies_a_column_only_a_quoted_term_names(
    jobs_table, filters, extra_where
):
    """The read keeps the columns the counts name, and a word inside a quoted term names none.
    Matched over the whole clause, hiding ashby:vector copied the 768-float `vector` column of
    every row read into memory: 1,293 MB against 25 MB for "engineer" on the served table."""
    table = _RecordingTable(jobs_table)
    out = facets.counts(table, filters, _MATERIALIZED, extra_where=extra_where)
    (read,) = table.selected
    assert not {"vector", "description"} & set(read)
    assert out == _whole_table(
        jobs_table, filters, _MATERIALIZED, extra_where, out["facets"]
    )


class _RecordingTable:
    """A real table that records every clause reaching it, counted or read, and the columns
    each read selects."""

    def __init__(self, table):
        self._table = table
        self.seen: list[str | None] = []
        self.selected: list[list[str]] = []
        self.schema = table.schema

    def count_rows(self, filter=None):
        self.seen.append(filter)
        return self._table.count_rows(filter=filter)

    def search(self):
        table = self

        class _Query:
            def __init__(self, query):
                self._query = query

            def where(self, clause):
                table.seen.append(clause)
                return _Query(self._query.where(clause))

            def with_row_id(self, asked):
                return _Query(self._query.with_row_id(asked))

            def select(self, columns):
                table.selected.append(columns)
                return _Query(self._query.select(columns))

            def __getattr__(self, name):
                return getattr(self._query, name)

        return _Query(self._table.search())


@pytest.mark.parametrize(
    "keyword", _KEYWORDS, ids=lambda k: "-".join(k.values()) or "none"
)
def test_a_lone_total_is_the_full_strips(jobs_table, keyword):
    """ADR-0274's count-only answer reads no rows first, and says what the full strip says."""
    filters = SearchFilters(**keyword, remote=True, ats="workday")
    full = facets.counts(jobs_table, filters, _MATERIALIZED)
    alone = facets.counts(jobs_table, filters, _MATERIALIZED, only_total=True)
    assert alone == {**full, "facets": {}}
