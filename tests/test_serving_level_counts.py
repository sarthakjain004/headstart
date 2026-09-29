"""How senior a set of Boards' served jobs are — `headstart.serving.level_counts` (ADR-0323).

Contracts: one filtered scan reading the three columns the band needs, bounded; each row counted
once, in the band the Trends Level view gives it; every band listed in that view's order, with its
label, a band with no row at 0; and a scan that reaches its bound says so.
"""

from __future__ import annotations

from headstart.serving import level_counts, location_counts
from headstart.trends.role_taxonomy import BAND_LABELS


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
        return list(self.rows)


def _row(min_years, title="Engineer", employment_type="Full-time"):
    return {"min_years": min_years, "title": title, "employment_type": employment_type}


def test_it_scans_only_the_columns_a_band_reads_of_the_rows_the_clause_selects():
    table = _Scan([_row(3)])
    level_counts.bands(table, "(lower(id) LIKE 'lever:acme:%')")
    assert table.asked == {
        "query": (),
        "where": ("(lower(id) LIKE 'lever:acme:%')", True),
        "select": ["min_years", "title", "employment_type"],
        "limit": location_counts.MAX_ROWS,
    }


def test_each_row_counts_once_in_its_trends_band_and_every_band_is_listed():
    rows = [_row(0), _row(1), _row(2), _row(4), _row(5), _row(7), _row(8), _row(15)]
    rows += [_row(None), _row(None), _row(3, title="Software Engineering Intern")]
    rows += [_row(None, employment_type="Internship")]
    answer = level_counts.bands(_Scan(rows), "x")
    assert answer["jobs"] == 12 and answer["capped"] is False
    assert [(b["band"], b["label"], b["count"]) for b in answer["bands"]] == [
        ("intern", BAND_LABELS["intern"], 2),
        ("entry", BAND_LABELS["entry"], 2),
        ("mid", BAND_LABELS["mid"], 2),
        ("senior", BAND_LABELS["senior"], 2),
        ("staff", BAND_LABELS["staff"], 2),
        ("unspecified", BAND_LABELS["unspecified"], 2),
    ]


def test_a_scan_that_reaches_its_bound_says_so(monkeypatch):
    monkeypatch.setattr(level_counts, "MAX_ROWS", 2)
    answer = level_counts.bands(_Scan([_row(1), _row(2), _row(3)]), "x")
    assert answer["jobs"] == 2 and answer["capped"] is True
