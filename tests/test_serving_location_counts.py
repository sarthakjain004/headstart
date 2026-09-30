"""Where a set of served jobs are — `headstart.serving.location_counts` (ADR-0275, ADR-0323,
ADR-0355).

Contracts: one filtered scan that reads only the `location` column (and India's materialized
`country` column where the table has it), bounded for a company and whole for a search; places
counted as served, whitespace collapsed and nothing else merged; empty locations counted apart;
most first, ties by name; a scan that reaches its bound says so; and every place rolled up by the
countries the ``country`` filter reads in it, a place naming none counted apart.
"""

from __future__ import annotations

from headstart.serving import location_counts


class _Scan:
    """A lancedb table's plain scan: records what it was asked, answers ``rows`` (locations, or
    (location, country) pairs for a table with India's column)."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = {}

    def count_rows(self):
        return len(self.rows)

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
        pairs = [row if isinstance(row, tuple) else (row, None) for row in self.rows]
        return [{"location": place, "country": country} for place, country in pairs]


def test_it_scans_only_the_location_column_of_the_rows_the_clause_selects():
    table = _Scan(["Berlin"])
    location_counts.top(table, "(lower(id) LIKE 'lever:acme:%')", 10, False)
    assert table.asked == {
        "query": (),
        "where": ("(lower(id) LIKE 'lever:acme:%')", True),
        "select": ["location"],
        "limit": location_counts.MAX_ROWS,
    }


def test_places_are_counted_as_served_most_first_ties_by_name():
    rows = ["Seattle, WA", "Pune", "Seattle,  WA ", "seattle, wa", "Austin", "Pune"]
    rows += ["", None, "   "]
    answer = location_counts.top(_Scan(rows), "x", 3, False)
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
        "countries": [
            {
                "code": "US",
                "jobs": 4,
                # Within a country, a place is its first city, case-blind.
                "places": [
                    {"location": "Seattle", "count": 3},
                    {"location": "Austin", "count": 1},
                ],
            },
            {"code": "IN", "jobs": 2, "places": [{"location": "Pune", "count": 2}]},
        ],
        "no_country": {"jobs": 0, "places": []},
    }


def test_places_roll_up_by_country_then_city_a_multi_country_place_in_each():
    """rc02b: "Dublin" 15 and "Dublin, Ireland" 4 were two places; they are one Dublin."""
    rows = ["Dublin", "Dublin, Ireland", "Dublin", "N/A", "N/A", "Remote"]
    rows += ["London, UK; Berlin, Germany", "Cork, Ireland", "Galway, Ireland"]
    answer = location_counts.top(_Scan(rows), "x", 10, False)
    assert answer["countries"] == [
        {
            "code": "IE",
            "jobs": 5,
            "places": [
                {"location": "Dublin", "count": 3},
                {"location": "Cork", "count": 1},
                {"location": "Galway", "count": 1},
            ],
        },
        # Its first city is in another country, so under Germany it stays whole.
        {
            "code": "DE",
            "jobs": 1,
            "places": [{"location": "London, UK; Berlin, Germany", "count": 1}],
        },
        {"code": "GB", "jobs": 1, "places": [{"location": "London", "count": 1}]},
    ]
    assert answer["no_country"] == {
        "jobs": 3,
        "places": [{"location": "N/A", "count": 2}, {"location": "Remote", "count": 1}],
    }


def test_india_is_read_from_the_column_country_in_reads_where_the_table_has_it():
    """ADR-0355: `country=IN` reads the materialized column, so a count under India does too, even
    where the gazetteer would read the place otherwise."""
    table = _Scan(
        [("Pune", "IN"), ("Pune", None), ("Remote", "IN"), ("Austin, TX", None)]
    )
    answer = location_counts.top(table, "x", 10, True)
    assert table.asked["select"] == ["location", "country"]
    assert answer["countries"] == [
        {
            "code": "IN",
            "jobs": 2,
            "places": [
                {"location": "Pune", "count": 1},
                {"location": "Remote", "count": 1},
            ],
        },
        {"code": "US", "jobs": 1, "places": [{"location": "Austin", "count": 1}]},
    ]
    assert answer["no_country"] == {
        "jobs": 1,
        "places": [{"location": "Pune", "count": 1}],
    }


def test_a_scan_that_reaches_its_bound_says_so(monkeypatch):
    monkeypatch.setattr(location_counts, "MAX_ROWS", 3)
    answer = location_counts.top(_Scan(["A", "B", "C", "D"]), "x", 10, False)
    assert answer["jobs"] == 3 and answer["capped"] is True


def test_no_rows_is_an_empty_answer_not_an_error():
    assert location_counts.top(_Scan([]), "x", 10, False) == {
        "jobs": 0,
        "unstated": 0,
        "distinct": 0,
        "capped": False,
        "locations": [],
        "countries": [],
        "no_country": {"jobs": 0, "places": []},
    }


def test_a_citys_spellings_the_location_filter_reads_alike_are_one_city():
    """R5-P2-4 (ADR-0367): p5c listed "Bangalore" 302 and "Bengaluru" 297 as two places under
    India, "Zurich" and "Zürich" under Switzerland; each is one city, spelled as most write it,
    a tie by name."""
    rows = ["Bengaluru, India", "Bangalore", "Bengaluru", "BENGALURU", "Pune"]
    rows += ["Zürich, Switzerland", "Zurich", "Zürich", "Kraków, Poland", "Krakow"]
    answer = location_counts.top(_Scan(rows), "x", 10, False)
    places = {c["code"]: c["places"] for c in answer["countries"]}
    assert places["IN"] == [
        {"location": "Bengaluru", "count": 4},
        {"location": "Pune", "count": 1},
    ]
    assert places["CH"] == [{"location": "Zürich", "count": 3}]
    assert places["PL"] == [{"location": "Krakow", "count": 2}]


def test_a_code_or_a_site_is_counted_in_its_country_but_not_listed_as_a_place():
    """R5-P2-4 (ADR-0367): "SG" and "Fab 10A" were two of Singapore's top places."""
    rows = ["Singapore", "Singapore", "SG", "Fab 10A, Singapore", "Fab 10A, Singapore"]
    answer = location_counts.top(_Scan(rows), "x", 10, False)
    assert answer["countries"] == [
        {"code": "SG", "jobs": 5, "places": [{"location": "Singapore", "count": 2}]}
    ]


def test_a_search_reads_every_row_it_matches_with_no_bound(monkeypatch):
    """ADR-0355: a search's places read the whole match, past a company's bound."""
    monkeypatch.setattr(location_counts, "MAX_ROWS", 2)
    table = _Scan(["Berlin, Germany", "Munich", "", "Remote"])
    answer = location_counts.places(table, None, False)
    assert "where" not in table.asked and table.asked["limit"] == 4
    assert answer == {
        "jobs": 4,
        "unstated": 1,
        "countries": [
            {
                "code": "DE",
                "jobs": 2,
                "places": [
                    {"location": "Berlin", "count": 1},
                    {"location": "Munich", "count": 1},
                ],
            }
        ],
        "no_country": {"jobs": 1, "places": [{"location": "Remote", "count": 1}]},
    }
