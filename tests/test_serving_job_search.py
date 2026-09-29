"""Tests for the serving path, `JobSearch` (ADR-0042).

`JobSearch` is exercised through its interface with a fake encoder and table — no model
load, so all of this runs in the standard test env. The where-clause builders it compiles
through are tested in `test_search_filters_compiler.py` (ADR-0194).
"""

from __future__ import annotations

import logging
import types
from contextlib import contextmanager
from dataclasses import replace

import pytest

from headstart.search_filters.compiler import account_clause, build_filter, with_extra
from headstart.serving.job_absence import WHY_NOT_SERVED
from headstart.serving.job_search import (
    FACET_CACHE_SIZE,
    QUERY_VECTOR_CACHE_SIZE,
    REQUIREMENTS_CATEGORY_WINDOW,
    RESULT_COLUMNS,
    SORT_COLUMNS,
    SORT_FLOOR,
    JobSearch,
    RoleAssignments,
    ScopeUnavailable,
    refusal,
    request_account_clause,
    scoped_jobs_clause,
)

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

    def nprobes(self, value):
        self._t.last_nprobes = value
        return self

    def refine_factor(self, value):
        self._t.last_refine_factor = value
        return self

    def select(self, cols):
        # lancedb raises `columns must be a list or a dictionary` on a tuple, and the browse
        # branch shipped one — green here, 500 in production, because this fake took anything.
        assert isinstance(cols, (list, dict)), f"lancedb rejects {type(cols).__name__}"
        self._t.last_select = list(cols)
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
        self.last_select = None
        self.last_nprobes = None
        self.last_refine_factor = None
        self.indices = []
        self.search_calls = 0

    def search(self, *args, **kwargs):
        self.search_calls += 1
        self.last_query = args[0] if args else None  # None => a browse, not a search
        return _Query(self)

    def list_indices(self):
        return self.indices

    def count_rows(self, filter=None):
        return len(self.rows)


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


def test_an_unknown_filter_value_is_warned_about_once_per_request(caplog):
    # The regression this pins is amplification, not wording: `facets.counts` recompiles one
    # request's filters once per facet option, so warning at the drop site in `build_filter`
    # turned a single `?ats=bogus&etype=bogus` into 58 WARNING records on the deployed Space —
    # user-controlled, unauthenticated log volume from any crawler holding a stale link. The
    # parse happens once; the compile happens ~30 times, and only the parse may speak.
    searcher, _ = _searcher()
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        caplog.clear()  # the constructor's own dark-column line is not what is counted here
        filters = searcher.parse_filters({"ats": "bogus", "etype": "bogus"})
        for _ in range(30):
            build_filter(filters, searcher.capabilities)
    assert len(caplog.records) == 2
    assert filters.ats == "bogus"  # still dropped by the compiler, unchanged
    assert build_filter(filters, searcher.capabilities) is None


def test_title_words_are_parsed_trimmed_and_absent_when_blank():
    searcher, _ = _searcher()
    assert searcher.parse_filters({"title_words": "  staff rust "}).title_words == (
        "staff rust"
    )
    assert searcher.parse_filters({"title_words": "  "}).title_words is None


def test_an_unknown_india_place_is_warned_about_and_a_known_one_is_not(caplog):
    searcher, _ = _searcher()
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        caplog.clear()
        searcher.parse_filters({"india": "bengaluru"})
        searcher.parse_filters({"india": "india"})
        assert not caplog.records
        searcher.parse_filters({"india": "atlantis"})
    assert [r.getMessage() for r in caplog.records] == [
        "filter dropped: india 'atlantis' is not a known place"
    ]


def test_a_country_is_read_as_an_upper_case_code_and_an_unknown_one_is_warned_about(
    caplog,
):
    searcher, _ = _searcher()
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        caplog.clear()
        assert searcher.parse_filters({"country": " de "}).country == "DE"
        assert searcher.parse_filters({"country": "IN"}).country == "IN"
        assert searcher.parse_filters({"country": ""}).country is None
        assert not caplog.records
        searcher.parse_filters({"country": "zz"})
    assert [r.getMessage() for r in caplog.records] == [
        "filter dropped: country 'ZZ' is not a known code"
    ]


def test_boot_names_unmaterialized_flags_a_capped_whitelist_and_unpriced_currencies(
    caplog, monkeypatch
):
    from headstart.search_filters import fx
    from headstart.serving import job_search

    monkeypatch.setattr(job_search, "WHITELIST_SCAN_ROWS", 0)
    monkeypatch.setattr(fx, "table", lambda: {"rates": {"USD": 1.0}})
    table = _priced(_Table([{**_ROW, "salary_currency": "XYZ"}]))
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        JobSearch(_Model(), table)
    lines = [r.getMessage() for r in caplog.records]
    assert "slow path (unmaterialized): employment_type flags" in lines[0]
    assert lines[1].startswith("ats/currency whitelists read 0 of 1 rows")
    assert lines[2] == "served currencies with no fx rate: XYZ"


def test_a_slow_search_is_named_by_shape_never_by_query(caplog, monkeypatch):
    from headstart.serving import job_search

    monkeypatch.setattr(job_search, "SLOW_SEARCH_MS", -1)
    searcher, _ = _searcher()
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        caplog.clear()
        searcher.run({"q": "secret words"})
    (line,) = [r.getMessage() for r in caplog.records]
    assert line.startswith("slow search ") and "query=True" in line
    assert "path=ranked encode_ms=" in line and "indexed=False" in line
    assert "secret" not in line


def test_a_bracket_re_scoped_or_dropped_by_currency_is_said_once(caplog):
    searcher, _ = _searcher()
    for currencies, tail in (
        (["USD", "INR"], "bracket uses USD"),
        (["INR"], "USD not served either, bracket dropped"),
    ):
        caps = replace(
            searcher.capabilities, currencies=currencies, has_min_salary_annual=True
        )
        searcher.capabilities = caps
        with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
            caplog.clear()
            searcher.parse_filters({"salary_currency": "INR"})  # no bound: no bracket
            searcher.parse_filters({"salary_min": "5", "salary_currency": "INR"})
            assert not caplog.records
            searcher.parse_filters({"salary_min": "5", "salary_currency": "xyz"})
        assert [r.getMessage() for r in caplog.records] == [
            f"filter re-scoped: salary_currency 'XYZ' not served; {tail}"
        ]
        if "dropped" in tail:
            filters = searcher.parse_filters(
                {"salary_min": "5", "salary_currency": "x"}
            )
            assert build_filter(filters, caps) is None  # the line tells the truth


def test_an_unknown_keyword_scope_or_sort_is_warned_about(caplog):
    searcher, _ = _searcher()
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        caplog.clear()
        searcher.parse_filters({"kw_in": "bogus"})  # no keyword: the scope is moot
        searcher.parse_filters({"kw": "go", "kw_in": "title", "sort": "posted"})
        assert not caplog.records
        searcher.parse_filters({"kw": "go", "kw_in": "bogus", "sort": "bogus"})
    assert [r.getMessage() for r in caplog.records] == [
        "filter re-scoped: kw_in 'bogus' is not a known scope; title used",
        "sort dropped: 'bogus' is not a known sort; default order",
    ]


def test_facets_cache_the_filter_set_not_the_semantic_query(monkeypatch):
    from headstart.serving import facets

    calls = []

    def counted(
        _table,
        filters,
        _capabilities,
        *,
        extra_where=None,
        only_total=False,
        table_where=None,
    ):
        calls.append((filters, extra_where))
        return {"total": len(calls), "facets": {}}

    monkeypatch.setattr(facets, "counts", counted)
    searcher, _ = _searcher()
    first = searcher.facets({"q": "backend", "remote": "true"})
    second = searcher.facets({"q": "frontend", "remote": "true"})
    assert first is second
    assert len(calls) == 1

    for n in range(FACET_CACHE_SIZE + 1):
        searcher.facets({"location": f"place-{n}"})
    assert len(searcher._facet_cache) == FACET_CACHE_SIZE


def test_facet_cache_keeps_account_clauses_separate(monkeypatch):
    from headstart.serving import facets

    calls = []

    def counted(
        _table,
        _filters,
        _capabilities,
        *,
        extra_where=None,
        only_total=False,
        table_where=None,
    ):
        calls.append(extra_where)
        return {"total": len(calls), "facets": {}}

    monkeypatch.setattr(facets, "counts", counted)
    searcher, _ = _searcher()
    first = searcher.facets({}, extra_where="account = 1")
    second = searcher.facets({}, extra_where="account = 2")
    assert first is not second
    assert calls == ["account = 1", "account = 2"]


def test_counts_total_asks_for_the_total_alone_and_is_cached_apart(monkeypatch):
    """ADR-0274: an agent printing only the total sends `counts=total`; the page never does."""
    from headstart.serving import facets

    calls = []

    def counted(
        _table,
        _filters,
        _capabilities,
        *,
        extra_where=None,
        only_total=False,
        table_where=None,
    ):
        calls.append(only_total)
        return {"total": 1, "facets": {} if only_total else {"remote": []}}

    monkeypatch.setattr(facets, "counts", counted)
    searcher, _ = _searcher()
    total = searcher.facets({"remote": "true", "counts": "total"})
    full = searcher.facets({"remote": "true"})
    assert searcher.facets({"remote": "true", "counts": "total"}) is total
    assert searcher.facets({"remote": "true", "counts": "all"}) is full
    assert calls == [True, False]
    with pytest.raises(
        ValueError, match="counts 'some' is not known; known: all, total"
    ):
        searcher.facets({"counts": "some"})


def test_facet_cache_expires_so_recency_counts_keep_moving(monkeypatch):
    from headstart.serving import facets, job_search

    now = [100.0]
    calls = []
    monkeypatch.setattr(job_search.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        facets,
        "counts",
        lambda *_args, **_kwargs: (
            calls.append(now[0]) or {"total": len(calls), "facets": {}}
        ),
    )
    searcher, _ = _searcher()
    first = searcher.facets({})
    now[0] += 61
    second = searcher.facets({})
    assert first != second
    assert calls == [100.0, 161.0]


def test_empty_query_pages_are_cached_and_expire(monkeypatch):
    from headstart.serving import job_search

    now = [100.0]
    monkeypatch.setattr(job_search.time, "monotonic", lambda: now[0])
    searcher, table = _searcher()
    table.search_calls = 0  # ignore constructor capability scans
    first = searcher.run({})
    second = searcher.run({"q": ""})
    assert first is second
    assert table.search_calls == 1

    now[0] += 61
    third = searcher.run({})
    assert third is not second
    assert table.search_calls == 2


def test_semantic_vector_is_reused_across_filter_and_page_changes():
    class CountingModel(_Model):
        def __init__(self):
            self.calls = 0

        def encode(self, texts, normalize_embeddings=False):
            self.calls += 1
            return super().encode(texts, normalize_embeddings)

    model = CountingModel()
    table = _Table([dict(_ROW)])
    searcher = JobSearch(model, table)
    table.search_calls = 0
    searcher.run({"q": "backend engineer", "remote": "true"})
    searcher.run({"q": "backend engineer", "page": "2"})
    assert model.calls == 1
    assert table.search_calls == 2

    for n in range(QUERY_VECTOR_CACHE_SIZE + 1):
        searcher.run({"q": f"query-{n}"})
    assert len(searcher._query_vector_cache) == QUERY_VECTOR_CACHE_SIZE


def test_warm_uses_the_same_normalized_key_as_the_first_browser_request(monkeypatch):
    from headstart.serving import facets

    monkeypatch.setattr(
        facets, "counts", lambda *_args, **_kwargs: {"total": 1, "facets": {}}
    )
    searcher, table = _searcher()
    table.search_calls = 0
    searcher.warm()
    assert table.search_calls == 2
    searcher.run({"q": "", "k": "20", "page": "1"})
    assert table.search_calls == 2


def test_startup_scan_learns_atses_and_first_seen():
    searcher, _ = _searcher()
    assert searcher.capabilities.atses == ["darwinbox"]
    assert searcher.capabilities.has_first_seen is True


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


def test_canonical_url_rewrites_match_the_scrapers_own_url_shape():
    """Ties ``_canonical_url``'s two hardcoded rewrites to the scrapers they repair for (ADR-0157).

    The HF Space has no ``curl_cffi``, so ``headstart.scrapers`` does not import there (see
    ``_canonical_url``'s own docstring), and the
    rewrites can't call through to ``DarwinboxScraper``/``RecruiteeScraper`` at runtime — this
    test is the structural check instead: it runs here, in the repo, where both modules are
    importable, and fails if either scraper's declared shape and this function's repaired
    output ever disagree.
    """
    import re

    from headstart.scrapers.darwinbox import DarwinboxScraper
    from headstart.scrapers.recruitee import RecruiteeScraper
    from headstart.serving.job_search import _canonical_url

    darwinbox_repaired = _canonical_url(
        "darwinbox",
        "https://x.darwinbox.in/ms/candidate/careers/jobs/5ebea18409d3e",
        "darwinbox:x:5ebea18409d3e",
    )
    assert re.fullmatch(DarwinboxScraper.url_shape, darwinbox_repaired)

    recruitee_repaired = _canonical_url(
        "recruitee",
        "https://transperfect.com/o/software-engineer-net-c-1",
        "recruitee:transperfect:2141029",
    )
    assert re.fullmatch(RecruiteeScraper.url_shape, recruitee_repaired)


def test_recruitee_rewrite_leaves_alone_what_it_cannot_rebuild():
    from headstart.serving.job_search import _rehost_recruitee

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
        "(regexp_like(title, '(?i)(^|[^a-z0-9])kubernetes'))" in table.last_where
    )  # unknown scope -> title


def test_keyword_scope_alone_is_nulled_so_it_can_never_be_the_blocking_filter():
    searcher, _ = _searcher()
    parsed = searcher.parse_filters({"kw_in": "description"})
    assert parsed.kw is None and parsed.kw_in is None


def test_keyword_description_scope_is_learned_from_the_schema():
    searcher, table = _searcher()
    assert searcher.capabilities.has_description == (
        "description" in table.schema.names
    )
    table.schema = types.SimpleNamespace(
        names=["ats", "title"]
    )  # no description column
    assert JobSearch(_Model(), table).capabilities.has_description is False


def test_has_country_is_learned_from_the_schema():
    """ADR-0138, the same dark-until-migrated rule `has_description` follows above."""
    searcher, table = _searcher()
    assert searcher.capabilities.has_country == ("country" in table.schema.names)
    table.schema = types.SimpleNamespace(names=["ats", "title"])  # no country column
    assert JobSearch(_Model(), table).capabilities.has_country is False
    table.schema = types.SimpleNamespace(names=["ats", "title", "country"])
    assert JobSearch(_Model(), table).capabilities.has_country is True


def test_employment_type_flags_are_learned_only_once_the_whole_migration_lands():
    _, table = _searcher()
    table.schema = types.SimpleNamespace(
        names=[
            "ats",
            "title",
            "is_full_time",
            "is_part_time",
            "is_contract",
            "is_internship",
        ]
    )
    assert JobSearch(_Model(), table).capabilities.has_employment_type_flags is True
    table.schema = types.SimpleNamespace(
        names=["ats", "title", "is_full_time", "is_part_time", "is_contract"]
    )
    assert JobSearch(_Model(), table).capabilities.has_employment_type_flags is False


def test_ann_tuning_is_applied_only_when_the_table_has_a_vector_index():
    searcher, table = _searcher()
    searcher.run({"q": "backend"})
    assert table.last_nprobes is None and table.last_refine_factor is None

    table.indices = [types.SimpleNamespace(columns=["vector"])]
    JobSearch(_Model(), table).run({"q": "backend"})
    assert table.last_nprobes == 80
    assert table.last_refine_factor == 2


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


def test_has_salary_search_uses_the_materialized_presence_flag():
    _, table = _searcher()
    table.schema = types.SimpleNamespace(
        names=["ats", "title", "min_salary_annual", "salary_known"]
    )
    searcher = JobSearch(_Model(), table)
    searcher.run({"q": "x", "has_salary": "true"})
    assert table.last_where == "salary_known = true"


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
    # `company` is in the base `_schema()`, so a row without it is a fake the real table can
    # never produce. (The constructor briefly read it, for a Board count since removed — the
    # field stays because a faithful fake is worth more than a minimal one.)
    table = _Table([{"ats": "darwinbox", "company": "acme", "id": "a:b:1"}])
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


def test_run_passes_ranges_and_rejects_garbage():
    searcher, table = _searcher()
    searcher.run({"q": "x", "posted_after": "2026-08-01", "seen_before": "2026-08-10"})
    assert "posted_at >= '2026-08-01'" in table.last_where
    assert "first_seen < '2026-08-11'" in table.last_where
    with pytest.raises(ValueError):
        searcher.run({"q": "x", "posted_after": "not-a-date"})


# ── the salary bracket and the sort control (issue #275) ─────────────────────────────────


def test_sort_is_whitelisted_to_a_column():
    from headstart.serving.job_search import SORT_COLUMNS

    assert SORT_COLUMNS == {
        "posted": "posted_at",
        "seen": "first_seen",
        "salary": "min_salary_annual",
    }
    searcher, table = _searcher()
    searcher.run({"q": "", "sort": "; DROP TABLE jobs; --"})
    # unknown value == no sort at all, i.e. the ordinary browse ordering
    assert table.last_order[0]["column_name"] == "first_seen"


def test_sorting_by_salary_orders_by_the_derived_column_with_nulls_last():
    """ "Highest salary" orders by `min_salary_annual`, the ADR-0082 column — so it reaches the
    description-mined figures too, not just the boards that publish a structured field. Unlike
    `posted` it needs no shape guard: the column is a number or NULL, and NULLs go last.
    """
    searcher, table = _searcher()
    searcher.run({"q": "", "sort": "salary"})
    assert table.last_order == [
        {"column_name": "min_salary_annual", "ascending": False, "nulls_first": False},
        {"column_name": "id", "ascending": True},
    ]
    assert "min_salary_annual" not in (table.last_where or "")


def test_sorting_a_ranked_window_by_salary_puts_the_unpriced_rows_last():
    """The ranked path re-orders its window in Python, and that is where a numeric sort column
    bites: `r.get(sort) or ""` would put a `str` in a tuple beside `float`s and raise TypeError
    on the first comparison between two rows. A row with no salary sorts last, matching the
    browse path's `nulls_first: False`.
    """
    rows = [
        {**_ROW, "id": "a", "min_salary_annual": 90_000.0},
        {**_ROW, "id": "b", "min_salary_annual": None},
        {**_ROW, "id": "c", "min_salary_annual": 250_000.0},
    ]
    table = _Table(rows)
    searcher = JobSearch(_Model(), table)
    out = searcher.run({"q": "backend", "sort": "salary", "k": "3"})
    assert [r["id"] for r in out] == ["c", "a", "b"]


def _priced(table):
    table.schema = types.SimpleNamespace(
        names=["ats", "title", "first_seen", "min_salary_annual", "salary_currency"]
    )
    return table


def test_a_ranked_salary_sort_compares_across_currencies_in_one(monkeypatch):
    """Salary is stored in the employer's own currency (ADR-0082), so ordering the raw number
    ranked ₹40,00,000 above $300,000 — on the 2026-09-15 served table the first 400
    salary-sorted rows were all INR. The ranked window restates each row in the sort's
    currency with the ADR-0117 rates; a currency with no rate cannot be compared, so it sorts
    with the unpriced rows rather than being taken 1:1.
    """
    from headstart.search_filters import fx

    monkeypatch.setattr(
        fx, "table", lambda: {"rates": {"USD": 1.0, "INR": 80.0, "EUR": 0.9}}
    )
    rows = [
        {
            **_ROW,
            "id": "inr",
            "min_salary_annual": 4_000_000.0,
            "salary_currency": "INR",
        },
        {**_ROW, "id": "usd", "min_salary_annual": 300_000.0, "salary_currency": "USD"},
        {**_ROW, "id": "eur", "min_salary_annual": 90_000.0, "salary_currency": "EUR"},
        {**_ROW, "id": "xyz", "min_salary_annual": 9e9, "salary_currency": "XYZ"},
        {**_ROW, "id": "b-none", "min_salary_annual": None, "salary_currency": None},
    ]
    searcher = JobSearch(_Model(), _priced(_Table(rows)))
    out = searcher.run({"q": "backend", "sort": "salary", "k": "5"})
    assert [r["id"] for r in out] == ["usd", "eur", "inr", "xyz", "b-none"]


class _SegmentQuery(_Query):
    """Answers the two browse segments the way LanceDB would: filtered, ordered, paged."""

    def __init__(self, table):
        super().__init__(table)
        self._where, self._limit, self._offset = None, None, 0

    def where(self, clause, prefilter=False):
        self._where = clause
        self._t.segments.append(clause)
        return super().where(clause, prefilter)

    def limit(self, k):
        self._limit = k
        return super().limit(k)

    def offset(self, off):
        self._offset = off
        return super().offset(off)

    def to_list(self):
        if self._where is None:  # the constructor's own whitelist scans
            return list(self._t.rows)
        own = "salary_currency = 'USD'" in self._where
        rows = self._t.own if own else self._t.rest
        return rows[self._offset : self._offset + self._limit]


class _SegmentTable(_Table):
    def __init__(self, own, rest):
        super().__init__(own + rest)
        self.own, self.rest, self.segments = own, rest, []

    def count_rows(self, filter=None):
        return (
            len(self.own)
            if "salary_currency = 'USD'" in (filter or "")
            else len(self.rows)
        )

    def search(self, *args, **kwargs):
        super().search(*args, **kwargs)
        return _SegmentQuery(self)


def test_a_salary_browse_lists_the_sort_currency_first_then_the_rest_by_currency():
    """A browse is ordered by LanceDB over the whole table, which can only ORDER BY a stored
    column — so it cannot compare across currencies. It lists the sort currency's Jobs first,
    in true salary order, then every other Job grouped by currency (each group in its own true
    order) and the unpriced last. The result SET is unchanged, so the facet total still
    describes it; scoping to one currency instead emptied an India browse sorted in USD.
    """
    usd = [
        {**_ROW, "id": f"u{i}", "min_salary_annual": 1e5, "salary_currency": "USD"}
        for i in range(3)
    ]
    rest = [
        {**_ROW, "id": f"r{i}", "min_salary_annual": 4e6, "salary_currency": "INR"}
        for i in range(2)
    ]
    table = _priced(_SegmentTable(usd, rest))
    searcher = JobSearch(_Model(), table)

    out = searcher.run({"q": "", "sort": "salary", "k": "2", "page": "2"})

    assert [r["id"] for r in out] == ["u2", "r0"]  # the page straddles the two segments
    own_clause, rest_clause = table.segments[-2:]
    assert own_clause == "salary_currency = 'USD'"
    assert rest_clause == "(salary_currency IS NULL OR salary_currency <> 'USD')"
    assert table.last_order[0] == {
        "column_name": "salary_currency",
        "ascending": True,
        "nulls_first": False,
    }


def test_the_salary_sort_follows_the_pickers_currency():
    """The currency picker is sent beside a salary sort even with no bound set, and the sort is
    stated in it — an India browse sorted in INR lists INR first. An unknown one falls back to
    SALARY_DEFAULT_CURRENCY, exactly like the bracket, since it reaches a where-clause.
    """
    usd = [{**_ROW, "id": "u0", "min_salary_annual": 1e5, "salary_currency": "USD"}]
    inr = [{**_ROW, "id": "i0", "min_salary_annual": 4e6, "salary_currency": "INR"}]
    table = _priced(_SegmentTable(usd, inr))
    searcher = JobSearch(_Model(), table)

    searcher.run({"q": "", "sort": "salary", "salary_currency": "inr"})
    assert "salary_currency = 'INR'" in table.segments

    searcher.run({"q": "", "sort": "salary", "salary_currency": "'; --"})
    assert "salary_currency = 'USD'" in table.segments


def test_the_salary_sort_is_dark_until_the_column_exists():
    """Same dark-until-migrated rule the first-seen sort follows (ADR-0031): the ADR-0082
    columns arrive by migration, and `order_by` on a column the table lacks fails planning.
    """
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(
        names=["id", "ats", "title", "url", "posted_at"]
    )
    searcher = JobSearch(_Model(), table)
    searcher.run({"q": "", "sort": "salary"})
    assert table.last_order == [{"column_name": "id", "ascending": True}]


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


def test_a_sorted_search_reorders_only_the_rows_above_the_floor():
    """cs03 of the round-3 critique: the 2,000-row window reached 0.55 under narrow filters,
    and a date sort led with a Database Administrator for "junior data analyst" (ADR-0338).
    A browse or a relevance order keeps every row."""
    below = 1 - SORT_FLOOR + 0.01
    rows = [
        {**_ROW, "id": "close-old", "posted_at": "2026-01-01"},
        {**_ROW, "id": "far-new", "posted_at": "2026-09-01", "_distance": below},
        {
            **_ROW,
            "id": "at-floor",
            "posted_at": "2026-05-01",
            "_distance": 1 - SORT_FLOOR,
        },
    ]
    searcher = JobSearch(_Model(), _Table(rows))
    out = searcher.run({"q": "data analyst", "sort": "posted", "k": "3"})
    assert [r["id"] for r in out] == ["at-floor", "close-old"]
    ranked = JobSearch(_Model(), _Table(rows)).run({"q": "data analyst", "k": "3"})
    assert len(ranked) == 3


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


# ── the served projection (ADR-0084's window, re-costed) ─────────────────────────────────────


def test_a_query_asks_only_for_the_columns_the_response_is_built_from():
    """The sorted-query path materialises 2,000 rows, so what each row carries is the cost.

    Without this the scan returned every column — above all the 768-float `vector`, which is the
    bulk of the payload — and threw it away one line later. `RESULT_COLUMNS` carries the measured
    figures; this test pins the behaviour rather than restating them. `_distance` is named
    explicitly because `score` is that value and lancedb warns its auto-projection "will change
    in the future".
    """
    searcher, table = _searcher()
    searcher.run({"q": "backend"})
    assert table.last_select is not None, "the scan asked for every column"
    assert "_distance" in table.last_select
    assert "vector" not in table.last_select
    assert set(table.last_select) - {"_distance"} == set(searcher.projection)


def test_a_browse_asks_for_the_projection_alone():
    """No vector means no `_distance`, and nothing else is needed either.

    An `order_by` over a projected scan plans fine so long as the ordering column is in the
    projection — see `test_every_sortable_column_is_projected`. A `_rowid` briefly lived here,
    justified by a planning error ("TakeExec requires the input plan to have a column named
    `_rowaddr` or `_rowid`") that a probe had produced only because *its* projection left the
    ordering column out.
    """
    searcher, table = _searcher()
    searcher.run({})
    assert table.last_select == list(searcher.projection)
    assert "_distance" not in table.last_select


def test_every_sortable_column_is_projected():
    """The invariant the browse path rests on: anything `run` can order by must be a column it
    also asked for. Leave one out and LanceDB fails planning outright rather than degrading, so
    this is what stands between a new sort option and a 500 on every browse."""
    # Asserted against RESULT_COLUMNS, not against a searcher built on the fake's toy schema:
    # filtering by that schema silently excused `posted_at` and `id`, and the test stayed green
    # with BOTH removed from the constant — while dropping `id` alone 500s every browse.
    orderable = set(SORT_COLUMNS.values()) | {"first_seen", "id"}
    assert orderable <= set(RESULT_COLUMNS), (
        f"orderable but not projected: {sorted(orderable - set(RESULT_COLUMNS))}"
    )


def test_the_projection_is_narrowed_to_columns_the_table_actually_has():
    """`select()` RAISES on a column the table lacks, and half of `RESULT_COLUMNS` arrive by
    migration (`first_seen`, the ADR-0082 salary columns). Naming one unconditionally would turn
    ADR-0031's dark-until-migrated rule into a 500 on every search, so the projection is
    intersected with the live schema once, at construction.
    """
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(names=["id", "ats", "title", "url"])
    searcher = JobSearch(_Model(), table)
    assert searcher.projection == ("id", "title", "ats", "url")
    searcher.run({"q": "x"})  # must not raise
    assert set(table.last_select) == {"id", "title", "ats", "url", "_distance"}


def test_the_response_reads_only_columns_the_projection_asked_for():
    """`run` projects `RESULT_COLUMNS` and then builds each row by name, and nothing binds the
    two lists. Several reads are direct indexes (`r["title"]`), so dropping a column from the
    projection is a runtime KeyError on every search rather than a missing field — and adding a
    field to the response without adding its column is the same failure. This walks the source
    of `run`'s response and asserts every name it reads was asked for.
    """
    import ast
    import inspect

    from headstart.serving.job_search import RESULT_COLUMNS, JobSearch

    tree = ast.parse(inspect.getsource(JobSearch.run).lstrip())
    read: set[str] = set()
    for node in ast.walk(tree):
        # r["title"] and r.get("location") — the two shapes the response uses
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.value, ast.Name)
            and node.value.id == "r"
        ):
            read.add(node.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "r"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            read.add(node.args[0].value)
    # `_distance` is supplied by the vector search itself, not stored, so it is not projected.
    missing = read - set(RESULT_COLUMNS) - {"_distance"}
    assert not missing, f"read from a row but never projected: {sorted(missing)}"


def test_the_two_sort_paths_break_ties_in_opposite_directions():
    """A wart, pinned rather than fixed: the same tie orders differently with and without a query.

    Browse and sort-without-query order `id` ASCENDING through lancedb. The query+sort path
    re-orders its window in Python with `reverse=True`, and that flag applies to the whole key —
    so `id` runs DESCENDING there. Both are stable, so ADR-0074's guarantee that a row cannot
    repeat or vanish across pages holds either way; only which of two tied rows comes first
    differs. Changing it would flip that order for real results, so it is recorded here to make
    the asymmetry visible and any future change deliberate.
    """
    import inspect

    from headstart.serving.job_search import JobSearch

    src = inspect.getsource(JobSearch.run)
    assert 'return (missing if value is None else value, r.get("id") or "")' in src
    assert "window.sort(key=key, reverse=True)" in src
    assert '{"column_name": "id", "ascending": True}' in src


# ── The salary bracket compares across currencies (ADR-0117) ──────────────────────────────


def test_extra_where_is_anded_onto_the_compiled_filter():
    """Account state (ADR-0171) rides alongside the filters, never instead of them.

    An `extra_where` that replaced the compiled clause would silently drop every control the
    user set; one that was ORed would widen the result rather than narrow it.
    """
    searcher, table = _searcher()
    searcher.run(
        {"q": "x", "remote": "true"}, extra_where="NOT (lower(id) LIKE 'lever:x:%')"
    )
    assert "remote = true" in table.last_where
    assert "NOT (lower(id) LIKE 'lever:x:%')" in table.last_where
    assert " AND " in table.last_where


def test_extra_where_alone_still_filters():
    """No controls set, so there is nothing to AND it onto — it must still reach the table."""
    searcher, table = _searcher()
    searcher.run({"q": "x"}, extra_where="(lower(id) LIKE 'lever:x:%')")
    assert table.last_where == "(lower(id) LIKE 'lever:x:%')"


def test_browse_cache_keeps_account_clauses_separate():
    searcher, table = _searcher()
    table.search_calls = 0
    searcher.run({}, extra_where="(lower(id) LIKE 'lever:x:%')")
    searcher.run({}, extra_where="(lower(id) LIKE 'lever:y:%')")
    assert table.search_calls == 2


def test_no_extra_where_leaves_the_clause_untouched():
    searcher, table = _searcher()
    searcher.run({"q": "x", "remote": "true"})
    assert "lower(id)" not in (table.last_where or "")


# ── what both adapters used to copy (ADR-0194) ──────────────────────────────────────────────


def test_request_account_clause_reads_mine_from_the_query_string():
    followed, hidden = ["greenhouse:acme"], ["lever:skip"]
    for mine in ("1", "true"):
        assert request_account_clause({"mine": mine}, followed, hidden) == (
            account_clause(followed, hidden, mine=True)
        )
    for args in ({}, {"mine": "0"}, {"mine": "yes"}):
        assert request_account_clause(args, followed, hidden) == (
            account_clause(followed, hidden, mine=False)
        )


def test_salary_bracket_converts_only_where_two_served_currencies_carry_a_rate(
    monkeypatch,
):
    from headstart.search_filters import fx

    searcher, _ = _searcher()
    monkeypatch.setattr(fx, "table", lambda: {"rates": {"USD": 1.0, "INR": 80.0}})
    for currencies, converts in ((["USD", "INR"], True), (["USD", "EUR"], False)):
        monkeypatch.setattr(
            searcher,
            "capabilities",
            replace(searcher.capabilities, currencies=currencies),
        )
        assert searcher.salary_bracket_converts is converts, currencies
    monkeypatch.setattr(fx, "table", lambda: None)
    assert searcher.salary_bracket_converts is False


def test_a_result_row_is_every_result_column_with_score_after_the_id():
    searcher, _ = _searcher()
    (row,) = searcher.run({})
    assert list(row) == ["id", "score", *[c for c in RESULT_COLUMNS if c != "id"]]
    # A column the table lacks reads as None rather than failing the whole page.
    assert row["salary_source"] is None


def test_a_category_hands_over_as_the_ids_trends_counted() -> None:
    """`family=` beside `board=` names the Boards' Jobs in that family, and nothing else."""
    from werkzeug.datastructures import MultiDict

    from headstart.serving.job_search import MAX_FAMILY_IDS, scoped_jobs_clause

    ids = {
        "ai-ml": sorted(
            ["google:careers.google.com:1", "Google:careers.google.com:2", "x:y:3"],
            key=str.lower,
        ),
        "devops": ["google:careers.google.com:4"],
    }
    args = MultiDict([("board", "google:careers.google.com"), ("family", "ai-ml")])
    assert scoped_jobs_clause(args, ids) == (
        "id IN ('google:careers.google.com:1', 'Google:careers.google.com:2')"
    )
    bare = MultiDict([("family", "ai-ml")])
    # Across the whole index a category is read from its family table (ADR-0322), not here.
    assert scoped_jobs_clause(bare, ids) is None, "only beside a company's Boards"
    other = MultiDict(
        [("board", "google:careers.google.com"), ("family", "data-science")]
    )
    assert scoped_jobs_clause(other, ids) == "id IN ('')"
    assert scoped_jobs_clause(args, None) is None, "no snapshot, no filter"
    quoted = {"ai-ml": ["b:o'k:1"]}
    q = MultiDict([("board", "b:o'k"), ("family", "ai-ml")])
    assert scoped_jobs_clause(q, quoted) == "id IN ('b:o''k:1')"
    many = {"ai-ml": [f"b:x:{i:05d}" for i in range(MAX_FAMILY_IDS + 1)]}
    with pytest.raises(ValueError):
        scoped_jobs_clause(MultiDict([("board", "b:x"), ("family", "ai-ml")]), many)


def test_a_tracked_role_hands_over_by_its_own_title_patterns() -> None:
    from werkzeug.datastructures import MultiDict

    from headstart.serving.job_search import scoped_jobs_clause

    patterns = {"watch:llm-genai": [r"\bLLM\b", r"\bGenAI\b"], "watch:odd": ["o'k"]}
    args = MultiDict([("board", "google:careers.google.com"), ("role", "llm-genai")])
    assert scoped_jobs_clause(args, None, patterns) == (
        r"regexp_like(title, '(?i)(?:\bLLM\b)|(?:\bGenAI\b)')"
    )
    odd = MultiDict([("board", "b:x"), ("role", "odd")])
    assert (
        scoped_jobs_clause(odd, None, patterns) == "regexp_like(title, '(?i)(?:o''k)')"
    )
    unknown = MultiDict([("board", "b:x"), ("role", "nope")])
    assert scoped_jobs_clause(unknown, None, patterns) is None


def test_a_hand_off_that_widens_or_empties_says_so(caplog) -> None:
    from werkzeug.datastructures import MultiDict

    from headstart.serving.job_search import MAX_FAMILY_IDS, scoped_jobs_clause

    board = ("board", "b:x")
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        scoped_jobs_clause(MultiDict([board, ("role", "nope")]), None, {})
        scoped_jobs_clause(
            MultiDict([board, ("role", "nope")]), None, {"watch:odd": ["x"]}
        )
        scoped_jobs_clause(MultiDict([board, ("family", "ai-ml")]), None)
        scoped_jobs_clause(MultiDict([board, ("family", "nope")]), {"ai-ml": []})
        scoped_jobs_clause(MultiDict([board, ("family", "ai-ml")]), {"ai-ml": []})
        scoped_jobs_clause(MultiDict([("family", "ai-ml")]), {"ai-ml": []})
        scoped_jobs_clause(MultiDict([("role", "odd")]), None, {"watch:odd": ["x"]})
        with pytest.raises(ValueError):
            scoped_jobs_clause(
                MultiDict([board, ("family", "ai-ml")]),
                {"ai-ml": [f"b:x:{i}" for i in range(MAX_FAMILY_IDS + 1)]},
            )
    assert [r.getMessage() for r in caplog.records] == [
        "scope widened: role 'nope' asked with no watchlist loaded; whole Board served",
        "scope widened: role 'nope' has no watch pattern; whole Board served",
        (
            "scope widened: family 'ai-ml' asked with no role assignments loaded; "
            "whole Board served"
        ),
        "family 'nope' is not a known family; zero results",
        "scope widened: role= given without board=; ignored",
        f"category hand-off refused: {MAX_FAMILY_IDS + 1} ids > {MAX_FAMILY_IDS}",
    ]


def test_a_missing_role_assignment_snapshot_is_named_at_boot(caplog, tmp_path) -> None:
    from headstart.serving.job_search import load_family_ids

    missing = tmp_path / "role_assignments.parquet"
    with caplog.at_level(logging.WARNING, logger="headstart.serving.job_search"):
        assert load_family_ids(missing) is None
    assert str(missing) in caplog.records[0].getMessage()


# ---- strict=1: refuse what would otherwise be dropped, re-scoped or widened (ADR-0253) ----


def _strict_searcher(currencies=("INR",)):
    """A searcher whose table serves one ATS (darwinbox) and only the given currencies."""
    searcher, table = _searcher()
    searcher.capabilities = replace(searcher.capabilities, currencies=list(currencies))
    return searcher, table


@pytest.mark.parametrize(
    ("args", "named"),
    [
        ({"ats": "workdya"}, ["'workdya'", "darwinbox"]),
        ({"etype": "gig"}, ["'gig'", "full-time, part-time, contract, internship"]),
        ({"india": "atlantis"}, ["'atlantis'", "india, delhi ncr", "bengaluru"]),
        ({"country": "uk"}, ["'UK'", "ISO 3166-1 alpha-2", "US, IN, GB"]),
        ({"kw": "go", "kw_in": "body"}, ["'body'", "title, description, both"]),
        ({"kw_in": "body"}, ["'body'"]),  # refused with or without a keyword to scope
        ({"sort": "newest"}, ["'newest'", "posted, seen, salary"]),
        ({"salary_min": "5", "salary_currency": "XYZ"}, ["'XYZ'", "it serves: INR"]),
        # no currency asked: the bracket is priced in USD, which this table does not serve
        ({"salary_max": "5"}, ["priced in USD", "it serves: INR"]),
    ],
)
def test_strict_refuses_a_value_outside_its_whitelist_naming_both(args, named):
    searcher, _ = _strict_searcher()
    for ask in (searcher.run, searcher.facets):
        with pytest.raises(ValueError) as refused:
            ask({**args, "strict": "1"})
        body, status = refusal(refused.value)
        assert status == 400 and body["error"] == "invalid filter"
        assert all(part in body["detail"] for part in named), body["detail"]
    searcher.run(args)  # without strict: dropped or re-scoped, as before, never refused


def test_strict_refuses_a_salary_sort_in_a_currency_the_table_does_not_serve():
    searcher, _ = _strict_searcher()
    for args, named in (
        ({"sort": "salary", "salary_currency": "XYZ"}, "'XYZ'"),
        ({"sort": "salary"}, "a salary sort with no salary_currency is priced in USD"),
    ):
        with pytest.raises(ValueError) as refused:
            searcher.run({**args, "strict": "1"})
        body, status = refusal(refused.value)
        assert status == 400 and named in body["detail"], body["detail"]
        searcher.run(args)  # without strict: USD, else the raw order, as before
    searcher.run({"sort": "salary", "salary_currency": "INR", "strict": "1"})


def test_strict_accepts_what_the_table_serves():
    searcher, table = _strict_searcher(currencies=("INR", "USD"))
    searcher.run(
        {
            "ats": "darwinbox",
            "etype": "full-time",
            "india": "bengaluru",
            "country": "IN",
            "kw": "go",
            "kw_in": "title",
            "salary_min": "5",
            "sort": "posted",
            "strict": "1",
        }
    )
    assert "darwinbox" in table.last_where


def test_strict_refuses_a_parameter_neither_route_reads_naming_it():
    """Round-3 critique P2-8 (ADR-0334): `/facets?strict=1&bogus_param=x` counted the whole
    index, so a parameter renamed on one side would drop its filter silently."""
    from werkzeug.datastructures import MultiDict

    searcher, _ = _strict_searcher()
    for ask in (searcher.run, searcher.facets):
        for args, named in (
            ({"bogus_param": "x"}, "unknown parameter 'bogus_param'"),
            ({"india_place": "pune", "kw": "go"}, "unknown parameter 'india_place'"),
            ({"b": "1", "a": "2"}, "unknown parameters 'a', 'b'"),
        ):
            with pytest.raises(ValueError) as refused:
                ask({**args, "strict": "1"})
            body, status = refusal(refused.value)
            assert status == 400 and named in body["detail"], body["detail"]
            assert "known: " in body["detail"] and "salary_min" in body["detail"]
            searcher.run(args)  # without strict: ignored, as before
        # A repeated parameter, as `board=` is, is one name.
        ask(MultiDict([("strict", "1"), ("board", "a"), ("board", "b"), ("k", "5")]))


def test_every_parameter_the_search_module_reads_is_one_strict_knows():
    """A parameter added to the parser but not to `REQUEST_PARAMETERS` would be refused under
    `strict=1`, which is every agent's call: this names it in the PR that adds it."""
    import re
    from pathlib import Path

    from headstart.serving import job_search

    source = Path(job_search.__file__).read_text(encoding="utf-8")
    read = set(re.findall(r'args\.get(?:list)?\("([a-z_]+)"', source))
    read |= set(re.findall(r'_int\("([a-z_]+)"\)', source))
    assert read and read <= job_search.REQUEST_PARAMETERS, (
        read - job_search.REQUEST_PARAMETERS
    )


@pytest.mark.parametrize(
    ("args", "column"),
    [
        ({"kw": "go", "kw_in": "description"}, "description"),
        (
            {"kw": "go", "kw_in": "both"},
            "description",
        ),  # would narrow to the title alone
        ({"has_salary": "true"}, "min_salary_annual"),
        ({"salary_min": "5", "salary_currency": "USD"}, "min_salary_annual"),
        ({"seen_within": "24"}, "first_seen"),
        ({"first_seen_after": "2026-09-01T00:00:00"}, "first_seen"),
        ({"seen_after": "2026-09-01"}, "first_seen"),
        ({"seen_before": "2026-09-01"}, "first_seen"),
        ({"max_age_days": "365"}, "first_seen"),  # would read the posted date alone
        ({"sort": "seen"}, "first_seen"),
        ({"sort": "salary"}, "min_salary_annual"),
    ],
)
def test_strict_refuses_a_filter_or_sort_on_a_column_the_table_lacks(args, column):
    table = _Table([dict(_ROW)])
    table.schema = types.SimpleNamespace(names=["ats", "title"])
    searcher = JobSearch(_Model(), table)
    asks = [searcher.run] if "sort" in args else [searcher.run, searcher.facets]
    for ask in asks:
        with pytest.raises(ScopeUnavailable) as refused:
            ask({**args, "strict": "1"})
        body, status = refusal(refused.value)
        assert status == 503 and body == {"error": str(refused.value)}
        assert f"{column} column" in body["error"]
    searcher.run(args)  # without strict: dark, as before


_WATCHED = {"watch:llm-genai": [r"\bLLM\b"]}
_CONFIGURED = {"ai-ml", "security"}


def _hand_off(pairs, family_ids, watch=_WATCHED, strict=True):
    from werkzeug.datastructures import MultiDict

    return scoped_jobs_clause(
        MultiDict([*pairs, *([("strict", "1")] if strict else [])]),
        family_ids,
        watch,
        known_families=_CONFIGURED,
    )


@pytest.mark.parametrize(
    ("pairs", "family_ids", "watch", "status", "named"),
    [
        # Across the whole index (ADR-0322) a family is checked as beside a Board.
        ([("family", "nonsense")], {"ai-ml": []}, _WATCHED, 400, "'nonsense'"),
        ([("family", "ai-ml")], None, _WATCHED, 503, "role assignments"),
        ([("role", "llm-genai")], None, _WATCHED, 400, "role= needs board="),
        (
            [("board", "b:x"), ("family", "ai-ml"), ("role", "llm-genai")],
            {"ai-ml": []},
            _WATCHED,
            400,
            "send one of them",
        ),
        ([("board", "b:x"), ("role", "nope")], None, _WATCHED, 400, "'nope'"),
        (
            [("board", "b:x"), ("family", "nonsense")],
            {"ai-ml": []},
            _WATCHED,
            400,
            "'nonsense'",
        ),
        (
            [("board", "b:x"), ("family", "ai-ml")],
            None,
            _WATCHED,
            503,
            "role assignments",
        ),
        ([("board", "b:x"), ("role", "llm-genai")], None, {}, 503, "role watchlist"),
    ],
)
def test_strict_refuses_a_hand_off_it_would_ignore_or_widen(
    pairs, family_ids, watch, status, named
):
    with pytest.raises((ValueError, ScopeUnavailable)) as refused:
        _hand_off(pairs, family_ids, watch)
    body, answered = refusal(refused.value)
    assert answered == status
    assert named in (body.get("detail") or body["error"])
    # Without strict the same hand-off is served as it always was, never refused.
    _hand_off(pairs, family_ids, watch, strict=False)


def test_strict_reads_no_family_taxonomy_as_the_deployments_state_not_the_callers():
    """A missing config/role_families.json leaves nothing configured: every family would read
    as a typo, so it is the 503 of a deployment that cannot check one."""
    from werkzeug.datastructures import MultiDict

    args = MultiDict([("board", "b:x"), ("family", "ai-ml"), ("strict", "1")])
    with pytest.raises(ScopeUnavailable, match="family taxonomy"):
        scoped_jobs_clause(args, {"ai-ml": []}, _WATCHED, known_families=())


def test_strict_names_the_accepted_roles_and_families():
    for pairs, accepted in (
        ([("board", "b:x"), ("role", "nope")], "watched roles: llm-genai"),
        ([("board", "b:x"), ("family", "nonsense")], "configured: ai-ml, security"),
    ):
        with pytest.raises(ValueError, match=accepted):
            _hand_off(pairs, {"ai-ml": []})


def test_strict_serves_a_configured_family_with_no_jobs_assigned_as_zero_rows():
    """The classifier's warm-up: a family the taxonomy names but no Job carries yet is an
    empty answer, not an error, so strict only refuses a name nothing configures."""
    ids = {"ai-ml": ["b:x:1"]}
    assert _hand_off([("board", "b:x"), ("family", "security")], ids) == "id IN ('')"
    assert _hand_off([("board", "b:x"), ("family", "ai-ml")], ids) == "id IN ('b:x:1')"
    assert _hand_off([("board", "b:x"), ("role", "llm-genai")], None) == (
        r"regexp_like(title, '(?i)(?:\bLLM\b)')"
    )


def test_a_refusal_is_a_400_with_detail_or_a_503():
    assert refusal(ValueError("ats 'x' is not in this index")) == (
        {"error": "invalid filter", "detail": "ats 'x' is not in this index"},
        400,
    )
    assert refusal(ScopeUnavailable("no role assignments")) == (
        {"error": "no role assignments"},
        503,
    )


# ---- a Job read by id, and `like=` (ADR-0277) ----


def test_like_leaves_its_job_out_of_the_list_and_the_counts_alike(monkeypatch):
    """The page's total and its rows must describe one search: `like=`'s own Job is excluded by
    the same clause on both paths, quote doubled."""
    from headstart.serving import facets

    counted = []
    monkeypatch.setattr(
        facets,
        "counts",
        lambda _t, _f, _c, *, extra_where=None, **_: counted.append(extra_where) or {},
    )
    table = _Table([dict(_ROW, id="a:b:2", vector=[0.1, 0.2])])
    searcher = JobSearch(_Model(), table)
    args = {"like": "o'brien:x:1", "remote": "true"}
    searcher.facets(args, extra_where="account")
    searcher.run(args, extra_where="account")
    assert counted == ["(account) AND id <> 'o''brien:x:1'"]
    # the list narrowed by the very clause the counts were (ADR-0320 shares it)
    assert table.last_where == "(remote = true) AND (account) AND id <> 'o''brien:x:1'"


def test_like_ranks_by_the_stored_vector_and_never_encodes(monkeypatch):
    table = _Table([dict(_ROW, id="a:b:1", vector=[0.1, 0.2])])
    searcher = JobSearch(_Model(), table)
    monkeypatch.setattr(searcher, "_query_vector", None)  # any encode would raise
    (row,) = searcher.run({"like": "a:b:1"})
    assert table.last_query == [0.1, 0.2]
    assert row["score"] == 0.75


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({"like": "a:b:1", "q": "backend"}, "send one of them"),
        ({"like": "x" * 301}, "at most 300 characters"),
    ],
)
def test_like_is_refused_beside_a_query_or_past_the_id_bound(args, words):
    searcher, _ = _searcher()
    for path in (searcher.run, searcher.facets):
        with pytest.raises(ValueError, match=words):
            path(args)


@pytest.mark.parametrize(
    ("ids", "words"),
    [([], "1 to 5"), (["", ""], "1 to 5"), ([f"a:b:{n}" for n in range(6)], "1 to 5")],
)
def test_a_read_by_id_names_one_to_five_ids(ids, words):
    searcher, _ = _searcher()
    with pytest.raises(ValueError, match=words):
        searcher.jobs_by_id(ids)
    with pytest.raises(ValueError, match="at most 300"):
        searcher.jobs_by_id(["x" * 301])


def test_a_read_by_id_asks_for_the_detail_columns_the_table_has():
    table = _Table([dict(_ROW, id="a:b:1")])
    table.schema = types.SimpleNamespace(
        names=["ats", "title", "first_seen", "description", "department", "vector"]
    )
    JobSearch(_Model(), table).jobs_by_id(["a:b:1", "o'brien:x:1", "a:b:1"])
    assert table.last_where == "id IN ('a:b:1', 'o''brien:x:1')"
    assert table.last_select == [
        "title",
        "ats",
        "first_seen",
        "department",
        "description",
    ]
    assert table.last_k == 2


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """A real LanceDB table of five Jobs whose 4-dimensional vectors fall away from job 1 in
    order, so `like=` can be read against the exact ranking it must give."""
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    vectors = {
        "lever:acme:1": [1.0, 0.0, 0.0, 0.0],
        "lever:acme:2": [0.9, 0.1, 0.0, 0.0],
        "lever:acme:3": [0.7, 0.3, 0.0, 0.0],
        "lever:beta:4": [0.2, 0.8, 0.0, 0.0],
        "lever:o'brien:5": [0.0, 0.0, 1.0, 0.0],
    }
    rows = [
        {
            "id": job_id,
            "title": f"Engineer {n}",
            "company": "Acme",
            "location": "Berlin",
            "remote": n % 2 == 1,
            "ats": "lever",
            "first_seen": "2026-09-2{n}T00:00:00+00:00",
            "url": f"https://jobs.lever.co/acme/{n}",
            "department": "Engineering" if n == 1 else None,
            "experience": "3-5 years" if n == 1 else None,
            "min_years": 3 if n == 1 else None,
            "max_years": 5 if n == 1 else None,
            "description": ("x" * 12_500) if n == 1 else (None if n == 5 else "Short."),
            "description_stored": n != 5,
            "vector": vector,
        }
        for n, (job_id, vector) in enumerate(vectors.items(), start=1)
    ]
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("title", pa.string()),
            ("company", pa.string()),
            ("location", pa.string()),
            ("remote", pa.bool_()),
            ("ats", pa.string()),
            ("first_seen", pa.string()),
            ("url", pa.string()),
            ("department", pa.string()),
            ("experience", pa.string()),
            ("min_years", pa.int32()),
            ("max_years", pa.int32()),
            ("description", pa.string()),
            ("description_stored", pa.bool_()),
            ("vector", pa.list_(pa.float32(), 4)),
        ]
    )
    db = lancedb.connect(tmp_path_factory.mktemp("served"))
    table = db.create_table("jobs", data=pa.Table.from_pylist(rows, schema=schema))
    return JobSearch(_Model(), table)


def test_like_ranks_by_that_jobs_vector_leaving_it_out(served):
    rows = served.run({"like": "lever:acme:1"})
    assert [r["id"] for r in rows] == [
        "lever:acme:2",
        "lever:acme:3",
        "lever:beta:4",
        "lever:o'brien:5",
    ]
    assert rows[0]["score"] > rows[1]["score"] > rows[2]["score"]


def test_like_applies_every_filter_as_a_query_does(served):
    rows = served.run({"like": "lever:acme:1", "remote": "true"})
    assert [r["id"] for r in rows] == ["lever:acme:3", "lever:o'brien:5"]


def test_like_naming_no_served_job_says_why_it_may_be_gone(served):
    with pytest.raises(ValueError) as refused:
        served.run({"like": "lever:gone:9"})
    assert str(refused.value) == (
        "no job with id 'lever:gone:9' is in the index now. " + WHY_NOT_SERVED
    )


def test_a_read_by_id_serves_the_detail_and_cuts_a_long_description(served):
    found = served.jobs_by_id(["lever:acme:1", "lever:gone:9", "lever:o'brien:5"])
    assert set(found) == {"lever:acme:1", "lever:o'brien:5"}
    job = found["lever:acme:1"]
    assert (job["department"], job["experience"], job["max_years"]) == (
        "Engineering",
        "3-5 years",
        5,
    )
    assert len(job["description"]) == 12_000
    assert job["description_chars"] == 12_500 and job["description_cut"] is True
    assert "score" not in job and "vector" not in job
    bare = found["lever:o'brien:5"]
    assert bare["description"] is None and bare["description_chars"] == 0
    assert bare["description_stored"] is False and bare["description_cut"] is False


# ---- what a sample of Jobs asks for: /requirements (ADR-0324) ----

_FAMILIES = RoleAssignments(
    {
        "data-engineering": sorted(
            ["lever:acme:1", "lever:acme:2", "lever:beta:4"], key=str.lower
        ),
        "security": ["lever:acme:3"],
    },
    frozenset({"data-engineering", "security"}),
)


@pytest.fixture(scope="module")
def sampled(tmp_path_factory):
    """A real LanceDB table of five Jobs, three of them data engineering, whose vectors fall
    away from job 1 in order, each with a description naming skills and the columns the
    requirements counts read."""
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    vectors = [
        [1.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.7, 0.3, 0.0, 0.0],
        [0.2, 0.8, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ]
    ids = [
        "lever:acme:1",
        "lever:acme:2",
        "lever:acme:3",
        "lever:beta:4",
        "lever:gamma:5",
    ]
    rows = [
        {
            "id": job_id,
            "title": f"Engineer {n}",
            "company": job_id.split(":")[1].title(),
            "location": "Berlin, Germany" if n % 2 else "Austin, TX",
            "remote": n == 1,
            "ats": "lever",
            "first_seen": f"2026-09-2{n}T00:00:00+00:00",
            "min_years": n,
            "experience_source": "regex",
            "min_salary_annual": 100_000 * n,
            "max_salary_annual": None,
            "salary_currency": "USD",
            "description": "Python, SQL and Spark on AWS." if n != 3 else "SIEM work.",
            "vector": vector,
        }
        for n, (job_id, vector) in enumerate(zip(ids, vectors, strict=True), start=1)
    ]
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("title", pa.string()),
            ("company", pa.string()),
            ("location", pa.string()),
            ("remote", pa.bool_()),
            ("ats", pa.string()),
            ("first_seen", pa.string()),
            ("min_years", pa.int32()),
            ("experience_source", pa.string()),
            ("min_salary_annual", pa.int32()),
            ("max_salary_annual", pa.int32()),
            ("salary_currency", pa.string()),
            ("description", pa.string()),
            ("vector", pa.list_(pa.float32(), 4)),
        ]
    )
    db = lancedb.connect(tmp_path_factory.mktemp("sampled"))
    table = db.create_table("jobs", data=pa.Table.from_pylist(rows, schema=schema))
    search = JobSearch(_Model(), table)
    search._query_vector = lambda _query: [1.0, 0.0, 0.0, 0.0]
    return search


def _query_string(args: dict):
    from werkzeug.datastructures import MultiDict

    return MultiDict(list(args.items()))


def _requirements(search, **args):
    return search.requirements(_query_string({"strict": "1", **args}), _FAMILIES)


def test_a_query_samples_the_closest_jobs_and_counts_every_match(sampled):
    counted = _requirements(sampled, q="data engineer")
    assert counted["order"] == "closest" and counted["matching"] == 5
    assert (counted["read"], counted["distinct"]) == (5, 5)
    assert counted["closest_score"] == 1.0 and counted["category_window"] is None
    assert counted["closest_score"] >= counted["farthest_score"]
    python = next(s for s in counted["skills"] if s["skill"] == "Python")
    assert (python["jobs"], python["employers"]) == (4, 3)
    assert counted["categories"] == [
        {"family": "data-engineering", "jobs": 3},
        {"family": "security", "jobs": 1},
    ]


def test_a_query_is_narrowed_by_the_filters_and_the_boards(sampled):
    counted = _requirements(sampled, q="data engineer", board="lever:acme")
    assert counted["matching"] == 3 and counted["distinct"] == 3
    remote = _requirements(sampled, q="data engineer", remote="true")
    assert remote["matching"] == 1 and remote["remote"] == 1


def test_a_category_alone_samples_its_newest_jobs(sampled, monkeypatch):
    read = []
    real = sampled._rows_for_requirements
    monkeypatch.setattr(
        sampled, "_rows_for_requirements", lambda ids: read.append(ids) or real(ids)
    )
    counted = _requirements(sampled, family="data-engineering")
    assert counted["order"] == "newest" and counted["closest_score"] is None
    assert counted["matching"] == 3
    # Newest to HeadStart first, and only the sample's descriptions are read.
    assert read == [["lever:beta:4", "lever:acme:2", "lever:acme:1"]]


def test_a_query_within_a_category_keeps_only_its_jobs(sampled):
    counted = _requirements(sampled, q="data engineer", family="data-engineering")
    assert counted["matching"] == 3 and counted["distinct"] == 3
    assert counted["category_window"] == REQUIREMENTS_CATEGORY_WINDOW
    assert counted["categories"] == [{"family": "data-engineering", "jobs": 3}]


def test_the_counts_come_from_the_sampled_columns(sampled):
    counted = _requirements(sampled, family="data-engineering")
    assert counted["experience"]["stated"] == {"0-1": 1, "2-4": 2, "5-7": 0, "8+": 0}
    usd = counted["salary"]["currencies"][0]
    assert (usd["currency"], usd["jobs"], usd["median"]) == ("USD", 3, 200_000)
    assert {c["code"] for c in counted["countries"]} == {"DE", "US"}


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({}, "q=, a category with family="),
        ({"family": "cooking"}, "not a configured family"),
        ({"q": "x", "country": "ZZ"}, "country"),
    ],
)
def test_a_requirements_request_is_refused_in_words(sampled, args, words):
    with pytest.raises(ValueError, match=words):
        _requirements(sampled, **args)


def test_a_category_without_role_assignments_is_the_deployments_state(sampled):
    with pytest.raises(ScopeUnavailable):
        sampled.requirements(_query_string({"family": "security"}), None)


def test_a_requirements_answer_is_kept_for_the_boot(sampled, monkeypatch):
    first = _requirements(sampled, q="kept answer")
    monkeypatch.setattr(
        sampled, "_closest_ids", lambda *a: pytest.fail("asked the table again")
    )
    assert _requirements(sampled, q="kept answer") is first


class _DescriptionReads:
    """A real table, counting the where-clauses that read the description column."""

    def __init__(self, table):
        self._table = table
        self.reads = 0

    def __getattr__(self, name):
        return getattr(self._table, name)

    def _saw(self, where):
        self.reads += bool(where and "regexp_like(description" in where)

    def count_rows(self, filter=None):
        self._saw(filter)
        return self._table.count_rows(filter=filter)

    def search(self, *args, **kwargs):
        return _ReadsQuery(self, self._table.search(*args, **kwargs))


class _ReadsQuery:
    def __init__(self, table, query):
        self._table, self._query = table, query

    def where(self, clause, *args, **kwargs):
        self._table._saw(clause)
        return _ReadsQuery(self._table, self._query.where(clause, *args, **kwargs))

    def __getattr__(self, name):
        attribute = getattr(self._query, name)
        if not callable(attribute):
            return attribute

        def chained(*args, **kwargs):
            out = attribute(*args, **kwargs)
            return _ReadsQuery(self._table, out) if hasattr(out, "where") else out

        return chained


@pytest.mark.parametrize(
    "ranking", [{}, {"like": "lever:acme:1"}], ids=["browse", "like"]
)
def test_a_description_keyword_is_read_once_for_the_page_its_total_and_page_2(
    served, ranking
):
    """ADR-0320: `search_jobs` asks the page and the total at once, then page 2. The keyword
    reads descriptions twice in all (its literal, then the exact clause over the literal's
    rows), not once per route and page; and the answers are the compiled clause's."""
    table = _DescriptionReads(served._table)
    searcher = JobSearch(_Model(), table)
    asked = {**ranking, "kw": "short", "kw_in": "description", "k": "1"}
    first = searcher.run(asked)
    total = searcher.facets({**asked, "counts": "total"})["total"]
    second = searcher.run({**asked, "page": "2"})
    assert table.reads == 2
    assert total == 3  # "Short." in jobs 2 to 4; job 1, the like= one, has none
    plain = JobSearch(_Model(), served._table)
    plain._description_matches.where = lambda filters, extra: with_extra(
        build_filter(filters, plain.capabilities), extra
    )
    assert [r["id"] for r in first] == [r["id"] for r in plain.run(asked)]
    assert [r["id"] for r in second] == [
        r["id"] for r in plain.run({**asked, "page": "2"})
    ]


# ---- a category across the whole index, and the three agent-only filters (ADR-0322) ----


def _days_ago(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture(scope="module")
def families_served(tmp_path_factory):
    """A real LanceDB table of five Jobs in two families, with posted dates of every shape the
    age filter reads, and family tables over it. No `posted_at_comparable`, so the age filter's
    unmaterialized guard (a LIKE, NULL on a NULL date) is the one exercised."""
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    from headstart.serving.job_search import FamilyTables

    rows = [
        # id, location, remote, posted_at, first_seen, min_years, company, description, vector
        ("lever:acme:1", "Berlin, Germany", True, _days_ago(10)[:10], _days_ago(9), 8,
         "Acme", "We sponsor visas.", [1.0, 0.0, 0.0, 0.0]),
        ("lever:acme:2", "Austin, TX", False, "2022-07-29", _days_ago(3), 2,
         "Acme", "No sponsorship.", [0.9, 0.1, 0.0, 0.0]),
        ("lever:beta:3", "Munich, Germany", False, None, _days_ago(5), None,
         "Beta", None, [0.6, 0.4, 0.0, 0.0]),
        ("lever:beta:4", "Berlin, Germany", True, _days_ago(1)[:10], _days_ago(1), 10,
         "Beta Labs", "Visa sponsorship offered.", [0.95, 0.05, 0.0, 0.0]),
        ("lever:gamma:5", "Toronto, ON, CA", False, "21-Apr-2026",
         "2024-01-01T00:00:00+00:00", 3, None, "Hybrid.", [0.0, 1.0, 0.0, 0.0]),
    ]  # fmt: skip
    columns = ("id", "location", "remote", "posted_at", "first_seen", "min_years",
               "company", "description", "vector")  # fmt: skip
    data = [dict(zip(columns, row, strict=True)) for row in rows]
    for n, row in enumerate(data, start=1):
        row |= {
            "title": f"Engineer {n}",
            "ats": "lever",
            "employment_type": "Full-time",
            "url": f"https://jobs.lever.co/x/{n}",
            "description_stored": row["description"] is not None,
        }
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("title", pa.string()),
            ("company", pa.string()),
            ("location", pa.string()),
            ("remote", pa.bool_()),
            ("ats", pa.string()),
            ("employment_type", pa.string()),
            ("posted_at", pa.string()),
            ("first_seen", pa.string()),
            ("url", pa.string()),
            ("min_years", pa.int32()),
            ("description", pa.string()),
            ("description_stored", pa.bool_()),
            ("vector", pa.list_(pa.float32(), 4)),
        ]
    )
    db = lancedb.connect(tmp_path_factory.mktemp("families"))
    table = db.create_table("jobs", data=pa.Table.from_pylist(data, schema=schema))
    searcher = JobSearch(_Model(), table)
    searcher.families = FamilyTables(
        table,
        {
            "ai-ml": ["lever:acme:1", "lever:acme:2", "lever:beta:3"],
            "software-engineering": ["lever:beta:4", "lever:gamma:5"],
        },
    )
    return searcher


def _ids(rows):
    return [row["id"] for row in rows]


def test_a_category_across_the_index_lists_and_counts_exactly_its_jobs(
    families_served,
):
    rows = families_served.run({"family": "ai-ml"})
    # Newest to HeadStart first, as any browse.
    assert _ids(rows) == ["lever:acme:2", "lever:beta:3", "lever:acme:1"]
    assert families_served.facets({"family": "ai-ml"})["total"] == 3
    german = {"family": "ai-ml", "country": "DE"}
    assert _ids(families_served.run(german)) == ["lever:beta:3", "lever:acme:1"]
    strip = families_served.facets(german)
    assert strip["total"] == 2
    # Every option is counted within the category too: one of its German jobs is remote.
    assert strip["facets"]["remote"] == [
        {"value": True, "label": "Remote only", "count": 1}
    ]


def test_a_ranked_category_ranks_only_its_own_jobs(families_served):
    rows = families_served.run({"family": "ai-ml", "like": "lever:beta:4"})
    assert _ids(rows) == ["lever:acme:1", "lever:acme:2", "lever:beta:3"]
    assert rows[0]["score"] > rows[1]["score"] > rows[2]["score"]
    total = families_served.facets({"family": "ai-ml", "like": "lever:beta:4"})
    assert total["total"] == 3


def test_a_description_keyword_in_a_category_is_matched_on_the_served_table(
    families_served,
):
    args = {"family": "ai-ml", "kw": "visa", "kw_in": "description"}
    assert _ids(families_served.run(args)) == ["lever:acme:1"]
    counted = families_served.facets(args)
    assert counted["total"] == 1
    # Of the category's 3 jobs, 2 have a description the keyword could match.
    assert counted["description_coverage"] == {"covered": 2, "total": 3}
    none = families_served.facets({**args, "kw": "kubernetes"})
    assert none["total"] == 0 and none["blocking"] == "kw"
    # The keyword is named when lifting it recovers more than lifting the filter the counts
    # named: max_years=5 leaves out its one match (8 years), and without the keyword 2 remain.
    both = families_served.facets({**args, "max_years": "5"})
    assert both["total"] == 0 and both["blocking"] == "kw"


def test_a_category_no_job_is_assigned_to_matches_nothing(families_served):
    assert families_served.run({"family": "security"}) == []
    assert families_served.facets({"family": "security"})["total"] == 0


def test_a_category_beside_a_board_is_the_board_hand_offs_not_a_family_table(
    families_served, monkeypatch
):
    from werkzeug.datastructures import MultiDict

    def no_table(_family):
        raise AssertionError("a Board's category is scoped_jobs_clause's")

    monkeypatch.setattr(families_served.families, "table", no_table)
    args = MultiDict([("family", "ai-ml"), ("board", "lever:acme")])
    assert len(families_served.run(args)) == 5  # the app's extra_where scopes it


def test_max_age_reads_the_posted_date_and_else_the_first_seen_day(families_served):
    # 2 was posted in 2022 though first seen this week: the posted date wins. 3 has no posted
    # date and was first seen 5 days ago. 5's date is unreadable and it was first seen in 2024.
    rows = families_served.run({"max_age_days": "365"})
    assert sorted(_ids(rows)) == ["lever:acme:1", "lever:beta:3", "lever:beta:4"]
    assert families_served.facets({"max_age_days": "365"})["total"] == 3


def test_an_experience_floor_leaves_out_jobs_whose_experience_is_unknown(
    families_served,
):
    rows = families_served.run({"required_years_at_least": "8"})
    assert sorted(_ids(rows)) == ["lever:acme:1", "lever:beta:4"]
    floored = {"family": "ai-ml", "required_years_at_least": "8"}
    assert _ids(families_served.run(floored)) == ["lever:acme:1"]


def test_exclude_company_drops_every_name_containing_it_and_keeps_nameless_rows(
    families_served,
):
    rows = families_served.run({"exclude_company": "BETA"})
    assert sorted(_ids(rows)) == ["lever:acme:1", "lever:acme:2", "lever:gamma:5"]
    blocked = families_served.facets({"family": "ai-ml", "exclude_company": "e"})
    assert blocked["total"] == 0 and blocked["blocking"] == "exclude_company"


# ---- operators= (ADR-0335) ----

#: Beta's Board is a staffing firm's and Gamma's a job board's, spelt as a directory may spell
#: them; Acme is an employer, as every Board no Operator names is.
_OPERATOR_BOARDS = {"staffing": ("lever:beta",), "aggregator": ("LEVER:Gamma",)}


@contextmanager
def _operators_loaded(searcher, boards):
    """``searcher`` with ``boards`` loaded, and none again after: the fixtures are shared."""
    searcher.load_operator_boards(boards)
    try:
        yield searcher
    finally:
        searcher.load_operator_boards(None)


@pytest.fixture
def operated(families_served):
    with _operators_loaded(families_served, _OPERATOR_BOARDS) as searcher:
        yield searcher


def test_operators_keep_only_the_boards_of_the_operators_named(operated):
    kept = operated.run({"operators": "employer,services"})
    assert sorted(_ids(kept)) == ["lever:acme:1", "lever:acme:2"]
    # A list without employers names the Boards it keeps.
    staffing = operated.run({"operators": "staffing"})
    assert sorted(_ids(staffing)) == ["lever:beta:3", "lever:beta:4"]
    assert operated.run({"operators": "services"}) == []
    every = "employer,services,staffing,aggregator"
    assert len(operated.run({"operators": every})) == 5
    ranked = operated.run({"like": "lever:acme:1", "operators": "employer"})
    assert _ids(ranked) == ["lever:acme:2"]


def test_the_counts_say_how_many_rows_operators_left_out(operated):
    counted = operated.facets({"operators": "employer,services", "counts": "total"})
    assert (counted["total"], counted["operators_left_out"]) == (2, 3)
    full = operated.facets({"operators": "Employer , services"})
    assert (full["total"], full["operators_left_out"]) == (2, 3)
    # A category counts its family table; a description keyword its matched rows.
    family = operated.facets({"family": "ai-ml", "operators": "employer"})
    assert (family["total"], family["operators_left_out"]) == (2, 1)
    keyword = {"kw": "sponsor", "kw_in": "description", "operators": "employer"}
    assert sorted(_ids(operated.run(keyword))) == ["lever:acme:1", "lever:acme:2"]
    described = operated.facets(keyword)
    assert (described["total"], described["operators_left_out"]) == (2, 1)
    # Nothing left out is 0; no list, or every Operator, is no figure at all.
    none_out = operated.facets({"operators": "employer,staffing,aggregator"})
    assert none_out["operators_left_out"] == 0
    assert "operators_left_out" not in operated.facets({})


def test_only_the_boards_the_table_serves_are_kept_case_folded(families_served):
    """Every Board named is one more alternative each id is matched against, and a directory
    names Boards long since empty (ADR-0335)."""
    boards = {**_OPERATOR_BOARDS, "services": ("lever:ghost", "lever:be")}
    with _operators_loaded(families_served, boards):
        assert families_served.operator_boards == {
            "staffing": ("lever:beta",),
            "aggregator": ("lever:gamma",),
            "services": (),
        }


def test_an_unknown_operator_is_refused_even_without_strict(operated):
    with pytest.raises(ValueError, match="operators recruiter not known"):
        operated.run({"operators": "employer,recruiter"})


def test_operators_without_a_directory_is_the_deployments_state(families_served):
    assert families_served.operator_boards is None
    with pytest.raises(ScopeUnavailable, match="company directory"):
        families_served.facets({"operators": "employer", "strict": "1"})
    # Not strict: widened, as a category without its tables is.
    assert len(families_served.run({"operators": "employer"})) == 5


def test_a_requirements_sample_leaves_out_the_operators_not_named(sampled):
    with _operators_loaded(sampled, {"staffing": ("lever:acme",)}):
        counted = _requirements(
            sampled, q="data engineer", operators="employer,services"
        )
        assert (counted["matching"], counted["operators_left_out"]) == (2, 3)
        assert counted["distinct"] == 2
        family = _requirements(sampled, family="data-engineering", operators="employer")
        assert (family["matching"], family["operators_left_out"]) == (1, 2)
    assert _requirements(sampled, q="data engineer")["operators_left_out"] is None


# ---- work_authorization: text-derived stances as a filter (ADR-0333) ----


def _stances_read(searcher):
    searcher.work_authorization.start()
    assert searcher.work_authorization.wait(30)
    return searcher


def test_a_stance_keeps_the_jobs_whose_description_states_it(families_served):
    search = _stances_read(families_served)
    offers = {"work_authorization": "offers_sponsorship"}
    assert _ids(search.run(offers)) == ["lever:beta:4", "lever:acme:1"]
    assert search.facets({**offers, "counts": "total"})["total"] == 2
    refuses = {"work_authorization": "refuses_sponsorship"}
    assert _ids(search.run(refuses)) == ["lever:acme:2"]
    # Within a category's family table too, and beside a description keyword.
    assert _ids(search.run({**offers, "family": "ai-ml"})) == ["lever:acme:1"]
    assert search.facets({**offers, "family": "ai-ml"})["total"] == 1
    keyed = {**offers, "kw": "offered", "kw_in": "description"}
    assert _ids(search.run(keyed)) == ["lever:beta:4"]
    assert search.facets(keyed)["total"] == 1


def test_a_stance_costing_everything_is_named_as_the_blocking_filter(families_served):
    search = _stances_read(families_served)
    counted = search.facets(
        {"work_authorization": "offers_relocation", "remote": "true"}
    )
    assert counted["total"] == 0 and counted["blocking"] == "work_authorization"


def test_an_unknown_stance_is_refused_naming_the_known_ones(families_served):
    with pytest.raises(ValueError, match="offers_sponsorship, refuses_sponsorship"):
        families_served.run({"work_authorization": "sponsors"})


def test_a_stance_asked_before_the_read_finishes_is_not_ready(served, monkeypatch):
    monkeypatch.setattr("headstart.serving.job_search.WORK_AUTHORIZATION_WAIT_S", 0.01)
    with pytest.raises(ScopeUnavailable, match="has not finished"):
        served.run({"work_authorization": "offers_sponsorship"})


def test_a_read_by_id_says_what_its_whole_description_states(families_served):
    job = families_served.jobs_by_id(["lever:acme:1"])["lever:acme:1"]
    assert job["work_authorization"] == {
        "stances": ["offers_sponsorship"],
        "mentions": ["We sponsor visas."],
    }
