"""India gazetteer (ADR-0024): alias hygiene + the where-clauses run against real LanceDB
LIKE semantics, including every substring trap the inventory vetting caught."""

from __future__ import annotations

import pytest

from headstart.geo import CITIES, DROPDOWN, REGIONS, _STATES, where

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
    aliases = [a for aliases in CITIES.values() for a in aliases] + list(_STATES)
    for a in aliases:
        assert a == a.lower() and "'" not in a and "%" not in a, a
    for trap in ("salt lake", "wai", "salem", "punjab", "verna", "whitefield", "supa"):
        assert trap not in aliases, f"vetoed trap alias reintroduced: {trap}"


def test_dropdown_entries_resolve():
    for place in DROPDOWN:
        assert where(place) is not None, place
