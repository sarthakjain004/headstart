"""World country gazetteer (ADR-0273): term hygiene, the traps the served table showed, and the
SQL clause run against a real LanceDB table agreeing with the Python rule row for row."""

from __future__ import annotations

import collections

import pytest

from headstart.search_filters import country_gazetteer
from headstart.search_filters.country_gazetteer import (
    COUNTRIES,
    classify,
    matches,
    where,
)

# Real location strings from the served table (2026-09-29) and the countries each is in.
_ROWS = [
    ("United States", {"US"}),
    ("USA", {"US"}),
    ("US", {"US"}),
    ("Remote - US", {"US"}),
    ("US-CA-Santa Clara", {"US"}),
    ("Austin, TX", {"US"}),
    ("San Francisco, CA", {"US"}),
    ("Annapolis Junction, MD", {"US"}),
    ("Nashville, TN", {"US"}),
    ("Chicago, IL", {"US"}),
    # a two-letter code is shared: another country named for sure takes the row
    ("Toronto, ON, CA", {"CA"}),
    ("Vancouver, BC, CA", {"CA"}),
    ("Ontario, CA, US", {"US"}),  # Ontario, California
    ("Tel Aviv, IL", {"IL"}),
    ("Madrid, MD, ES", {"ES"}),
    ("Chennai, TN, India", {"IN"}),
    ("Berlin, DE", {"DE"}),
    ("München", {"DE"}),
    ("Wilmington, DE, US", {"US"}),
    ("Luxembourg, Luxembourg (fr)", {"LU"}),  # a language tag, not France
    ("Seongnam-si, Gyeonggi-do, Korea", {"KR"}),  # "-si" is not Slovenia
    # a shared city name yields to another country's code or name
    ("London", {"GB"}),
    ("London, ON, Canada", {"CA"}),
    ("Dublin, Ireland", {"IE"}),
    ("Dublin, OH", {"US"}),
    ("Vienna, VA", {"US"}),
    ("Vienna, Austria", {"AT"}),
    ("Melbourne, FL, US", {"US"}),
    ("Melbourne, Victoria, Australia", {"AU"}),
    # a word inside a longer place name is a segment trap
    ("Albuquerque, New Mexico", {"US"}),
    ("Guadalajara, Mexico", {"MX"}),
    ("Tijuana, Baja California, Mexico", {"MX"}),
    ("Belfast, Northern Ireland", {"GB"}),
    ("Cardiff, Wales", {"GB"}),
    ("Sydney, New South Wales, Australia", {"AU"}),
    ("Taiwan, Province of China", {"TW"}),
    ("Hong Kong, China", {"CN", "HK"}),
    ("USA - New York - Malta; United States of America", {"US"}),
    ("Beth Israel Lahey Health; United States of America", {"US"}),
    ("Hyderabad - Phoenix Aquila, India", {"IN"}),
    # whitespace and an in-word hyphen never separate a code
    ("Rio de Janeiro, Brazil", {"BR"}),
    ("Louvain-la-Neuve, BE", {"BE"}),
    # rows naming several countries are in all of them
    ("United States; Canada", {"US", "CA"}),
    ("London, United Kingdom; New York, NY, United States", {"GB", "US"}),
    ("Remote", set()),
    ("Europe", set()),
    ("", set()),
    (None, set()),
]


def test_every_term_is_a_trusted_lowercase_constant():
    for code, country in COUNTRIES.items():
        assert code.isupper() and len(code) == 2, code
        for kind in ("words", "segments", "shared_words", "shared_segments"):
            for term in getattr(country, kind):
                assert term == term.lower().strip() and term, (code, term)
                assert "'" not in term and "%" not in term, (code, term)


def test_no_term_names_two_countries():
    """A term two countries both claim would put its bare rows in both."""
    owners = collections.defaultdict(set)
    for code, country in COUNTRIES.items():
        for kind in ("words", "segments", "shared_words", "shared_segments"):
            for term in getattr(country, kind):
                owners[term].add(code)
    assert {t: o for t, o in owners.items() if len(o) > 1} == {}


def test_every_two_letter_code_but_us_is_shared():
    """Two letters always name something else somewhere: a state, a province, a language."""
    for code, country in COUNTRIES.items():
        sure_codes = [s for s in country.segments if len(s) == 2]
        assert sure_codes == (["us"] if code == "US" else []), code


@pytest.mark.parametrize(("location", "expected"), _ROWS)
def test_classify(location, expected):
    assert classify(location) == expected


def test_india_is_the_india_filters_rule_and_not_compiled_here():
    assert where("IN") is None
    assert matches("IN", "Pune City") and not matches("IN", "Indianapolis, IN")


def test_an_unknown_code_is_none():
    assert where("ZZ") is None and not matches("ZZ", "Anywhere")


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    lancedb = pytest.importorskip("lancedb")
    db = lancedb.connect(tmp_path_factory.mktemp("country_db"))
    rows = [{"id": str(i), "location": loc} for i, (loc, _) in enumerate(_ROWS)]
    return db.create_table("jobs", rows)


def test_where_agrees_with_matches_on_a_real_table(table):
    """The SQL each code compiles to selects exactly the rows the Python rule does: the same
    regex strings run in DataFusion and in `re`."""
    for code in COUNTRIES:
        clause = where(code)
        hits = set(table.search().where(clause).limit(100).to_arrow()["id"].to_pylist())
        expected = {str(i) for i, (loc, _) in enumerate(_ROWS) if matches(code, loc)}
        assert hits == expected, code


def test_a_clause_is_at_most_five_regex_passes():
    for code in COUNTRIES:
        assert where(code).count("regexp_like(") <= 5, code
    assert country_gazetteer.where("SG").count("regexp_like(") == 3
