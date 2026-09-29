"""India gazetteer (ADR-0024): alias hygiene + the where-clauses run against a real LanceDB
table, including every substring trap the inventory vetting caught."""

from __future__ import annotations

import hashlib

import pytest

from headstart.search_filters.india_gazetteer import (
    CITIES,
    DROPDOWN,
    EXCLUDE,
    IND_EXCLUDE,
    IND_FORMS,
    INDIA_EXCLUDE,
    REGIONS,
    STATES,
    SUBDIVISIONS,
    TOWNS,
    _anchored,
    _rx,
    classify,
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
    ("IN", True, set()),  # ISO alpha-2 as the whole string: SuccessFactors' feed, iCIMS
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
    ("Hyderabad, Sindh, Pakistan", False, set()),  # ADR-0322
    ("Taiwan - Remote", False, set()),  # 'wai' trap
    ("Salem, OR", False, set()),
    ("Lahore, Punjab", False, set()),  # punjab deliberately not a state alias
    ("Governador Valadares, Brazil", False, set()),  # 'verna' trap
    ("Whitefield, Manchester", False, set()),
    ("Berlin, Germany", False, set()),
    # 'goa' inside Brazilian "lagoa" (lagoon)
    ("Maceió, Alagoas, Brasil", False, set()),
    ("Sete Lagoas, Minas Gerais, Brasil", False, set()),
    ("Arapiraca, Alagoas, Brasil", False, set()),
    ("Três Lagoas, Mato Grosso do Sul, Brasil", False, set()),
    ("Lagoa Santa, Minas Gerais, Brasil", False, set()),
    ("Lagoa da Prata, Minas Gerais, Brasil", False, set()),
    ("Murici, Alagoas, Brasil", False, set()),
    ("Pilar, Alagoas, Brasil", False, set()),
    ("Rio Largo, Alagoas, Brasil", False, set()),
    # 'anand' inside unrelated Brazilian/US place names
    ("Sananduva, Rio Grande do Sul, Brasil", False, set()),
    ("Canandaigua, NY, United States", False, set()),
    # true positives that must survive the 'goa'/'anand' guards above
    ("Panaji, Goa, India", True, {"goa"}),
    ("Goa, India", True, {"goa"}),
    ("Anand, Gujarat, India", True, {"anand"}),
    # 'india' as a substring of a US place name
    ("Indian Head, MD", False, set()),
    ("Indialantic, FL", False, set()),
    ("Indianola, PA, United States", False, set()),
    ("Indian Springs, NV", False, set()),
    ("Diego Garcia, British Indian Ocean Territory", False, set()),
    ("Indian Creek Correctional Center", False, set()),
    ("2200 N Indianwood Ave, Broken Arrow, OK, USA", False, set()),
    ("630 Indian Street, Savannah, GA, USA", False, set()),
    ("Indiantown, FL, United States", False, set()),
    # IND is also Indianapolis's IATA code
    ("IND U; CVG SD; United States, PA, Philadelphia - Remote; MKE W", False, set()),
    (
        "Grayslake, Ind",
        False,
        set(),
    ),  # Illinois; why the IND form is '% - ind', not '% ind'
    # ADR-0347: "india" is a whole word, so a longer word that contains it is not the country.
    ("Xindian District, New Taipei", False, set()),
    ("Florida; Indian Harbour Beach", False, set()),
    ("Little India, Singapore", False, set()),
    # ADR-0347: a state name that is also a place elsewhere, and city aliases hidden mid-word
    ("Debrecen, Hajdú-Bihar, Hungary", False, set()),
    ("DEBRECEN, HAJDÚ BIHAR, Hungary", False, set()),
    ("Delhi New York", False, set()),
    ("Boardman, OR; Delhi, LA (Delhi Plant)", False, set()),
    ("Madras, OR, United States", False, set()),
    ("Inashiki-gun, Ibaraki, Japan", False, set()),  # 'nashik'
    ("Istanbul Kagithane", False, set()),  # 'thane'
    ("US / Cananda", False, set()),  # 'anand'
    ("Maladzyechna, Minsk Region, Belarus", False, set()),  # 'malad'
    # "Hyderabad, PK" stays tagged: its one row is an India job (the posting requires authorization
    # to work in India), so the ", pk" veto that looked obvious would have dropped a real row.
    ("Hyderabad, PK", True, {"hyderabad"}),
    # ADR-0347: a bare "Town, IN" is Indiana far more often than India, and only shapes India
    # alone uses read as the country tag
    ("Indianapolis, IN, IN", False, set()),
    ("Muscatatuck, IN", False, set()),
    ("Evansville, IN or Baltimore, MD, IN", False, set()),
    ("Crane, IN, Indi, Un", False, set()),
    # SuccessFactors' cut "Indonesia", not the ISO code
    ("Others, Bant, In", False, set()),
    ("Jakarta, Othe, In", False, set()),
    ("Whitestown, IN, 46077", False, set()),  # a five-digit ZIP; an Indian PIN has six
    # ADR-0347: a town name is a whole word, so it cannot hide inside a longer place
    ("Korbach, Germany", False, set()),  # 'korba'
    ("Kota Kinabalu, Malaysia", False, set()),  # 'kota' stays out of the list
    # ADR-0347: shapes that only India uses
    ("KA, IN", True, set()),  # a subdivision code at the start of the string
    ("MH, IN, 410208", True, set()),  # ...and an Indian PIN after the country
    ("Jamnagar, GJ, IN, 361004", True, set()),
    ("IN, 201301", True, set()),
    ("Singahalli, Autoliv Asia - AAS, IN", True, set()),  # "Town, Plant, IN"
    ("Chakan, Chakan_MahTower, IN", True, set()),
    ("CHEYYAR CC, Cheyyar-MSPT, IN", True, set()),
    ("IND, Alwaye-South 1", True, set()),  # 'IND,' as a prefix
    ("IND, Remote", True, set()),
    (
        "Remote, IN",
        True,
        set(),
    ),  # "Remote, <ISO country>"; not "Remote, IN, US" or a state list
    ("Remote, IN, US", False, set()),
    ("Remote - CA; Remote - IN; Remote - KY; United States of America", False, set()),
    # ADR-0347: towns read off the country-less rows of the 2026-09-29 table
    ("Mundra", True, set()),
    ("Sri City, Andh, IN", True, set()),
    ("Miraroad", True, set()),
    ("Sahnewal", True, set()),
    ("Parwanoo-Hmachal", True, set()),
    ("Barrackpore; Malda; Siliguri", True, set()),
    ("Kanchipuram, IN", True, set()),
    ("Arunachal Pradesh, IN", True, set()),
    ("Chh Sambhajinagar", True, {"aurangabad"}),
    ("Bengalore, IN", True, {"bengaluru"}),  # observed typos of a city already held
    ("gurugarm", True, {"gurgaon", "delhi ncr"}),
    ("gaziabad", True, {"ghaziabad", "delhi ncr"}),
    ("manglore", True, {"mangaluru"}),
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


def test_where_india_is_unchanged_by_classify_s_addition():
    """`classify()` (ADR-0138) is a from-scratch Python reimplementation of this rule, not a
    refactor of `where()` — but pin the exact compiled clause anyway, as a tripwire independent
    of `test_india_clause_recall_and_traps`'s row-matching check: a future edit could change the
    SQL text without changing which rows it matches (e.g. a harmless-looking reordering that
    still passes recall but breaks something reading the raw clause string, like a query-plan
    cache keyed on it).

    A hash, not the ~3KB literal itself — copying that string by hand into a test is exactly how
    a transcription slip would go unnoticed (one did, while drafting this test: `surat`/`thane`
    swapped, caught only because this assertion failed against the real output). ADR-0024/
    ADR-0086/ADR-0138 cite 3,068 chars; the `goa`/`anand`/`INDIA_EXCLUDE` guards below moved it
    to 3,301, the whole-string alpha-2 "IN" (`IN_EXACT`) to 3,327, the Pakistan guard on
    `hyderabad` (ADR-0322) to 3,419, and the whole-word, tail and town rules with the guards
    beside them (ADR-0347) to 4,350.
    """
    clause = where("india")
    assert len(clause) == 4350
    assert hashlib.sha256(clause.encode()).hexdigest() == (
        "8912a43292a03e0cae61ca7fab3091ba933a07e8971ccb69a989e7f80bd81416"
    ), (
        "the compiled clause moved — if this is a deliberate CITIES/STATES/etc. data change, "
        "recompute the hash (hashlib.sha256(where('india').encode()).hexdigest()) and update "
        "this pin alongside test_classify_agrees_with_the_country_level_rule_on_every_oracle_"
        "row; if it's unexpected, that's exactly what this test exists to catch"
    )


def test_classify_agrees_with_the_country_level_rule_on_every_oracle_row():
    """The materialized `country` column's correctness gate (ADR-0138): `classify()` must agree
    with `where("india")` on every real location string here, traps included — this is what
    actually keeps the two paths from drifting, not the shared-constants argument in
    `classify`'s own docstring. Pure Python, no lancedb needed, so it runs in the base CI job
    too."""
    for loc, in_india, _ in _ROWS:
        assert (classify(loc) == "IN") == in_india, loc


def test_classify_of_no_location_is_none():
    assert classify(None) is None
    assert classify("") is None


def test_the_country_code_after_a_plant_or_town_is_read_in_upper_case_only():
    """SuccessFactors cuts every part of a location to four letters, so "In" is India *or*
    Indonesia there ("Ramanagara, Karn, In" against "Others, Bant, In"; the first is a made-up
    string in the real shape), while the ISO code is "IN". The three-part tail reads only the
    code (ADR-0347)."""
    assert classify("Ramanagara, Karn, IN") == "IN"
    assert classify("Ramanagara, Karn, In") is None
    assert classify("Jakarta, Othe, In") is None


@pytest.fixture(scope="module")
def table_with_country(tmp_path_factory):
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    db = lancedb.connect(tmp_path_factory.mktemp("db_country"))
    return db.create_table(
        "locs",
        pa.table(
            {
                "location": [loc for loc, _, _ in _ROWS],
                "country": [classify(loc) for loc, _, _ in _ROWS],
            }
        ),
    )


def test_materialized_country_column_agrees_with_the_where_clause(table_with_country):
    """The direct analog of `test_india_clause_recall_and_traps` for the fast path (ADR-0138):
    confirms `country = 'IN'` and `where("india")` select identical row sets."""
    hits = _hits(table_with_country, "country = 'IN'")
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
    aliases = (
        [a for aliases in CITIES.values() for a in aliases] + list(STATES) + list(TOWNS)
    )
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
    # ADR-0347: each of these names an Indian place and also a place, a person or a word
    # elsewhere, so no untagged row read India on the strength of it (kota: Kota Kinabalu and
    # Kota Bharu; kalina: a Polish village; blore: an English village; parsa: Nepal's district;
    # shalimar: Florida; patan: Nepal's city; mirzapur: Bangladesh; hassan: a given name).
    for trap in ("kalina", "blore", "parsa", "shalimar", "patan", "mirzapur", "hassan"):
        assert trap not in aliases, f"unvetted ambiguous alias added: {trap}"


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
        assert tail == "" or tail[0] in " -,)", form


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
