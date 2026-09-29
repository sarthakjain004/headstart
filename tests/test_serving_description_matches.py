"""Tests for `headstart.serving.description_matches` (ADR-0320).

Contracts: every where-clause it answers keeps exactly the rows the compiled one keeps, on a real
LanceDB table; a description keyword's rows are found once, whichever route asks first, and the
next page and the facet total read no description; past the row list's bound the compiled clause
comes back as it was.
"""

from __future__ import annotations

import threading
import time

import pytest

from headstart.search_filters.compiler import (
    IndexCapabilities,
    SearchFilters,
    build_filter,
    with_extra,
)
from headstart.serving.description_matches import DescriptionMatches

lancedb = pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")

_CAPABILITIES = IndexCapabilities(
    atses=["greenhouse", "lever"],
    has_first_seen=True,
    has_min_salary_annual=True,
    has_description=True,
)

#: (ats, title, location, remote, description) per Job.
_ROWS = [
    (
        "lever",
        "Backend Engineer",
        "Berlin, Germany",
        False,
        "We offer visa sponsorship.",
    ),
    ("lever", "Data Engineer", "Munich", True, "Visa\nsponsorship and relocation."),
    (
        "lever",
        "ML Engineer",
        "Berlin",
        False,
        "No visa-sponsorship; nonrelocation role.",
    ),
    ("greenhouse", "Frontend Engineer", "Amsterdam", True, "Relocation package."),
    ("greenhouse", "SRE", "Amsterdam", False, "sponsorship of your visa"),
    ("greenhouse", "Rust Engineer", "London", True, None),
    ("greenhouse", "Kubernetes Admin", "Berlin", True, "kubernetes, relocation"),
    ("lever", "Engineer (Relocation)", "Paris", False, "Hybrid."),
]


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    from headstart.ingest.index import _schema

    rows = [
        {
            "id": f"{ats}:acme:{n}",
            "ats": ats,
            "title": title,
            "location": location,
            "remote": remote,
            "description": description,
            "description_stored": description is not None,
            "vector": [1.0, float(n), 0.0, 0.0],
        }
        for n, (ats, title, location, remote, description) in enumerate(_ROWS)
    ]
    db = lancedb.connect(tmp_path_factory.mktemp("description_matches"))
    return db.create_table("jobs", pa.Table.from_pylist(rows, schema=_schema(4)))


def _ids(table, where: str | None) -> set[str]:
    query = table.search()
    if where:
        query = query.where(where)
    found = query.with_row_id(True).select(["id"]).to_arrow()
    return set(found.column("id").to_pylist())


_KEYWORDS = [
    {"kw": "relocation", "kw_in": "description"},
    {"kw": '"visa sponsorship"', "kw_in": "description"},
    {"kw": "visa sponsorship", "kw_in": "description"},
    {"kw": "relocation", "kw_in": "both"},
    {"kw": "nothingmatches", "kw_in": "description"},
    # Quote marks alone compile to no keyword at all (a review found them failing an assert).
    {"kw": '"', "kw_in": "description"},
    {"kw": '" "', "kw_in": "both"},
    {"kw": "relocation", "kw_in": "title"},
    {},
]
_OTHERS = [{}, {"remote": True}, {"ats": "lever"}, {"location": "berlin"}]


@pytest.mark.parametrize(
    "row_list_max", [100, 3, 1], ids=["named", "narrowed-first", "too-many"]
)
@pytest.mark.parametrize("others", _OTHERS, ids=lambda o: "-".join(o) or "none")
@pytest.mark.parametrize(
    "keyword", _KEYWORDS, ids=lambda k: "-".join(k.values()) or "none"
)
def test_every_clause_keeps_the_compiled_clauses_rows(
    table, keyword, others, row_list_max
):
    filters = SearchFilters(**keyword, **others)
    extra = "ats <> 'workday'"
    matches = DescriptionMatches(table, _CAPABILITIES, row_list_max=row_list_max)
    compiled = with_extra(build_filter(filters, _CAPABILITIES), extra)
    answered = matches.where(filters, extra)
    assert _ids(table, answered) == _ids(table, compiled)
    # asked again, from what was kept
    assert matches.where(filters, extra) == answered


def test_the_rows_are_named_by_row_id_and_a_count_can_read_them(table):
    matches = DescriptionMatches(table, _CAPABILITIES)
    filters = SearchFilters(kw="relocation", kw_in="description", remote=True)
    named = matches.where(filters, None)
    assert "_rowid IN (" in named and "description" not in named
    assert table.count_rows(filter=named) == 3


def test_nothing_matching_is_named_as_no_row(table):
    matches = DescriptionMatches(table, _CAPABILITIES)
    filters = SearchFilters(kw="nothingmatches", kw_in="description")
    assert matches.where(filters, None) == "false"


def test_past_the_row_list_the_compiled_clause_comes_back(table):
    matches = DescriptionMatches(table, _CAPABILITIES, row_list_max=1)
    filters = SearchFilters(kw="relocation", kw_in="description")
    assert matches.where(filters, None) == build_filter(filters, _CAPABILITIES)


def test_no_filter_at_all_is_still_no_clause(table):
    assert DescriptionMatches(table, _CAPABILITIES).where(SearchFilters(), None) is None


class _Recording:
    """The real table, recording each where-clause a read of it names; ``hold`` makes every
    read wait until it is set, so two requests can be made to overlap."""

    def __init__(self, table):
        self._table = table
        self.reads: list[str] = []
        self.hold = threading.Event()
        self.hold.set()
        self._lock = threading.Lock()

    def search(self):
        recording = self

        class _Query:
            def __init__(self, query):
                self._query = query

            def where(self, clause):
                with recording._lock:
                    recording.reads.append(clause)
                return _Query(self._query.where(clause))

            def to_arrow(self):
                recording.hold.wait(5)
                return self._query.to_arrow()

            def __getattr__(self, name):
                attribute = getattr(self._query, name)
                if not callable(attribute):
                    return attribute
                return lambda *a, **k: _Query(attribute(*a, **k))

        return _Query(self._table.search())


def _description_reads(recording: _Recording) -> list[str]:
    return [r for r in recording.reads if "description" in r]


def test_a_keyword_is_read_once_then_every_ask_reuses_it(table):
    """The ranked page, the facet total and page 2 ask the same where-clause: one finding.
    The other filters' rows are read first; then the literal, and the exact clause, read only
    the rows named before them."""
    recording = _Recording(table)
    matches = DescriptionMatches(recording, _CAPABILITIES)
    filters = SearchFilters(kw="relocation", kw_in="description", location="berlin")
    first = matches.where(filters, None)
    rest, loose, exact = recording.reads
    assert rest == "lower(location) LIKE '%berlin%'"
    assert "_rowid IN (" in loose and "(?i)relocation'" in loose
    assert "_rowid IN (" in exact and "(^|[^a-z0-9])relocation" in exact
    for _ in range(3):
        assert matches.where(filters, None) == first
    assert len(recording.reads) == 3


def test_past_the_row_list_the_other_filters_read_only_the_literals_candidates(table):
    """With the other filters keeping all 8 rows, past a row list of 5, the literal is looked
    for over the whole table, once per keyword, and the other filters read only its 4 rows."""
    recording = _Recording(table)
    matches = DescriptionMatches(recording, _CAPABILITIES, row_list_max=5)
    keyword = SearchFilters(kw="relocation", kw_in="description")
    everyone = "ats <> 'workday'"
    for extra in (None, everyone, everyone):
        answered = matches.where(keyword, extra)
        assert "_rowid IN (" in answered
        assert _ids(table, answered) == _ids(
            table, with_extra(build_filter(keyword, _CAPABILITIES), extra)
        )
    literal = "(regexp_like(description, '(?i)relocation'))"
    assert recording.reads.count(literal) == 1
    final = recording.reads[-1]
    assert "_rowid IN (" in final and everyone in final


def test_the_coverages_rows_are_read_once_however_often_it_asks(table):
    """The coverage counts the rows every filter but the keyword keeps, on each request."""
    recording = _Recording(table)
    matches = DescriptionMatches(recording, _CAPABILITIES)
    unkeyed = SearchFilters(location="berlin")
    named = matches.where(unkeyed, None)
    assert _ids(table, named) == _ids(table, build_filter(unkeyed, _CAPABILITIES))
    assert matches.where(unkeyed, None) == named
    assert recording.reads == ["lower(location) LIKE '%berlin%'"]


def test_two_requests_at_once_share_one_finding(table):
    recording = _Recording(table)
    matches = DescriptionMatches(recording, _CAPABILITIES)
    filters = SearchFilters(kw='"visa sponsorship"', kw_in="description")
    recording.hold.clear()
    answers: list[str | None] = []
    threads = [
        threading.Thread(target=lambda: answers.append(matches.where(filters, None)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.3)  # both asking: one reading, held; the other waiting for it
    recording.hold.set()
    for thread in threads:
        thread.join(10)
    assert len(answers) == 2 and answers[0] == answers[1]
    assert len(_description_reads(recording)) == 2  # the literal, then the exact clause
