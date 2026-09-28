"""Where a set of Boards' served jobs are — `headstart.serving.location_counts` (ADR-0275).

Contracts: one filtered scan that reads only the `location` column, bounded; places counted as
served, whitespace collapsed and nothing else merged; empty locations counted apart; most first,
ties by name; and a scan that reaches its bound says so.
"""

from __future__ import annotations

from headstart.serving import location_counts


class _Scan:
    """A lancedb table's plain scan: records what it was asked, answers ``rows``."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = {}

    def search(self, *args):
        self.asked["query"] = args
        return self

    def where(self, clause, prefilter=False):
        self.asked["where"] = (clause, prefilter)
        return self

    def select(self, columns):
        assert isinstance(columns, list), "lancedb rejects a tuple"
        self.asked["select"] = columns
        return self

    def limit(self, n):
        self.asked["limit"] = n
        self.rows = self.rows[:n]
        return self

    def to_list(self):
        return [{"location": place} for place in self.rows]


def test_it_scans_only_the_location_column_of_the_rows_the_clause_selects():
    table = _Scan(["Berlin"])
    location_counts.top(table, "(lower(id) LIKE 'lever:acme:%')", 10)
    assert table.asked == {
        "query": (),
        "where": ("(lower(id) LIKE 'lever:acme:%')", True),
        "select": ["location"],
        "limit": location_counts.MAX_ROWS,
    }


def test_places_are_counted_as_served_most_first_ties_by_name():
    rows = ["Seattle, WA", "Pune", "Seattle,  WA ", "seattle, wa", "Austin", "Pune"]
    rows += ["", None, "   "]
    answer = location_counts.top(_Scan(rows), "x", 3)
    assert answer == {
        "jobs": 9,
        "unstated": 3,
        "distinct": 4,
        "capped": False,
        "locations": [
            # Whitespace collapses; case and spelling do not merge.
            {"location": "Pune", "count": 2},
            {"location": "Seattle, WA", "count": 2},
            {"location": "Austin", "count": 1},
        ],
    }


def test_a_scan_that_reaches_its_bound_says_so(monkeypatch):
    monkeypatch.setattr(location_counts, "MAX_ROWS", 3)
    answer = location_counts.top(_Scan(["A", "B", "C", "D"]), "x", 10)
    assert answer["jobs"] == 3 and answer["capped"] is True


def test_no_rows_is_an_empty_answer_not_an_error():
    assert location_counts.top(_Scan([]), "x", 10) == {
        "jobs": 0,
        "unstated": 0,
        "distinct": 0,
        "capped": False,
        "locations": [],
    }
