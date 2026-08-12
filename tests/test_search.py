"""Tests for the shared search layer — both where-clause builders and JobSearch.

The builders are the one place user input reaches the LanceDB where-clause, so their
validation (whitelists, re-serialization, escaping) is worth locking down: `eval_filter`
is the frozen benchmark builder (ADR-0019), `build_filter` the reference product filter
(ADR-0031, moved here from the Space app in ADR-0042). `JobSearch` is exercised through
its interface with a fake encoder and table — no model load, so all of this runs in the
standard test env.
"""

from __future__ import annotations

import types

import pytest

from headstart.search import (
    EMPLOYMENT_TYPES,
    JobSearch,
    build_filter,
    eval_filter,
)

# ---- eval_filter: the frozen benchmark builder (was named build_filter) ----


def test_eval_no_filters_returns_none():
    assert eval_filter() is None


def test_eval_remote_only():
    assert eval_filter(remote=True) == "remote = true"


def test_eval_employment_type_must_be_known():
    for value in EMPLOYMENT_TYPES:
        assert eval_filter(employment_type=value) == f"employment_type = '{value}'"


def test_eval_unknown_employment_type_rejected():
    with pytest.raises(ValueError):
        eval_filter(employment_type="full-time'; DROP TABLE wellfound; --")


def test_eval_max_years_keeps_unknown_experience():
    assert eval_filter(max_years=5) == "(min_years <= 5 OR min_years IS NULL)"


def test_eval_filters_combine_with_and():
    clause = eval_filter(remote=True, employment_type="contract", max_years=3)
    assert clause == (
        "remote = true AND employment_type = 'contract' "
        "AND (min_years <= 3 OR min_years IS NULL)"
    )


# ---- build_filter: the reference product builder ----


def _clause(**kw):
    kw.setdefault("atses", ("greenhouse", "lever"))
    kw.setdefault("has_first_seen", True)
    return build_filter(**kw)


def test_no_filters_is_no_clause():
    assert _clause() is None


def test_first_seen_after_is_strictly_greater_than():
    # Strict `>`: a Watermark taken from a row's own first_seen must not re-select that row.
    clause = _clause(first_seen_after="2026-08-02T12:00:00+00:00")
    assert clause == "first_seen > '2026-08-02T12:00:00+00:00'"


def test_first_seen_after_is_reserialized_not_interpolated():
    # The value arrives as free text and lands in a where-clause, so it is parsed and
    # re-emitted; a quote cannot survive that round trip.
    with pytest.raises(ValueError):
        _clause(first_seen_after="2026-08-02' OR '1'='1")
    with pytest.raises(ValueError):
        _clause(first_seen_after="yesterday")


def test_first_seen_after_normalizes_sub_second_precision():
    clause = _clause(first_seen_after="2026-08-02T12:00:00.123456+00:00")
    assert clause == "first_seen > '2026-08-02T12:00:00+00:00'"


def test_first_seen_after_combines_with_other_filters():
    clause = _clause(
        remote=True, max_years=3, first_seen_after="2026-08-02T12:00:00+00:00"
    )
    assert clause.startswith("remote = true AND (min_years <= 3")
    assert clause.endswith("AND first_seen > '2026-08-02T12:00:00+00:00'")


def test_seen_within_still_works_beside_it():
    assert "first_seen >= '" in _clause(seen_within=6)


def test_unknown_ats_is_ignored_rather_than_interpolated():
    assert _clause(ats="'; DROP TABLE jobs; --") is None


def test_known_ats_passes_the_whitelist():
    assert _clause(ats="lever") == "ats = 'lever'"


def test_first_seen_filters_stay_dark_without_the_column():
    assert _clause(seen_within=6, has_first_seen=False) is None
    assert (
        _clause(first_seen_after="2026-08-02T12:00:00+00:00", has_first_seen=False)
        is None
    )


def test_location_quotes_are_doubled():
    assert _clause(location="O'Fallon") == "lower(location) LIKE '%o''fallon%'"


def test_india_expands_via_geo():
    clause = _clause(india="bengaluru")
    assert clause is not None and "lower(location) LIKE" in clause


# ---- JobSearch: through its interface, with fakes ----


class _Vec:
    def astype(self, _dtype):
        return self


class _Model:
    def encode(self, texts, normalize_embeddings=False):
        assert texts[0].startswith(
            "search_query: "
        )  # the load-bearing prefix (ADR-0005)
        return [_Vec()]


class _Query:
    """The chained lancedb query object; records what reached the table."""

    def __init__(self, table):
        self._t = table

    def metric(self, _m):
        return self

    def select(self, _cols):
        return self

    def where(self, clause, prefilter=False):
        self._t.last_where = clause
        return self

    def limit(self, k):
        self._t.last_k = k
        return self

    def to_list(self):
        return list(self._t.rows)


class _Table:
    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])

    def __init__(self, rows):
        self.rows = rows
        self.last_where = None
        self.last_k = None

    def search(self, *args, **kwargs):
        return _Query(self)


_ROW = {
    "_distance": 0.25,
    "title": "Backend Engineer",
    "company": "Acme",
    "location": "Berlin",
    "remote": True,
    "employment_type": "Full-time",
    "min_years": 3,
    "salary": None,
    "ats": "darwinbox",
    "posted_at": "2026-08-01",
    "first_seen": "2026-08-10T00:00:00+00:00",
    "url": "https://x.darwinbox.in/ms/candidate/careers/jobs/abc",
}


def _searcher():
    table = _Table([dict(_ROW)])
    return JobSearch(_Model(), table), table


def test_startup_scan_learns_atses_and_first_seen():
    searcher, _ = _searcher()
    assert searcher.atses == ["darwinbox"]
    assert searcher.has_first_seen is True


def test_empty_query_returns_empty_without_touching_the_model():
    class _Boom:
        def encode(self, *args, **kwargs):
            raise AssertionError("encoded an empty query")

    assert JobSearch(_Boom(), _Table([dict(_ROW)])).run({"q": "   "}) == []


def test_run_projects_rows_and_scores():
    searcher, _ = _searcher()
    rows = searcher.run({"q": "backend"})
    assert rows[0]["score"] == 0.75  # 1 - _distance
    assert rows[0]["title"] == "Backend Engineer"
    assert "_distance" not in rows[0]


def test_run_heals_stale_darwinbox_urls():
    searcher, _ = _searcher()
    assert (
        "/ms/candidatev2/main/careers/jobDetails/" in searcher.run({"q": "x"})[0]["url"]
    )


def test_filters_reach_the_where_clause():
    searcher, table = _searcher()
    searcher.run({"q": "x", "remote": "true", "ats": "darwinbox"})
    assert "remote = true" in table.last_where
    assert "ats = 'darwinbox'" in table.last_where


def test_k_is_capped():
    searcher, table = _searcher()
    searcher.run({"q": "x", "k": "5000"})
    assert table.last_k == 100


def test_k_zero_floors_to_one_not_the_default():
    # The old route's `int(raw or 20)` gave k=0 → 1 row; an `or` on the parsed int would
    # have silently turned it into 20. Pin the floor.
    searcher, table = _searcher()
    searcher.run({"q": "x", "k": "0"})
    assert table.last_k == 1


def test_garbage_int_raises_valueerror():
    searcher, _ = _searcher()
    with pytest.raises(ValueError):
        searcher.run({"q": "x", "k": "lots"})
    with pytest.raises(ValueError):
        searcher.run({"q": "x", "max_years": "several"})


# ---- custom date ranges (Matches view controls; both ends optional, both inclusive) ----


def test_posted_range_is_inclusive_and_shape_guarded():
    clause = _clause(posted_after="2026-08-01", posted_before="2026-08-10")
    # inclusive end: strictly below the NEXT day, since '2026-08-10T12:00' > '2026-08-10'
    assert "(posted_at >= '2026-08-01' AND posted_at LIKE '____-__-__%')" in clause
    assert "(posted_at < '2026-08-11' AND posted_at LIKE '____-__-__%')" in clause


def test_seen_range_uses_first_seen_and_goes_dark_without_the_column():
    clause = _clause(seen_after="2026-08-01", seen_before="2026-08-10")
    assert "first_seen >= '2026-08-01'" in clause
    assert "first_seen < '2026-08-11'" in clause
    assert (
        _clause(seen_after="2026-08-01", seen_before="2026-08-10", has_first_seen=False)
        is None
    )


def test_range_values_are_reserialized_not_interpolated():
    for kw in ("posted_after", "posted_before", "seen_after", "seen_before"):
        with pytest.raises(ValueError):
            _clause(**{kw: "2026-08-01' OR '1'='1"})
        with pytest.raises(ValueError):
            _clause(**{kw: "yesterday"})


def test_run_passes_ranges_and_rejects_garbage():
    searcher, table = _searcher()
    searcher.run({"q": "x", "posted_after": "2026-08-01", "seen_before": "2026-08-10"})
    assert "posted_at >= '2026-08-01'" in table.last_where
    assert "first_seen < '2026-08-11'" in table.last_where
    with pytest.raises(ValueError):
        searcher.run({"q": "x", "posted_after": "not-a-date"})
