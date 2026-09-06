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
    kw.setdefault("has_min_salary_annual", True)
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


def test_like_metacharacters_are_escaped_so_a_term_matches_literally():
    r"""Quote doubling stops injection; this stops the quieter failure, a widened match.

    `%` and `_` are LIKE wildcards, so a user typing them got a pattern rather than the
    characters — measured on the served table: company "100%" returned 30 rows for the 1 that is
    right, location "new_york" 9,004 for 8. `\` is escaped for the same reason: DataFusion
    honours it as LIKE's escape character with no ESCAPE clause present, so "AT\T" matched
    "att" (698 rows).
    """
    assert _clause(company="100%") == r"lower(company) LIKE '%100\%%'"
    assert _clause(location="new_york") == r"lower(location) LIKE '%new\_york%'"
    assert _clause(company="AT\\T") == r"lower(company) LIKE '%at\\t%'"


def test_the_term_cap_lands_on_the_raw_term_so_it_cannot_split_an_escape_pair():
    r"""60 chars of what the user typed, *then* escaping — never a truncated `\x` pair.

    Order matters here, which is why it is pinned: capping after escaping can leave a trailing
    lone backslash, which escapes the pattern's own closing `%` and matches nothing at all
    (measured: 0 rows). A long term would become a silent zero-result filter.
    """
    assert (
        _clause(company="a" * 59 + "%b")
        == "lower(company) LIKE '%" + "a" * 59 + r"\%%'"
    )


def test_india_expands_via_geo():
    """The India control expands through the gazetteer rather than matching its value literally.

    Asserted as delegation to `geo.where` rather than by looking for a substring of the compiled
    clause: how that clause is built is `geo`'s business — it moved from 267 `LIKE`s to 10
    `regexp_like`s without changing a single matched row — and a test that reads its internals
    fails on that kind of change while catching none of what it is here to catch.
    """
    from headstart import geo

    clause = _clause(india="bengaluru")
    assert clause is not None
    assert clause == geo.where("bengaluru")
    assert _clause(india="not-a-place") is None


# ---- the Keyword filter (ADR-0104) ----


def test_keyword_defaults_to_the_title_scope():
    assert _clause(kw="kubernetes") == "(lower(title) LIKE '%kubernetes%')"


def test_keyword_terms_are_anded_and_each_may_land_in_any_scoped_column():
    clause = _clause(kw="Senior Kubernetes", kw_in="both", has_description=True)
    assert clause == (
        "(lower(title) LIKE '%senior%' OR lower(description) LIKE '%senior%') AND "
        "(lower(title) LIKE '%kubernetes%' OR lower(description) LIKE '%kubernetes%')"
    )


def test_keyword_description_scope_stays_dark_without_the_column():
    # Same dark-until-migrated rule as first_seen and the salary columns: a table that predates
    # the ADR-0104 column must not 500 on kw_in=description — it simply filters nothing.
    assert _clause(kw="rust", kw_in="description", has_description=False) is None
    # ...and `both` degrades to the column that IS there rather than to nothing.
    assert _clause(kw="rust", kw_in="both", has_description=False) == (
        "(lower(title) LIKE '%rust%')"
    )


def test_keyword_description_scope_compiles_once_the_column_exists():
    assert _clause(kw="rust", kw_in="description", has_description=True) == (
        "(lower(description) LIKE '%rust%')"
    )


def test_keyword_quotes_are_doubled_like_every_other_term():
    assert _clause(kw="O'Reilly") == "(lower(title) LIKE '%o''reilly%')"


def test_keyword_metacharacters_are_escaped_like_every_other_term():
    # The widening is worst here: unescaped, the keyword "c_" matched 199,591 of the served
    # table's 318,003 rows — 63% — where the literal reading matches 243.
    assert _clause(kw="c_ 100%", kw_in="both", has_description=True) == (
        r"(lower(title) LIKE '%c\_%' OR lower(description) LIKE '%c\_%') AND "
        r"(lower(title) LIKE '%100\%%' OR lower(description) LIKE '%100\%%')"
    )


def test_keyword_unknown_scope_compiles_to_nothing_at_the_builder():
    # Two layers, on purpose. The builder never interpolates a scope: an unknown one names no
    # columns and so compiles to no clause at all. The fall-back to `title` is the *parser's* job
    # (filter_kwargs whitelists `kw_in` against KEYWORD_SCOPES), pinned in the JobSearch tests
    # below — so garbage that somehow bypassed the parser still cannot reach the where-clause.
    assert _clause(kw="go", kw_in="'; DROP TABLE jobs; --") is None


def test_keyword_terms_are_capped():
    clause = _clause(kw="a b c d e f g")
    assert clause is not None and clause.count(" AND ") == 4  # five terms, not seven


def test_a_scope_without_a_keyword_filters_nothing():
    assert _clause(kw_in="description", has_description=True) is None


def test_keyword_scope_options_come_from_the_map_in_order_with_labels_and_needs():
    from headstart.search import KEYWORD_SCOPES, keyword_scope_options

    options = keyword_scope_options()
    assert [v for v, _, _ in options] == list(KEYWORD_SCOPES)  # same order as the map
    assert all(label for _, label, _ in options)  # every scope has a label
    # only scopes that name the optional column carry the disclaimer / disabled rule
    assert {v: needs for v, _, needs in options} == {
        "title": False,
        "description": True,
        "both": True,
    }


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
    schema = types.SimpleNamespace(
        names=["ats", "title", "first_seen", "min_salary_annual"]
    )

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


def test_keyword_filter_reaches_the_where_clause_and_its_scope_is_whitelisted():
    searcher, table = _searcher()
    searcher.run({"q": "x", "kw": "kubernetes", "kw_in": "nonsense"})
    assert (
        "(lower(title) LIKE '%kubernetes%')" in table.last_where
    )  # unknown scope -> title


def test_keyword_scope_alone_is_nulled_so_it_can_never_be_the_blocking_filter():
    searcher, _ = _searcher()
    parsed = searcher.filter_kwargs({"kw_in": "description"})
    assert parsed["kw"] is None and parsed["kw_in"] is None


def test_keyword_description_scope_is_learned_from_the_schema():
    searcher, table = _searcher()
    assert searcher.has_description == ("description" in table.schema.names)
    table.schema = types.SimpleNamespace(
        names=["ats", "title"]
    )  # no description column
    assert JobSearch(_Model(), table).has_description is False


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


def test_has_salary_stays_dark_without_the_column():
    # Mirrors test_first_seen_filters_stay_dark_without_the_column: a table LanceDB hasn't
    # migrated onto the ADR-0082 salary columns yet must not 500 on has_salary=true — the
    # feature stays dark, like every other optional-column filter in this file.
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(names=["ats", "title"])  # no salary columns
    searcher = JobSearch(_Model(), table)
    searcher.run({"q": "x", "has_salary": "true"})
    assert table.last_where is None


def test_run_projects_the_derived_salary_columns():
    row = dict(_ROW)
    row["min_salary_annual"] = 90000
    row["max_salary_annual"] = 110000
    row["salary_currency"] = "EUR"
    table = _Table([row])
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


def test_recency_window_overflow_is_a_valueerror_not_a_500():
    # Same treatment for the windows, which take an unbounded int: both the calendar bound
    # (739,864 days / 17,756,754 hours walks below year 1) and timedelta's own magnitude cap, in
    # both directions — a huge negative window runs off the far end of the calendar instead.
    for days in (740_000, 1_000_000_000, -3_000_000, -1_000_000_000):
        with pytest.raises(ValueError):
            _clause(posted_within=days)
    for hours in (17_800_000, 24_000_000_000, -70_000_000, -24_000_000_000):
        with pytest.raises(ValueError):
            _clause(seen_within=hours)


def test_recency_windows_still_compile_inside_the_calendar():
    # A CONTROL, not a regression test: it passes with the fix reverted too, and is here to pin
    # that the guard is the calendar's own bound rather than a policy about plausible windows —
    # an absurd but representable window must still compile. Both values keep ~86 years of slack
    # under the bound, which creeps forward with `now`, so neither is a dated test.
    assert "posted_at >= '" in _clause(posted_within=700_000)
    assert "first_seen >= '" in _clause(seen_within=17_000_000)


# ── the salary bracket and the sort control (issue #275) ─────────────────────────────────


def _bracket(**kwargs):
    base = {
        "atses": ["darwinbox"],
        "currencies": ["USD", "INR"],
        "has_first_seen": True,
        "has_min_salary_annual": True,
    }
    return build_filter(**{**base, **kwargs})


def test_salary_bracket_is_an_overlap_test_not_containment():
    # A 90k-140k posting answers "at least 100k", and a band wider than the user's still
    # qualifies — so the job's TOP clears the floor and its BOTTOM sits under the ceiling.
    where = _bracket(salary_currency="USD", salary_min=100_000, salary_max=200_000)
    assert "COALESCE(max_salary_annual, min_salary_annual) >= 100000" in where
    assert "min_salary_annual <= 200000" in where
    assert "salary_currency = 'USD'" in where


def test_salary_bracket_falls_back_to_the_single_figure_it_has():
    # `max_salary_annual` is null on a single-figure posting; COALESCE keeps that row in
    # play rather than dropping every job that quotes one number.
    assert "COALESCE(max_salary_annual, min_salary_annual)" in _bracket(
        salary_currency="USD", salary_min=100_000
    )


def test_currency_alone_does_not_filter():
    """Picking a currency with both bounds empty must not silently cut the result set.

    Only 28.5% of Jobs carry a salary at all (measured 2026-08-25), so treating the picker as
    a filter in its own right would drop ~71% of results for a click the user reads as
    "which currency should the bracket be in" — not "hide everything without a salary".
    """
    assert _bracket(salary_currency="USD") is None
    assert "salary_currency" in _bracket(salary_currency="USD", salary_min=1)


def test_a_bound_with_no_currency_defaults_to_usd():
    """A bracket with no currency named must still compile — it used to vanish entirely.

    The USD default ADR-0084 records lived only in the browser's <select>, so every other
    caller (the alerts path, `scripts/eval/verify_filters.py`, a hand-built
    `/search?salary_min=…`) had its numeric bound silently dropped and got the unfiltered set
    back, with no error and nothing for `facets._blocking` to name.
    """
    where = _bracket(salary_min=100_000)
    assert "salary_currency = 'USD'" in where
    assert "COALESCE(max_salary_annual, min_salary_annual) >= 100000" in where
    # ...and the default is still a *modifier*: with no bound to scope there is no bracket, so
    # nothing salary-shaped is compiled beside the filters the user did ask for (ADR-0084 —
    # filtering on the currency alone would cut the set to the 28.5% carrying any salary).
    assert _bracket(remote=True) == "remote = true"


def test_currency_is_whitelisted_against_the_table_never_interpolated():
    # Never interpolated: an unrecognised value falls back to the default rather than reaching
    # the clause — ADR-0084's "whitelisted like `ats`", and `ats` ignores what it does not know.
    # The bound it scopes still applies, which is the whole point of the default.
    assert (
        _bracket(salary_currency="'; DROP TABLE jobs; --", salary_min=1)
        == "salary_currency = 'USD' AND COALESCE(max_salary_annual, min_salary_annual) >= 1"
    )
    assert _bracket(salary_currency="XXX", salary_min=1) == _bracket(salary_min=1)
    # With no bound there is no bracket to scope, so a currency alone still compiles nothing —
    # ADR-0084's rule that picking one must not cut the result set to the ~28.5% carrying a salary.
    assert _bracket(salary_currency="XXX") is None


def test_the_bracket_stays_dark_where_even_the_default_is_unavailable():
    # `currencies` is empty until the ADR-0082 columns land, which makes every currency unknown
    # there — including `SALARY_DEFAULT_CURRENCY`. Emitting it anyway would be a clause matching
    # nothing: the same silent wrong answer the default exists to remove, just relocated.
    assert (
        build_filter(
            salary_currency="INR",
            salary_min=1,
            atses=[],
            currencies=[],
            has_first_seen=True,
            has_min_salary_annual=True,
        )
        is None
    )


def test_bracket_stays_dark_until_the_salary_columns_exist():
    # Same dark-until-migrated rule the rest of the salary path follows: a table LanceDB has
    # not synced onto the ADR-0082 columns would error on every query rather than just not
    # offering the feature.
    assert (
        build_filter(
            salary_currency="USD",
            salary_min=1,
            atses=[],
            currencies=["USD"],
            has_first_seen=True,
            has_min_salary_annual=False,
        )
        is None
    )


def test_sort_is_whitelisted_to_a_column():
    from headstart.search import SORT_COLUMNS

    assert SORT_COLUMNS == {"posted": "posted_at", "seen": "first_seen"}
    searcher, table = _searcher()
    searcher.run({"q": "", "sort": "; DROP TABLE jobs; --"})
    # unknown value == no sort at all, i.e. the ordinary browse ordering
    assert table.last_order[0]["column_name"] == "first_seen"


def test_sorting_by_posted_shape_guards_the_ordering():
    """`posted_at` is a raw per-ATS string and a non-ISO form sorts ABOVE every ISO date.

    Measured 2026-08-25 without this guard: a "newest posted" page led with '22-Jun-2026'
    above '2028-07-01'. The same guard `posted_within` already applies to the window has to
    apply to the ordering, or the top of the page is the one row nobody can parse.
    """
    searcher, table = _searcher()
    searcher.run({"q": "", "sort": "posted"})
    assert "posted_at LIKE '____-__-__%'" in table.last_where
    assert table.last_order == [
        {"column_name": "posted_at", "ascending": False, "nulls_first": False},
        {"column_name": "id", "ascending": True},
    ]


def test_sorting_by_seen_goes_dark_without_the_column():
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(names=["ats", "title"])
    JobSearch(_Model(), table).run({"q": "", "sort": "seen"})
    assert table.last_order == [{"column_name": "id", "ascending": True}]


def test_sorting_a_ranked_search_keeps_the_query_and_reorders_the_window():
    """The constraint this exists for: an `order_by` on the vector branch REPLACES similarity
    ranking rather than tie-breaking it, so sorting a search server-side would discard the
    query. Instead the whole addressable window (ADR-0074's `max_k * max_page`) is ranked,
    then re-ordered here."""
    rows = [
        {**_ROW, "id": "a", "posted_at": "2026-01-01"},
        {**_ROW, "id": "b", "posted_at": "2026-08-01"},
        {**_ROW, "id": "c", "posted_at": "2026-04-01"},
    ]
    table = _Table(rows)
    searcher = JobSearch(_Model(), table)
    out = searcher.run({"q": "backend", "sort": "posted", "k": "3"})
    assert [r["id"] for r in out] == ["b", "c", "a"]  # newest first
    assert (
        table.last_query is not None
    )  # the query still ran — ranking was not discarded
    assert table.last_order is None  # ...and no ORDER BY was pushed down to override it
    assert table.last_k == searcher.max_k * searcher.max_page  # the whole window


def test_sorting_a_ranked_search_still_paginates_without_repeating():
    rows = [
        {**_ROW, "id": c, "posted_at": f"2026-0{i + 1}-01"}
        for i, c in enumerate("abcd")
    ]
    searcher = JobSearch(_Model(), _Table(rows))
    first = searcher.run({"q": "backend", "sort": "posted", "k": "2", "page": "1"})
    second = searcher.run({"q": "backend", "sort": "posted", "k": "2", "page": "2"})
    assert [r["id"] for r in first] == ["d", "c"]
    assert [r["id"] for r in second] == ["b", "a"]
