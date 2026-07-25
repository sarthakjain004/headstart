"""Tests for the measured scrape-cost ledger (ADR-0027).

The thing worth locking down is the estimate cascade — measured seconds, else the ATS median,
else the global median — because collapsing unmeasured Boards to one constant is exactly the
failure ADR-0026's model had. Plus the EWMA blend, the partial-harvest carry rule, and tolerance
of a shard's torn final row.
"""

from __future__ import annotations

from headstart.board_cost import (
    BoardCost,
    ats_medians,
    costs_for,
    load,
    read_shard_rows,
    save,
    update,
)


def _rows(**kw: float) -> dict[str, BoardCost]:
    return {
        b: BoardCost(seconds=s, jobs=10, updated_at="2026-07-25") for b, s in kw.items()
    }


def test_first_measurement_is_adopted_not_blended():
    rows = update({}, {"workday:acme": (120.0, 300)})
    assert rows["workday:acme"].seconds == 120.0  # no history to blend against


def test_second_measurement_ewma_blends_with_history():
    prev = _rows(**{"workday:acme": 100.0})
    rows = update(prev, {"workday:acme": (200.0, 300)}, current_weight=0.5)
    assert rows["workday:acme"].seconds == 150.0


def test_unscraped_boards_carry_unchanged():
    # partial-harvest rule: a run must not decay a Board it never timed
    prev = _rows(**{"lever:a": 30.0, "workday:b": 90.0})
    rows = update(prev, {"lever:a": (10.0, 5)}, current_weight=0.5)
    assert rows["workday:b"] == prev["workday:b"]
    assert rows["lever:a"].seconds == 20.0


def test_zero_seconds_never_poisons_the_ewma():
    prev = _rows(**{"workday:acme": 100.0})
    rows = update(prev, {"workday:acme": (0.0, 0)})
    assert rows["workday:acme"].seconds == 100.0


def test_ats_medians_group_by_ats_prefix():
    rows = _rows(**{"workday:a": 100.0, "workday:b": 300.0, "lever:c": 10.0})
    assert ats_medians(rows) == {"workday": 200.0, "lever": 10.0}


def test_costs_for_prefers_measurement_then_ats_median_then_global():
    rows = _rows(**{"workday:a": 100.0, "workday:b": 300.0, "lever:c": 10.0})
    got = costs_for(
        [
            ("workday", "workday:a"),  # measured
            ("workday", "workday:unseen"),  # ATS median
            ("personio", "personio:unseen"),  # no ATS history -> global median
        ],
        rows,
    )
    assert got == [100.0, 200.0, 105.0]  # median([200.0, 10.0]) == 105.0


def test_costs_for_falls_back_when_ledger_is_empty():
    assert costs_for([("workday", "workday:a")], {}, fallback=7.0) == [7.0]


def test_read_shard_rows_skips_a_torn_final_line(tmp_path):
    # a shard killed mid-write by its time budget leaves a partial row; the rest must survive
    p = tmp_path / "board_cost.csv"
    p.write_text("board,seconds,jobs\nlever:a,12.5,3\nworkday:b,", encoding="utf-8")
    assert read_shard_rows(p) == {"lever:a": (12.5, 3)}


def test_read_shard_rows_missing_file_is_empty():
    assert read_shard_rows("/nonexistent/board_cost.csv") == {}


def test_save_load_round_trip_sorted_cost_desc(tmp_path):
    p = tmp_path / "state" / "board_cost.csv"
    save(p, _rows(**{"lever:cheap": 5.0, "workday:pricey": 500.0}))
    assert p.read_text(encoding="utf-8").splitlines()[1].startswith("workday:pricey")
    assert load(p)["workday:pricey"].seconds == 500.0


def test_load_missing_file_is_empty():
    assert load("/nonexistent/board_cost.csv") == {}
