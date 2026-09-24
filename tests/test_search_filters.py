"""Tests for the Search-filter compiler, `headstart.search_filters` (ADR-0031, ADR-0149, ADR-0194).

The builders are the one place user input reaches the LanceDB where-clause, so their
validation (whitelists, re-serialization, escaping) is worth locking down: `build_filter`
is the reference product filter (ADR-0031, moved from the Space app in ADR-0042 and out of
`search.py` in ADR-0194).
"""

from __future__ import annotations

import pytest

from headstart.employment_type_filter import RULES as EMPLOYMENT_TYPE_RULES
from headstart.search_filters import (
    IndexCapabilities,
    SearchFilters,
    account_clause,
    board_clause,
    build_filter,
)

# `IndexCapabilities`'s field names — used below to route a `_clause`/`_bracket` override into
# the right one of the two objects `build_filter` now takes (ADR-0149), so the bulk of this
# file's tests can keep calling those helpers with one flat kwargs blob, unchanged.
_CAPABILITY_FIELDS = set(IndexCapabilities.__dataclass_fields__)


def _split(kwargs: dict) -> tuple[SearchFilters, IndexCapabilities]:
    caps = {k: v for k, v in kwargs.items() if k in _CAPABILITY_FIELDS}
    filters = {k: v for k, v in kwargs.items() if k not in _CAPABILITY_FIELDS}
    return SearchFilters(**filters), IndexCapabilities(**caps)


def _clause(**kw):
    kw.setdefault("atses", ("greenhouse", "lever"))
    kw.setdefault("has_first_seen", True)
    kw.setdefault("has_min_salary_annual", True)
    return build_filter(*_split(kw))


def test_product_experience_filter_uses_flags_only_for_materialized_ceilings():
    assert (
        _clause(max_years=5, has_experience_filter_flags=True)
        == "experience_at_most_5 = true"
    )
    assert _clause(max_years=3, has_experience_filter_flags=True) == (
        "(min_years <= 3 OR min_years IS NULL)"
    )


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
    fails on that kind of change while catching none of what it is here to catch. City/region
    values are unaffected by `has_country` (ADR-0138) — only the top-level "india" value ever
    takes the materialized-column path, see the tests below.
    """
    from headstart import geo

    clause = _clause(india="bengaluru")
    assert clause is not None
    assert clause == geo.where("bengaluru")
    assert _clause(india="bengaluru", has_country=True) == geo.where("bengaluru")
    assert _clause(india="not-a-place") is None


def test_india_country_level_uses_the_materialized_column_when_available():
    """ADR-0138: once the table carries `country`, the whole-country case uses a plain equality
    instead of `geo.where("india")`'s regex alternation."""
    assert _clause(india="india", has_country=True) == "country = 'IN'"


def test_india_country_level_falls_back_to_geo_without_the_column():
    """Dark-until-migrated (ADR-0138): forgetting `has_country`, or a table that hasn't synced
    it yet, falls back to the slower-but-correct `geo.where()` path — never errors, never
    changes which rows match."""
    from headstart import geo

    assert _clause(india="india", has_country=False) == geo.where("india")
    assert _clause(india="india") == geo.where("india")  # has_country defaults False


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
    # (parse_filters whitelists `kw_in` against KEYWORD_SCOPES), pinned in the JobSearch tests
    # below — so garbage that somehow bypassed the parser still cannot reach the where-clause.
    assert _clause(kw="go", kw_in="'; DROP TABLE jobs; --") is None


def test_keyword_terms_are_capped():
    clause = _clause(kw="a b c d e f g")
    assert clause is not None and clause.count(" AND ") == 4  # five terms, not seven


def test_a_scope_without_a_keyword_filters_nothing():
    assert _clause(kw_in="description", has_description=True) is None


def test_keyword_scope_options_come_from_the_map_in_order_with_labels_and_needs():
    from headstart.search_filters import KEYWORD_SCOPES, keyword_scope_options

    options = keyword_scope_options()
    assert [v for v, _, _ in options] == list(KEYWORD_SCOPES)  # same order as the map
    assert all(label for _, label, _ in options)  # every scope has a label
    # only scopes that name the optional column carry the disclaimer / disabled rule
    assert {v: needs for v, _, needs in options} == {
        "title": False,
        "description": True,
        "both": True,
    }


def test_posted_at_shape_guard_prefers_the_materialized_flag():
    assert (
        _clause(posted_after="2026-09-01", has_posted_at_comparable=True)
        == "(posted_at >= '2026-09-01' AND posted_at_comparable = true)"
    )


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


def test_range_overflow_is_a_valueerror_not_a_500():
    # 9999-12-31 + 1 day overflows date; the route only turns ValueError into a 400.
    with pytest.raises(ValueError):
        _clause(posted_before="9999-12-31")
    with pytest.raises(ValueError):
        _clause(seen_before="9999-12-31")


def test_recency_window_overflow_is_a_valueerror_not_a_500():
    # Same treatment for the windows, which take an unbounded int: both the calendar bound
    # (739,865 days / 17,756,755 hours walks below year 1) and timedelta's own magnitude cap, in
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


def _bracket(**kwargs):
    kwargs.setdefault("atses", ["darwinbox"])
    kwargs.setdefault("currencies", ["USD", "INR"])
    kwargs.setdefault("has_first_seen", True)
    kwargs.setdefault("has_min_salary_annual", True)
    return build_filter(*_split(kwargs))


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

    The USD default ADR-0084 records lived only in the browser's <select>, so a caller that is
    not that <select> — `scripts/eval/verify_filters.py`, or a hand-built `/search?salary_min=…`
    — had its numeric bound silently dropped and got the unfiltered set back, with no error and
    nothing for `facets._blocking` to name. Not the alerts path: `alerts.store`'s
    `ALLOWED_SEARCH_FILTERS` excludes the salary keys, so a Subscription never carries a bracket.
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
    where = _bracket(salary_currency="'; DROP TABLE jobs; --", salary_min=1)
    assert "DROP TABLE" not in where
    assert (
        "salary_currency = 'USD' AND COALESCE(max_salary_annual, min_salary_annual) >= 1"
        in where
    )
    # Every currency named in the clause came from the table's own whitelist, never from the
    # request — the cross-currency expansion (ADR-0117) widened what is emitted, not what is
    # trusted.
    import re

    assert set(re.findall(r"salary_currency = '([^']+)'", where)) <= {
        "USD",
        "INR",
        "EUR",
    }
    assert _bracket(salary_currency="XXX", salary_min=1) == _bracket(salary_min=1)
    # With no bound there is no bracket to scope, so a currency alone still compiles nothing —
    # ADR-0084's rule that picking one must not cut the result set to the ~28.5% carrying a salary.
    assert _bracket(salary_currency="XXX") is None


def test_the_bracket_stays_dark_where_even_the_default_is_unavailable():
    # A CONTROL for the *placement* of the whitelist check, not for the fallback itself: it passes
    # with `search.py` reverted too, because the old code also emitted nothing here. What it
    # discriminates against is a naive fallback that trusts its own default.
    # `currencies` is empty until the ADR-0082 columns land, which makes every currency unknown
    # there — including `SALARY_DEFAULT_CURRENCY`. Emitting it anyway would be a clause matching
    # nothing: the same silent wrong answer the default exists to remove, just relocated.
    assert (
        build_filter(
            SearchFilters(salary_currency="INR", salary_min=1),
            IndexCapabilities(
                atses=[], currencies=[], has_first_seen=True, has_min_salary_annual=True
            ),
        )
        is None
    )


def test_bracket_stays_dark_until_the_salary_columns_exist():
    # Same dark-until-migrated rule the rest of the salary path follows: a table LanceDB has
    # not synced onto the ADR-0082 columns would error on every query rather than just not
    # offering the feature.
    assert (
        build_filter(
            SearchFilters(salary_currency="USD", salary_min=1),
            IndexCapabilities(
                atses=[],
                currencies=["USD"],
                has_first_seen=True,
                has_min_salary_annual=False,
            ),
        )
        is None
    )


def test_an_inclusive_upper_bound_compares_below_the_next_day():
    """An inclusive "before" has to be expressed as "< the next day", because both date columns
    hold date-or-datetime strings and '2026-08-10T12:00' sorts above '2026-08-10'. Leap years
    come from `date` itself, and running off the calendar is a 400 like any other bad date.
    """
    from headstart.search_filters import _next_day

    assert _next_day("2026-08-10") == "2026-08-11"
    assert _next_day("2026-12-31") == "2027-01-01"
    assert _next_day("2028-02-28") == "2028-02-29"  # a leap year, not 03-01
    with pytest.raises(ValueError):
        _next_day("9999-12-31")  # +1 day leaves the calendar; a 400 like any bad date


def test_internship_does_not_claim_international():
    """`intern` is a substring of `international`, which is not an employment type at all.

    On the served table the unguarded clause claimed 47 of the 794 rows it returned (5.9%) —
    "International EOR", "International Full Time Employee", "International Office Entity".
    Every distinct value carrying both words was one of those, so the guard costs nothing real.
    Asserted on the compiled clause rather than through a table, the way
    `test_ind_is_never_a_bare_substring` guards the gazetteer's identical trap: the failure is
    silent and the table can only catch strings someone thought to add.
    """
    clause = EMPLOYMENT_TYPE_RULES["internship"].raw_clause()
    assert "LIKE '%intern%'" in clause
    assert "NOT LIKE '%international%'" in clause


def test_a_usd_bracket_also_matches_the_same_money_in_other_currencies():
    """The defect this fixes: picking USD used to drop every INR job, silently."""
    where = _bracket(salary_currency="USD", salary_min=100_000, salary_max=200_000)
    # The user's own currency keeps the numbers they typed, unrounded.
    assert (
        "salary_currency = 'USD' AND "
        "COALESCE(max_salary_annual, min_salary_annual) >= 100000 AND "
        "min_salary_annual <= 200000"
    ) in where
    # …and the same money is asked for in the others, at whatever the committed table says.
    # Derived from the table rather than hardcoded: a rate refresh is a routine two-line edit
    # (ADR-0117), and a test that pins 83.0 turns every refresh into a failing build.
    from headstart import fx

    rate = fx.table()["rates"]["INR"]
    assert "salary_currency = 'INR'" in where
    assert str(int(100_000 * rate)) in where
    assert where.startswith("((") and " OR " in where


def test_a_converted_bound_rounds_outward_so_a_boundary_job_is_never_dropped():
    """The ceiling goes UP, never down — a job sitting exactly on the user's bound must survive
    the arithmetic. Asserted against the table's own rate so a refresh cannot break it."""
    from headstart import fx

    rate = fx.table()["rates"]["INR"]
    where = _bracket(salary_currency="USD", salary_max=200_000)
    assert f"min_salary_annual <= {int(200_000 * rate) + 1}" in where


def test_a_currency_with_no_rate_is_left_out_rather_than_compared_at_one_to_one():
    where = build_filter(
        SearchFilters(salary_currency="USD", salary_min=100_000),
        IndexCapabilities(
            currencies=["USD", "INR", "XTS"],  # XTS is not in config/fx_rates.json
            atses=["greenhouse"],
            has_first_seen=True,
            has_description=True,
            has_min_salary_annual=True,
        ),
    )
    assert "XTS" not in where
    assert "salary_currency = 'INR'" in where


def test_without_a_rate_table_the_bracket_falls_back_to_one_currency(monkeypatch):
    """Degrading to the older, narrower answer is recoverable; a wrong one is not."""
    from headstart import fx

    monkeypatch.setattr(fx, "table", lambda: None)
    where = _bracket(salary_currency="USD", salary_min=100_000)
    assert where == (
        "salary_currency = 'USD' AND "
        "COALESCE(max_salary_annual, min_salary_annual) >= 100000"
    )


def test_no_boards_compiles_to_no_clause() -> None:
    """None, not an empty string: a caller must be able to tell 'nothing to say' from a clause."""
    assert board_clause([], exclude=True) is None
    assert board_clause([""], exclude=False) is None


def test_a_board_matches_its_own_jobs_only() -> None:
    """The trailing colon is load-bearing — without it `acme` also matches `acmecorp`."""
    clause = board_clause(["greenhouse:acme"], exclude=False)
    assert clause == "(lower(id) LIKE 'greenhouse:acme:%')"


def test_like_wildcards_in_a_board_key_are_escaped() -> None:
    """Board keys legitimately contain `_`, which is a LIKE wildcard matching any character."""
    clause = board_clause(["workday:ngc/Northrop_Grumman_External_Site"], exclude=True)
    assert r"northrop\_grumman\_external\_site:%" in clause
    assert clause.startswith("NOT (")


def test_matching_is_case_insensitive_so_one_company_is_not_half_hidden() -> None:
    """The index holds 335 Board-key groups differing only in casing — one company, two rows."""
    assert board_clause(["Workday:Micron/External"], exclude=True) == board_clause(
        ["workday:micron/external"], exclude=True
    )


def test_a_long_board_key_is_not_truncated() -> None:
    """`_like` caps terms at 60 chars; a Taleo key is a whole URL, and a truncated prefix
    would hide every Board sharing that prefix rather than the one chosen."""
    board = "taleo_be:https://phh.tbe.taleo.net/phh01/ats/careers/v2/searchResults?org=EXAMPLECO"
    clause = board_clause([board], exclude=True)
    assert "examplecc" not in clause
    assert board.lower() in clause.replace("\\", "")


@pytest.mark.parametrize("exclude", [True, False])
def test_boards_are_deduplicated_and_ordered(exclude: bool) -> None:
    once = board_clause(["lever:b", "lever:a", "lever:b"], exclude=exclude)
    assert once.count("lower(id) LIKE") == 2
    assert once.index("lever:a") < once.index("lever:b"), (
        "sorted, so the clause is stable"
    )


def test_account_clause_states_the_rule_once_for_both_apps():
    """The Space and the dev renderer share this, so the rule cannot come to differ."""
    assert account_clause([], [], mine=False) is None
    # An empty follow list with `mine` must match NOTHING, never widen to the whole index.
    assert account_clause([], [], mine=True) == "false"
    # Hidden Boards are excluded whether or not `mine` is on.
    assert account_clause([], ["c:d"], mine=False) == "NOT (lower(id) LIKE 'c:d:%')"
    both = account_clause(["a:b"], ["c:d"], mine=True)
    assert both == "(lower(id) LIKE 'a:b:%') AND NOT (lower(id) LIKE 'c:d:%')"


def test_employment_type_flags_are_used_only_after_the_whole_migration_lands():
    assert _clause(etype="contract") == EMPLOYMENT_TYPE_RULES["contract"].raw_clause()
    assert (
        _clause(etype="contract", has_employment_type_flags=True)
        == "is_contract = true"
    )


def test_has_salary_prefers_the_materialized_presence_flag():
    assert _clause(has_salary=True, has_salary_known=True) == "salary_known = true"
