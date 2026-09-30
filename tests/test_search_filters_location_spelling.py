"""Tests for the `location` filter's spelling rules, `headstart.search_filters.location_spelling`
(ADR-0344): accent folding and a city's other spellings, one regex that DataFusion and `re` both run.

The cases are the measured misses on the served table (v18, 500,167 rows, 2026-09-29): "Zurich"
reached 474 of 777 Zurich rows, "Krakow" 709 of 1,302, "Bangalore" 15,035 of 29,224 Bengaluru
rows, "istanbul" missed 130 rows spelled "İstanbul".
"""

from __future__ import annotations

import re

import pytest

from headstart.search_filters import location_spelling
from headstart.search_filters.compiler import (
    IndexCapabilities,
    SearchFilters,
    build_filter,
)
from headstart.search_filters.location_spelling import (
    accented_words,
    fold,
    matches,
    pattern,
)

# Locations as the served table writes them, one for each accented word the tests below ask for.
_ACCENTED = accented_words(
    [
        "Zürich, Switzerland", "Kraków, Poland", "Łódź, Poland", "İstanbul, Turkey",
        "São Paulo, Brazil", "Malmö", "Hauptstraße 1, Berlin", "Bogotá, D.C., Colombia",
        "København, Denmark", "München, Germany", "Köln", "Nürnberg", "Türkiye",
        "Ciudad de México, Mexico", "Genève", "București", "Göteborg", "Bruxelles",
        "Montréal, Canada", "Düsseldorf", "Yerevan, Հայաստան", "東京都, Japan",
    ]
)  # fmt: skip


def _matches(term: str, location: str | None) -> bool:
    return matches(term, location, _ACCENTED)


# Every accented Latin letter that 10+ served locations carry (measured on the table's own
# `location` values, 2026-09-29), with the plain letters a person types for it.
_TABLE_LETTERS = {
    "ü": "u", "é": "e", "ā": "a", "ó": "o", "á": "a", "ã": "a", "ö": "o", "í": "i",
    "ł": "l", "ń": "n", "ș": "s", "î": "i", "ä": "a", "ø": "o", "ñ": "n", "ý": "y",
    "š": "s", "à": "a", "å": "a", "ź": "z", "è": "e", "ồ": "o", "ī": "i", "ě": "e",
    "ç": "c", "ş": "s", "ô": "o", "ë": "e", "ú": "u", "ộ": "o", "ą": "a", "â": "a",
    "ă": "a", "ś": "s", "č": "c", "ố": "o", "ę": "e", "ž": "z", "ň": "n", "õ": "o",
    "ț": "t", "ì": "i", "ř": "r", "ï": "i", "ầ": "a", "ı": "i", "İ": "i",
}  # fmt: skip


def test_fold_reads_every_accented_letter_the_table_carries_as_its_plain_letter():
    for accented, plain in _TABLE_LETTERS.items():
        assert fold(accented) == plain, accented


def test_fold_lowercases_and_spells_out_the_letters_with_no_single_plain_one():
    assert fold("Zürich") == "zurich"
    assert fold("Kraków") == "krakow"
    assert fold("Łódź") == "lodz"
    assert fold("København") == "kobenhavn"
    assert fold("Straße") == "strasse"
    assert fold("Ærø") == "aero"
    assert fold("İSTANBUL") == "istanbul"


def test_fold_reads_a_decomposed_letter_like_the_composed_one():
    assert fold("Zürich") == fold("Zürich") == "zurich"


def test_fold_leaves_other_scripts_as_they_are():
    for text in ("東京", "Москва", "Київ", "กรุงเทพมหานคร", "서울"):
        assert fold(text) == text.casefold()


def test_the_words_a_table_spells_with_accents_are_read_off_its_locations():
    words = accented_words(
        [
            "Zürich, Switzerland", "Zurich", None, "", "İstanbul", "Hauptstraße 1",
            "東京都, Japan", "Москва", "Bengaluru", "São Paulo",
        ]
    )  # fmt: skip
    # each letter an accent made is capitalised: that is where a plain `LIKE` would miss
    assert words == {"zUrich", "Istanbul", "hauptstraSSe", "sAo"}
    assert accented_words(["ZÜRICH", "zürich", "Zürich"]) == {"zUrich"}


@pytest.mark.parametrize(
    ("term", "location"),
    [
        ("Zurich", "Zürich, Switzerland"),
        ("Zürich", "Zurich, Switzerland"),
        ("ZURICH", "zürich"),
        ("zuri", "Zürich"),
        ("Krakow", "Kraków, Poland"),
        ("Kraków", "Krakow, Poland"),
        ("lodz", "Łódź, Poland"),
        ("istanbul", "İstanbul, Turkey"),
        ("İstanbul", "Istanbul"),
        ("Sao Paulo", "São Paulo, Brazil"),
        ("Malmo", "Malmö"),
        ("strasse", "Hauptstraße 1, Berlin"),
        ("Straße", "Hauptstrasse 1, Berlin"),
        ("Bogota", "Bogotá, D.C., Colombia"),
        ("Copenhagen", "København, Denmark"),
        ("Montreal", "Montréal, Canada"),
        ("東京", "東京都, Japan"),
    ],
)
def test_an_accent_never_decides_a_match(term, location):
    assert _matches(term, location)


def test_a_typed_accent_finds_the_plain_spelling_even_where_the_table_has_none_accented():
    """The term carries the accent itself, so it is folded whatever the table holds."""
    assert matches("Ünknown", "Unknown, Nowhere", frozenset())
    assert not matches("Ünknown", "Unrelated", frozenset())


@pytest.mark.parametrize(
    ("term", "location"),
    [
        ("Bangalore", "Bengaluru, Karnataka, India"),
        ("Bengaluru", "Bangalore, KA"),
        ("Gurugram", "Gurgaon, Haryana"),
        ("Gurgaon", "Gurugram, HR, IN"),
        ("Munich", "München, Germany"),
        ("München", "Munich, Germany"),
        ("Muenchen", "Munich, Germany"),
        ("Cologne", "Köln, Germany"),
        ("Prague", "Praha, Czechia"),
        ("Nuremberg", "Nürnberg"),
        ("Bombay", "Mumbai, Maharashtra"),
        ("Madras", "Chennai, Tamil Nadu"),
        ("Chennai", "Madras, India"),
        ("Calcutta", "Kolkata, West Bengal"),
        ("Poona", "Pune, Maharashtra"),
        ("Cochin", "Kochi, Kerala"),
        ("Trivandrum", "Thiruvananthapuram, Kerala"),
        ("Baroda", "Vadodara, Gujarat"),
        ("Allahabad", "Prayagraj, UP"),
        ("Saigon", "Ho Chi Minh City, Vietnam"),
        ("Ho Chi Minh City", "Saigon"),
        ("Kiev", "Kyiv, Ukraine"),
        ("Kyiv", "Kiev"),
        ("Vienna", "Wien, Austria"),
        ("Warsaw", "Warszawa"),
        ("Rome", "Roma, Italy"),
        ("Bangalore, India", "Bengaluru, India"),
        ("Bangalore North", "Bengaluru North, Karnataka"),
    ],
)
def test_a_renamed_city_is_read_as_its_other_spellings(term, location):
    assert _matches(term, location)


@pytest.mark.parametrize(
    ("term", "location"),
    [
        # a spelling that also names another place is asked for one way only
        ("Wien", "Vienna, VA, United States"),
        ("Warszawa", "Warsaw, Indiana, United States"),
        ("Roma", "Rome, GA, United States"),
        ("Firenze", "Florence, SC, United States"),
        ("Napoli", "Naples, FL, United States"),
        # a whole word, not the letters inside another: "Madras" for Chennai is not "Madrasa"
        ("Chennai", "Madrasa Road, Delhi"),
        ("Mumbai", "Bombaymore, OK"),
        ("Pune", "Poonamallee, Tamil Nadu"),
        # nothing widened beyond the term's own alternatives
        ("Bangalore", "Mysore, Karnataka"),
        ("Zurich", "Zug, Switzerland"),
    ],
)
def test_an_alias_never_pulls_in_a_different_place(term, location):
    assert not _matches(term, location)


def test_the_term_typed_still_matches_as_a_substring_as_it_always_did():
    assert _matches("Madras", "Madrasa Road, Delhi")
    assert _matches("york", "New York, NY")
    assert _matches("Zurich", "Zurichsee, Switzerland")


@pytest.mark.parametrize(
    ("term", "location"),
    [
        ("St. Louis", "Saint Louis, MO"),
        ("St Louis", "St. Louis, MO"),
        ("Saint Louis", "St. Louis, Missouri"),
        ("St. Paul", "Saint Paul, MN"),
        ("Ft. Meade", "Fort Meade, MD"),
        ("Fort Worth", "Ft. Worth, TX"),
        ("fort", "Ft Lauderdale, FL"),
        ("Washington DC", "Washington, DC, United States"),
        ("Washington, DC", "Washington D.C."),
        ("Washington D.C.", "Washington DC"),
        ("NYC", "New York City, NY"),
        ("NYC", "New York, NY, United States"),
        ("New York City", "NYC"),
        ("Mexico City", "Ciudad de México, Mexico"),
        ("CDMX", "Mexico City"),
        ("Tel Aviv-Yafo", "Tel Aviv, Israel"),
        ("Cluj-Napoca", "Cluj, Romania"),
        ("Hong Kong SAR", "Hong Kong"),
        ("Frankfurt am Main", "Frankfurt, Germany"),
        ("New Delhi", "Delhi"),
        ("Czechia", "Czech Republic"),
        ("Turkey", "Türkiye"),
        ("Türkiye", "Turkey"),
    ],
)
def test_the_other_spellings_the_table_shows_in_hundreds_of_rows(term, location):
    assert _matches(term, location)


def test_a_word_is_not_an_abbreviation_of_a_longer_one():
    assert not _matches("Saint Louis", "Stockholm, Sweden")
    assert not _matches("Fort Worth", "Soft Worth")


def test_a_term_is_literal_text_never_a_pattern():
    assert _matches("c++", "C++ Hub, Berlin")
    assert not _matches("a.b", "axb")
    assert not _matches("new_york", "New York")
    assert _matches("new_york", "New_York")
    assert _matches("100%", "100% Remote")
    assert not _matches("100%", "100 Remote")
    assert _matches("AT\\T", "AT\\T Plaza")
    assert _matches("(remote)", "Berlin (Remote)")
    assert not _matches("[a-z]", "berlin")
    assert _matches("o'fallon", "O'Fallon, IL")
    # in a term the regex reads too, not only in the plain substring one
    assert _matches("zürich (hq)", "Zurich (HQ)")
    assert not _matches("zürich (hq)", "Zurich HQ")
    assert _matches("R&D zürich", "R&D Zurich")
    assert _matches("Winston-Salem", "Winston-Salem, NC")
    assert _matches("bangalore #1", "Bengaluru #1")


def test_a_bare_combining_mark_is_matched_as_itself_not_dropped():
    """It has no plain letter to fold to; dropping it would leave an empty pattern, which
    matches every row."""
    assert not _matches("́", "Berlin")
    assert _matches("́", "é")


def test_a_null_or_empty_location_matches_nothing():
    assert not _matches("berlin", None)
    assert not _matches("berlin", "")
    assert not _matches("zurich", None)


def test_a_term_is_cut_to_sixty_characters_of_what_was_typed():
    long = "a" * 59 + "%b"
    assert pattern(long, _ACCENTED) == pattern("a" * 59 + "%", _ACCENTED)
    assert _matches("a" * 59 + "%bbbb", "a" * 59 + "%")


def test_a_term_the_rules_leave_as_typed_stays_a_plain_substring():
    """No accent of its own, no word the table spells with accents, no renamed place: the old
    `lower(location) LIKE`, which is the cheaper predicate on a ranked page (ADR-0344)."""
    for term in ("london", "Berlin", "remote", "new york", "united states", "100%"):
        assert pattern(term, _ACCENTED) is None, term
    assert _matches("LONDON", "London, UK")
    assert not _matches("london", "Londres")


def test_a_term_the_rules_change_is_one_regex_pass():
    for term in ("Zurich", "Zürich", "Bangalore", "St. Louis", "Turkey"):
        regex = pattern(term, _ACCENTED)
        assert regex is not None and regex.startswith("(?i)"), term
    assert pattern("Bangalore", frozenset())  # a renamed place needs no vocabulary
    assert (
        pattern("zurich", frozenset()) is None
    )  # an empty vocabulary reads it as typed


@pytest.mark.parametrize(
    ("term", "changed"),
    [
        # "san" is inside "sánchez", but only "sán ..." would meet the accent
        ("san francisco", False),
        ("san", True),
        ("sanchez", True),
        ("anchez", True),
        ("sanc", True),
        ("berlin", False),  # "Überlingen" holds it, the accent before it
        ("berlingen", False),
        ("uberlin", True),
        ("zurich", True),
        ("urich", True),
        ("rich", False),  # "zürich" holds "rich" past its accent
        ("zurich, switzerland", True),
        ("switzerland", False),
        ("x zurich", True),  # a word after a separator ends the row's word: "…zürich"
        ("zurich x", True),
        ("urich x", True),
        ("zuric x", False),  # must end the row's word, and "zürich" does not end at "c"
    ],
)
def test_a_word_is_classed_only_where_it_can_meet_an_accent(term, changed):
    words = frozenset({"sAnchez", "Uberlingen", "zUrich"})
    assert (pattern(term, words) is not None) is changed


def test_the_alignment_rule_never_changes_which_rows_a_term_matches():
    """Reading a term as typed where it cannot meet an accent is only a cheaper predicate: for
    every substring of every location below, the rule keeps the rows an always-classed pattern
    keeps."""
    rows = [
        "Zürich, Switzerland", "Sánchez Peña, Spain", "Überlingen, Germany",
        "Hauptstraße 1, Berlin", "München", "Sao Paulo", "São Paulo, Brazil",
        "İstanbul, TR", "Ålesund", "Łódź, Poland", "San Francisco, CA", "Berlin",
    ]  # fmt: skip
    words = accented_words(rows)
    # the control: every word of every row written as if all its letters were accented
    everything = frozenset(
        word.upper() for row in rows for word in re.findall(r"[a-z]+", fold(row))
    )
    terms = set()
    for row in rows:
        folded = fold(row)
        for start in range(len(folded)):
            for end in range(start + 1, min(len(folded), start + 14) + 1):
                terms.add(folded[start:end].strip())
    terms.discard("")
    assert len(terms) > 500
    for term in terms:
        for row in rows:
            assert matches(term, row, words) == matches(term, row, everything), (
                term,
                row,
            )


def test_only_the_words_the_table_spells_with_accents_get_classes():
    regex = pattern("london zurich", _ACCENTED)
    assert regex is not None
    assert regex.startswith(
        "(?i)london\\ [z"
    )  # plain word literal, accented word classed
    assert "l[" not in regex


def test_a_clause_stays_small_however_many_aliases_a_term_holds():
    """Alias alternatives are capped: the worst case (three spellable words and a long tail of
    accented letters) stays under 20 KB."""
    worst = "bangalore mumbai chennai " + "s" * 35
    assert len(_sql(worst)) < 20_000
    assert len(_sql("a" * 60)) < 4_000
    assert (
        len(location_spelling.variants(fold(worst))) <= location_spelling.MAX_VARIANTS
    )


def test_every_spelling_in_the_vocabulary_is_written_folded_and_named_once():
    seen = {}
    for group in location_spelling.GROUPS:
        for spelling in group:
            assert fold(spelling) == spelling, spelling
            assert spelling not in seen, (spelling, group, seen.get(spelling))
            seen[spelling] = group
    for source, targets in location_spelling.ONE_WAY.items():
        assert fold(source) == source and source not in seen, source
        for target in targets:
            assert fold(target) == target, target


def test_the_vocabulary_holds_the_pairs_the_brief_names():
    named = {
        ("bengaluru", "bangalore"), ("gurugram", "gurgaon"), ("mumbai", "bombay"),
        ("chennai", "madras"), ("kolkata", "calcutta"), ("pune", "poona"),
        ("kochi", "cochin"), ("thiruvananthapuram", "trivandrum"),
        ("vadodara", "baroda"), ("prayagraj", "allahabad"), ("saigon", "ho chi minh"),
        ("kyiv", "kiev"), ("prague", "praha"), ("cologne", "koln"),
        ("vienna", "wien"), ("nuremberg", "nurnberg"),
    }  # fmt: skip
    for one, other in named:
        assert other in location_spelling.variants(one), (one, other)


# --- The Python rule and the SQL clause agree on a real table ---------------------------------

_LOCATIONS = [
    "Zürich, Switzerland", "Zurich, Switzerland", "Zug, Switzerland", "Kraków, Poland",
    "Krakow", "Bengaluru, Karnataka, India", "Bangalore", "Bengaluru North, KA",
    "Gurugram, Haryana", "Gurgaon", "München, Germany", "Munich", "Muenchen",
    "Köln", "Cologne, DE", "İstanbul, Turkey", "Istanbul", "São Paulo, Brazil",
    "Sao Paulo", "Vienna, VA, United States", "Wien, Austria", "Vienna, Austria",
    "Rome, GA, United States", "Roma, Italy", "Chennai", "Madras, India",
    "Madrasa Road, Delhi", "Saint Louis, MO", "St. Louis, MO", "St Louis",
    "Stockholm, Sweden", "Fort Meade, MD", "Ft. Meade, MD", "Washington, DC",
    "Washington D.C.", "Washington DC", "New York City", "NYC", "New York, NY",
    "New_York", "100% Remote", "O'Fallon, IL", "AT\\T Plaza", "C++ Hub",
    "Berlin (Remote)", "Hauptstraße 1, Berlin", "Hauptstrasse 1, Berlin",
    "東京都, Japan", "Москва, Россия", "Türkiye", "Turkey", "Ho Chi Minh City",
    "Saigon", "Tel Aviv-Yafo", "Tel Aviv", "Kyiv", "Kiev", "", "Remote",
    "R&D Zurich", "Zürich (HQ)", "Winston-Salem, NC", "Bengaluru #1", "Unknown",
    "Warsaw, Indiana, United States", "Warszawa", "Warsaw, Poland",
]  # fmt: skip

_TERMS = [
    "Zurich", "Zürich", "krakow", "Bangalore", "Bengaluru", "gurgaon", "Munich",
    "München", "Cologne", "istanbul", "sao paulo", "Vienna", "Wien", "Rome", "Roma",
    "Madras", "Chennai", "St. Louis", "Saint Louis", "fort", "Ft. Meade",
    "Washington DC", "NYC", "New York City", "new_york", "100%", "O'Fallon",
    "AT\\T", "c++", "(remote)", "strasse", "Straße", "東京", "Москва", "Turkey",
    "Türkiye", "Saigon", "Tel Aviv-Yafo", "Kiev", "a", "Bangalore, India", "x'; --",
    "london", "remote", "R&D zurich", "zürich (hq)", "Winston-Salem", "bangalore #1",
    "Ünknown", "Warsaw", "Warszawa",
]  # fmt: skip

_TABLE_ACCENTS = accented_words(_LOCATIONS)


def _sql(term: str) -> str:
    """The where-clause `build_filter` compiles for ``term`` against `_LOCATIONS`'s table."""
    caps = IndexCapabilities(
        atses=(),
        has_first_seen=False,
        has_min_salary_annual=False,
        accented_words=_TABLE_ACCENTS,
    )
    clause = build_filter(SearchFilters(location=term), caps)
    assert clause is not None
    return clause


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    lancedb = pytest.importorskip("lancedb")
    db = lancedb.connect(tmp_path_factory.mktemp("location_spelling_db"))
    rows = [{"id": str(i), "location": loc} for i, loc in enumerate(_LOCATIONS)]
    return db.create_table("jobs", rows)


def _hits(table, term: str) -> set[str]:
    found = table.search().where(_sql(term)).limit(1000).to_arrow()
    return set(found["id"].to_pylist())


def test_the_compiled_clause_agrees_with_matches_on_a_real_table(table):
    """DataFusion and `re` run one regex string, and the plain `LIKE` is a lowercase substring:
    they select the same rows for every term."""
    for term in _TERMS:
        expected = {
            str(i)
            for i, loc in enumerate(_LOCATIONS)
            if matches(term, loc, _TABLE_ACCENTS)
        }
        assert _hits(table, term) == expected, term


def test_the_folded_clause_keeps_every_row_the_substring_clause_kept(table):
    """Nothing the old `lower(location) LIKE '%term%'` found is lost: the new clause is a
    superset of it for every plain term."""
    plain = [t for t in _TERMS if not any(c in t for c in "%_\\'")]
    for term in plain:
        old = set(
            table.search()
            .where(f"lower(location) LIKE '%{term.lower()}%'")
            .limit(1000)
            .to_arrow()["id"]
            .to_pylist()
        )
        assert old <= _hits(table, term), term


@pytest.mark.parametrize(
    "spellings",
    [
        ("Bangalore", "Bengaluru", "BENGALURU"),
        ("Zürich", "Zurich", "ZURICH"),
        ("Kraków", "Krakow", "Cracow"),
        ("St. Louis", "Saint Louis", "St Louis"),
        ("Vienna", "Wien"),
        ("Tel Aviv-Yafo", "Tel Aviv"),
        ("Ho Chi Minh City", "Saigon"),
    ],
)
def test_place_key_is_one_key_for_every_spelling_the_filter_reads_alike(spellings):
    """ADR-0367: the Where-snapshot groups a country's cities by this key."""
    assert len({location_spelling.place_key(s) for s in spellings}) == 1


def test_place_key_keeps_other_places_apart():
    keys = {location_spelling.place_key(s) for s in ("Pune", "Mumbai", "Madrid")}
    assert len(keys) == 3
    # A spelling inside a longer word is not the renamed place: "Madrasa" is not Madras.
    assert location_spelling.place_key("Madrasa") == "madrasa"


def test_a_plain_term_compiles_to_the_old_clause_exactly():
    assert _sql("london") == "lower(location) LIKE '%london%'"
    assert _sql("new_york") == r"lower(location) LIKE '%new\_york%'"


def test_a_changed_term_compiles_to_one_regexp_like_over_location():
    for term in ("Zurich", "Bangalore", "St. Louis", "O'Fallon zurich"):
        sql = _sql(term)
        assert sql.count("regexp_like(") == 1 and sql.startswith(
            "regexp_like(location, '(?i)"
        )
        assert "lower(" not in sql
    assert _sql("O'Fallon zurich").count("''") == 1
    assert _sql("x' zurich; DROP TABLE jobs; --").count("''") == 1
