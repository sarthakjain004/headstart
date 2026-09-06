"""India gazetteer (ADR-0024): alias hygiene + the where-clauses run against a real LanceDB
table, including every substring trap the inventory vetting caught."""

from __future__ import annotations

import pytest

from headstart.geo import (
    CITIES,
    DROPDOWN,
    EXCLUDE,
    IND_EXCLUDE,
    IND_FORMS,
    INDIA_EXCLUDE,
    REGIONS,
    STATES,
    SUBDIVISIONS,
    _anchored,
    _rx,
    where,
)

# Real-shaped location strings: (location, in_india, cities_it_belongs_to)
_ROWS = [
    ("Bangalore North", True, {"bengaluru"}),
    ("Bengaluru, Karnātaka, India", True, {"bengaluru"}),
    ("Banagalore", True, {"bengaluru"}),  # observed typo
    ("IN-Pune", True, {"pune"}),
    ("Gurgaon Kty.", True, {"gurgaon", "delhi ncr"}),
    # observed typo without the 'delhi' substring
    ("New Delihi", True, {"delhi", "delhi ncr"}),
    ("Remote - India", True, set()),
    ("India", True, set()),
    ("Surat City", True, {"surat"}),
    ("Karnataka, IN", True, set()),  # state-only residue
    ("Hyderaba", True, {"hyderabad"}),  # observed typo
    ("Noida", True, {"noida", "delhi ncr"}),
    ("Kalyani Nagar, Pune", True, {"pune"}),  # must NOT hit thane's 'kalyan'
    # country-tag rows carrying no city name at all (2026-08-25 audit: 429 such rows)
    ("IND", True, set()),
    ("IND-BLR-Divyasree Technopolis", True, set()),
    ("IND BNGL FL2-3 TWR 3", True, set()),
    ("Remote - IND", True, set()),
    ("Remote (IND)", True, set()),
    ("IN_India_WFH", True, set()),  # no word boundary around india: substring must stay
    ("Vemagal, KA, IN", True, set()),  # workday subdivision tail
    ("Jagiroad, AS, IN", True, set()),
    ("Varanasi, UP, IN", True, set()),
    # the country term is a substring, so the guard must keep a real India row that happens
    # to carry a bad country tag
    ("Nagpur, Maharashtra, British Indian Ocean Territory", True, {"nagpur"}),
    # traps — must NOT match india or any city
    ("Indianapolis, IN", False, set()),
    ("Fort Wayne, IN", False, set()),
    ("Salt Lake City, UT", False, set()),  # contaminated the raw inventory
    ("Surat Thani, Thailand", False, set()),
    ("Taiwan - Remote", False, set()),  # 'wai' trap
    ("Salem, OR", False, set()),
    ("Lahore, Punjab", False, set()),  # punjab deliberately not a state alias
    ("Governador Valadares, Brazil", False, set()),  # 'verna' trap
    ("Whitefield, Manchester", False, set()),
    ("Berlin, Germany", False, set()),
    # 'india' as a substring of a US place name
    ("Indian Head, MD", False, set()),
    ("Indialantic, FL", False, set()),
    ("Indianola, PA, United States", False, set()),
    ("Indian Springs, NV", False, set()),
    ("Diego Garcia, British Indian Ocean Territory", False, set()),
    # IND is also Indianapolis's IATA code
    ("IND U; CVG SD; United States, PA, Philadelphia - Remote; MKE W", False, set()),
    (
        "Grayslake, Ind",
        False,
        set(),
    ),  # Illinois; why the IND form is '% - ind', not '% ind'
]


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    # lancedb is in the `embed` extra, which the quality CI job doesn't install — the
    # behavioral tests skip there; the pure-python hygiene tests below still run.
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    db = lancedb.connect(tmp_path_factory.mktemp("db"))
    return db.create_table("locs", pa.table({"location": [loc for loc, _, _ in _ROWS]}))


def _hits(table, clause: str) -> set[str]:
    rows = table.search().where(clause, prefilter=True).limit(len(_ROWS)).to_list()
    return {r["location"] for r in rows}


def test_india_clause_recall_and_traps(table):
    hits = _hits(table, where("india"))
    assert hits == {loc for loc, in_india, _ in _ROWS if in_india}


def test_city_clauses(table):
    # strict equality: a city clause finds exactly its own rows — no trap rows, and no
    # cross-city leaks (e.g. thane's 'kalyan' must not swallow Pune's Kalyani Nagar)
    for place in list(CITIES) + list(REGIONS):
        hits = _hits(table, where(place))
        assert hits == {loc for loc, _, cities in _ROWS if place in cities}, place


def test_unknown_place_is_none():
    assert where("mars") is None
    assert where("") is None


def test_alias_hygiene():
    aliases = [a for aliases in CITIES.values() for a in aliases] + list(STATES)
    for a in aliases:
        # `_` joins `%` here: the aliases are substrings in a regex alternation now, and the
        # LIKE-to-regex equivalence holds only because none of them carries a LIKE wildcard.
        # One that did would silently NARROW the filter — a wildcard becoming a literal.
        assert a, "an empty alias would make its whole alternation match every row"
        assert a == a.lower() and "'" not in a and "%" not in a and "_" not in a, a
    for trap in ("salt lake", "wai", "salem", "punjab", "verna", "whitefield", "supa"):
        assert trap not in aliases, f"vetoed trap alias reintroduced: {trap}"
    # 2026-08-25 audit re-confirmed these against the live table: every one is a world
    # substring trap, not a missing Indian city. salem=Jerusalem/Winston-Salem,
    # kota=Dakota, agra=Agrate Brianza, erode=Wernigerode.
    for trap in ("kota", "agra", "erode"):
        assert trap not in aliases, f"vetoed trap alias reintroduced: {trap}"


def test_country_tag_terms_are_sql_safe():
    # Every one of these is interpolated straight into a where-clause, so a stray quote would
    # be a broken query and an uppercase term would silently never match lower(location).
    guards = tuple(t for ts in EXCLUDE.values() for t in ts)
    for term in IND_FORMS + SUBDIVISIONS + IND_EXCLUDE + INDIA_EXCLUDE + guards:
        assert term == term.lower() and "'" not in term, term
    # The exclude terms become their own regex alternation, so a stray `%` would be matched
    # literally rather than as a wildcard and the guard would never fire.
    for term in IND_EXCLUDE + INDIA_EXCLUDE + guards:
        # A wildcard in a *guard* widens rather than narrows: the guard stops firing, and the
        # collision it was vetted to exclude comes back.
        assert term and "%" not in term and "_" not in term, term
    # Subdivision codes are two letters and only ever used inside a ', {code}, in' anchor;
    # a longer or looser one would match free text.
    for code in SUBDIVISIONS:
        assert len(code) == 2 and code.isalpha(), code


def test_ind_is_never_a_bare_substring():
    """The whole IND rule rests on anchoring; a bare '%ind%' would claim half the world.

    Asserted on the pattern rather than only through the table rows, because the table can only
    catch the strings someone thought to add - and the failure mode here is silent and huge
    (indore, indianapolis, 'King Street Ind Estate', every 'Industrial Area').
    """
    for form in IND_FORMS:
        # 'ind' must be pinned on BOTH sides: against a string start or a real delimiter on the
        # left, and against a delimiter or the string end on the right. '%ind%' pins neither and
        # 'ind%' pins only the left, which would claim Indore and every "Industrial Area".
        head, _, tail = form.partition("ind")
        assert head in ("", "%(", "% - "), form
        assert tail == "" or tail[0] in " -)", form


def test_dropdown_entries_resolve():
    for place in DROPDOWN:
        assert where(place) is not None, place


def test_regex_escaping_keeps_an_alias_literal():
    """The aliases moved from LIKE patterns into a regex alternation, where `.` and `(` mean
    something. `re.escape` is what keeps them literal — without it "(ind)" would be a capture
    group and any alias carrying a dot would become a wildcard, silently widening the filter.
    """
    assert _rx("(ind)") == r"\(ind\)"
    assert _rx("a.b") == r"a\.b"
    # SQL-literal safety on top of regex safety: two different escapes, both needed.
    assert _rx("o'brien").count("''") == 1


def test_ind_forms_keep_their_anchoring_through_the_translation():
    """The IND rule *is* anchoring (`test_ind_is_never_a_bare_substring`), so the LIKE-to-regex
    step is exactly where it could be lost — and losing it is silent and huge: a bare `ind`
    claims Indore and every "Industrial Area". `%` means "unanchored at that end"; its absence
    becomes a `^` or `$`.
    """
    translated = {form: _anchored(form) for form in IND_FORMS}
    for form, rx in translated.items():
        assert rx.startswith("^") == (not form.startswith("%")), form
        assert rx.endswith("$") == (not form.endswith("%")), form
    # The two that pin nothing on the left must still pin something on the right, and vice
    # versa — the property the constants' own test asserts, carried through the translation.
    assert translated["ind-%"] == r"^ind\-"
    assert translated["% - ind"] == r"\ \-\ ind$"


def test_ind_forms_carry_no_interior_wildcard():
    """`_anchored` reads a `%` only at the ends. An interior one would be stripped by neither
    branch and reach the regex as a literal `%`, silently matching nothing — and `_` is not read
    as a wildcard at all. Both assumptions are asserted here rather than left in a docstring.
    """
    for form in IND_FORMS:
        assert "_" not in form, form
        assert "%" not in form.strip("%"), form
