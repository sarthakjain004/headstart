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

    def order_by(self, ordering):
        self._t.last_order = ordering
        return self

    def limit(self, k):
        self._t.last_k = k
        return self

    def offset(self, off):
        self._t.last_offset = off
        return self

    def to_list(self):
        return list(self._t.rows)


class _Table:
    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])

    def __init__(self, rows):
        self.rows = rows
        self.last_where = None
        self.last_k = None
        self.last_offset = None
        self.last_order = None

    def search(self, *args, **kwargs):
        self.last_query = args[0] if args else None  # None => a browse, not a search
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


def test_empty_query_browses_instead_of_searching_and_never_touches_the_model():
    # ADR-0074: an empty query lists the table's newest rows rather than returning nothing —
    # it must still never call the encoder, since there is no query to embed.
    class _Boom:
        def encode(self, *args, **kwargs):
            raise AssertionError("encoded an empty query")

    table = _Table([dict(_ROW)])
    rows = JobSearch(_Boom(), table).run({"q": "   "})
    assert rows[0]["title"] == "Backend Engineer"
    assert rows[0]["score"] is None  # no similarity was ever computed
    assert table.last_query is None  # search() called with no vector — a plain scan


def test_empty_query_orders_by_first_seen_desc_with_an_id_tiebreak():
    # The load-bearing regression: `first_seen` alone ties heavily (stamped once per sync
    # batch), and offset pagination over a tied sort silently repeats and drops rows across
    # pages (measured 2026-08-20). `id` must ride along as a tiebreaker on every browse.
    searcher, table = _searcher()
    searcher.run({"q": ""})
    assert table.last_order == [
        {"column_name": "first_seen", "ascending": False, "nulls_first": False},
        {"column_name": "id", "ascending": True},
    ]


def test_empty_query_without_first_seen_column_still_tiebreaks_on_id():
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(names=["ats", "title"])  # no first_seen column
    JobSearch(_Model(), table).run({"q": ""})
    assert table.last_order == [{"column_name": "id", "ascending": True}]


def test_a_real_query_never_gets_an_explicit_order_by():
    # Passing any order_by alongside a vector search was measured to override ranking by
    # similarity entirely (2026-08-20), not merely break ties within it — so the search path
    # must never call order_by at all, unlike the browse path above.
    searcher, table = _searcher()
    searcher.run({"q": "backend"})
    assert table.last_order is None
    assert table.last_query is not None  # search() called with an actual vector


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


def test_run_moves_recruitee_links_onto_the_tenant_host():
    """A stored recruitee row carries the customer's vanity domain, which is often dead. The
    tenant is in the id and the offer slug is in the path, so the live link is derivable."""
    row = dict(
        _ROW,
        ats="recruitee",
        id="recruitee:transperfect:2141029",
        url="https://transperfect.com/o/software-engineer-net-c-1",
    )
    rows = JobSearch(_Model(), _Table([row])).run({"q": "x"})
    assert (
        rows[0]["url"]
        == "https://transperfect.recruitee.com/o/software-engineer-net-c-1"
    )


def test_recruitee_rewrite_leaves_alone_what_it_cannot_rebuild():
    from headstart.search import _rehost_recruitee

    jid = "recruitee:transperfect:1"
    # already canonical
    canonical = "https://transperfect.recruitee.com/o/a-role"
    assert _rehost_recruitee(jid, canonical) == canonical
    # an apply link keeps the offer segment, not the trailing /c/new
    assert _rehost_recruitee(jid, "https://transperfect.com/o/a-role/c/new") == (
        "https://transperfect.recruitee.com/o/a-role"
    )
    # no /o/ segment, and no usable id: served as stored rather than mangled
    assert _rehost_recruitee(jid, "https://transperfect.com/careers") == (
        "https://transperfect.com/careers"
    )
    assert _rehost_recruitee(None, "https://transperfect.com/o/a-role") == (
        "https://transperfect.com/o/a-role"
    )


def test_filters_reach_the_where_clause():
    searcher, table = _searcher()
    searcher.run({"q": "x", "remote": "true", "ats": "darwinbox"})
    assert "remote = true" in table.last_where
    assert "ats = 'darwinbox'" in table.last_where


def test_has_salary_matches_a_description_only_derived_value():
    # A Job whose only known salary is Tier-2-derived from the description (ADR-0082) has
    # `salary` (the raw display string) null — it only ever gets populated from a scraper's
    # own structured field. Filtering has_salary on `salary IS NOT NULL` alone silently
    # excludes every description-only extraction, which is most of this initiative's own
    # measured coverage on most ATSes. Real example: ashby:clera:17e1a31f-3923-4af4-8b40-
    # 8fdbbc7c83d6 states "Salary range: €90,000 – €110,000 per year" only in
    # its description; `salary` is null, `min_salary_annual`/`max_salary_annual` are not.
    searcher, table = _searcher()
    searcher.run({"q": "x", "has_salary": "true"})
    assert "min_salary_annual IS NOT NULL" in table.last_where
    assert "salary IS NOT NULL" not in table.last_where


def test_run_projects_the_derived_salary_columns():
    row = dict(_ROW)
    row["min_salary_annual"] = 90000
    row["max_salary_annual"] = 110000
    row["salary_currency"] = "EUR"
    table = _Table([row])
    table.schema = types.SimpleNamespace(
        names=[
            *table.schema.names,
            "min_salary_annual",
            "max_salary_annual",
            "salary_currency",
        ]
    )
    rows = JobSearch(_Model(), table).run({"q": "x"})
    assert rows[0]["min_salary_annual"] == 90000
    assert rows[0]["max_salary_annual"] == 110000
    assert rows[0]["salary_currency"] == "EUR"


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


# ---- pagination (ADR-0074) ----


def test_default_page_is_one_offset_zero():
    # No `page` behaves exactly as before this feature existed — load-bearing for
    # `headstart.alerts.space_query`, which always requests k=100 and never sends `page`.
    searcher, table = _searcher()
    searcher.run({"q": "x"})
    assert table.last_offset == 0


def test_page_two_offsets_by_one_page_size():
    searcher, table = _searcher()
    searcher.run({"q": "x", "k": "20", "page": "2"})
    assert table.last_offset == 20


def test_page_is_capped_at_max_page():
    searcher, table = _searcher()
    searcher.run({"q": "x", "k": "20", "page": "9999"})
    assert table.last_offset == (20 - 1) * 20  # clamped to max_page=20, not 9998*20


def test_page_zero_or_negative_floors_to_one():
    searcher, table = _searcher()
    searcher.run({"q": "x", "k": "20", "page": "0"})
    assert table.last_offset == 0


def test_max_page_is_configurable_like_max_k():
    table = _Table([dict(_ROW)])
    searcher = JobSearch(_Model(), table, max_page=2)
    searcher.run({"q": "x", "k": "20", "page": "5"})
    assert table.last_offset == (2 - 1) * 20


def test_garbage_page_raises_valueerror():
    searcher, _ = _searcher()
    with pytest.raises(ValueError):
        searcher.run({"q": "x", "page": "many"})


def test_run_carries_the_job_id_for_starring():
    table = _Table([dict(_ROW, id="darwinbox:acme:abc")])
    assert JobSearch(_Model(), table).run({"q": "x"})[0]["id"] == "darwinbox:acme:abc"


def test_indexed_answers_which_ids_survive_and_escapes_quotes():
    # The Saved tab's "closed" check: ids come from stored records, so a quote must be
    # doubled before the where-clause, like every other filter term.
    table = _Table([{"ats": "darwinbox", "id": "a:b:1"}])
    searcher = JobSearch(_Model(), table)
    assert searcher.indexed(["a:b:1", "gone:x:9", "o'brien:x:1"]) == {"a:b:1"}
    assert "'o''brien:x:1'" in table.last_where


def test_indexed_skips_the_query_when_there_is_nothing_to_ask():
    searcher, table = _searcher()
    table.search = None  # any query attempt would now raise
    assert searcher.indexed([]) == set()
    assert searcher.indexed(["", ""]) == set()


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


def test_range_overflow_is_a_valueerror_not_a_500():
    # 9999-12-31 + 1 day overflows date; the route only turns ValueError into a 400.
    with pytest.raises(ValueError):
        _clause(posted_before="9999-12-31")
    with pytest.raises(ValueError):
        _clause(seen_before="9999-12-31")
