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
    ShardCost,
    read_shard_rows,
    save,
    update,
)


def _rows(**kw: float) -> dict[str, BoardCost]:
    return {
        b: BoardCost(seconds=s, jobs=10, updated_at="2026-07-25") for b, s in kw.items()
    }


def test_first_measurement_is_adopted_not_blended():
    rows = update({}, {"workday:acme": ShardCost(120.0, 300)})
    assert rows["workday:acme"].seconds == 120.0  # no history to blend against


def test_second_measurement_ewma_blends_with_history():
    prev = _rows(**{"workday:acme": 100.0})
    rows = update(prev, {"workday:acme": ShardCost(200.0, 300)}, current_weight=0.5)
    assert rows["workday:acme"].seconds == 150.0


def test_unscraped_boards_carry_unchanged():
    # partial-harvest rule: a run must not decay a Board it never timed
    prev = _rows(**{"lever:a": 30.0, "workday:b": 90.0})
    rows = update(prev, {"lever:a": ShardCost(10.0, 5)}, current_weight=0.5)
    assert rows["workday:b"] == prev["workday:b"]
    assert rows["lever:a"].seconds == 20.0


def test_zero_seconds_never_poisons_the_ewma():
    prev = _rows(**{"workday:acme": 100.0})
    rows = update(prev, {"workday:acme": ShardCost(0.0, 0)})
    assert rows["workday:acme"].seconds == 100.0


def test_ats_medians_group_by_ats_prefix():
    rows = _rows(**{"workday:a": 100.0, "workday:b": 300.0, "lever:c": 10.0})
    assert ats_medians(rows) == {"workday": 200.0, "lever": 10.0}


def test_costs_for_prefers_measurement_then_ats_median_then_global():
    rows = _rows(**{"workday:a": 100.0, "workday:b": 300.0, "lever:c": 10.0})
    got = costs_for(
        [
            "workday:a",  # measured
            "workday:unseen",  # ATS median, derived from the key
            "personio:unseen",  # no ATS history -> global median
        ],
        rows,
    )
    assert got == [100.0, 200.0, 105.0]  # median([200.0, 10.0]) == 105.0


def test_costs_for_falls_back_when_ledger_is_empty():
    assert costs_for(["workday:a"], {}, fallback=7.0) == [7.0]


def test_read_shard_rows_skips_a_torn_final_line(tmp_path):
    # a shard killed mid-write by its time budget leaves a partial row; the rest must survive
    p = tmp_path / "board_cost.csv"
    p.write_text(
        "board,seconds,jobs,unfinished\nlever:a,12.5,3,0\nworkday:b,", encoding="utf-8"
    )
    assert read_shard_rows(p) == {"lever:a": ShardCost(12.5, 3, False)}


def test_read_shard_rows_reads_a_fragment_written_before_the_unfinished_column(
    tmp_path,
):
    """An in-flight upgrade must not drop a whole shard's measurements. The old three-column
    fragment is all-measured — the column is absent, not false."""
    p = tmp_path / "board_cost.csv"
    p.write_text("board,seconds,jobs\nlever:a,12.5,3\n", encoding="utf-8")
    assert read_shard_rows(p) == {"lever:a": ShardCost(12.5, 3, False)}


def test_an_unfinished_row_raises_the_price_and_never_lowers_it(tmp_path):
    """The floor is a bound, not a measurement (ADR-0064). Blending it records less than the
    kill proved — which for dollartree was the difference between 1,766 s and the 3,120 s it
    demonstrably burned, and 1,766 s is low enough to be packed into another shard next run."""
    prev = {"workday:dollartree": BoardCost(411.9, 24017, "2026-08-17")}
    rows = update(prev, {"workday:dollartree": ShardCost(3120.0, 0, unfinished=True)})
    assert rows["workday:dollartree"].seconds == 3120.0
    assert rows["workday:dollartree"].jobs == 24017, (
        "an unfinished run banked no listing; a 0 would erase the last full scrape's count"
    )


def test_an_unfinished_row_below_the_stored_cost_leaves_it_alone(tmp_path):
    """A shard killed early proves only a small bound. Taking it as the measurement would let
    an unlucky kill make an expensive Board look cheap — the opposite of the point."""
    prev = {"workday:big": BoardCost(2000.0, 900, "2026-08-17")}
    rows = update(prev, {"workday:big": ShardCost(30.0, 0, unfinished=True)})
    assert rows["workday:big"].seconds == 2000.0


def test_read_shard_rows_missing_file_is_empty():
    assert read_shard_rows("/nonexistent/board_cost.csv") == {}


def test_save_load_round_trip_sorted_cost_desc(tmp_path):
    p = tmp_path / "state" / "board_cost.csv"
    save(p, _rows(**{"lever:cheap": 5.0, "workday:pricey": 500.0}))
    assert p.read_text(encoding="utf-8").splitlines()[1].startswith("workday:pricey")
    assert load(p)["workday:pricey"].seconds == 500.0


def test_load_missing_file_is_empty():
    assert load("/nonexistent/board_cost.csv") == {}


def test_a_floor_row_torn_mid_write_is_dropped_not_read_as_measured(tmp_path):
    """The floor rows are written last, at teardown, so a torn tail is most likely one of them —
    and a floor read as a measurement is EWMA-blended, which is exactly what the floor exists to
    prevent. The header tells the two apart: this file has the column, so a row without it is
    torn, not old."""
    p = tmp_path / "board_cost.csv"
    p.write_text(
        "board,seconds,jobs,unfinished\nlever:a,12.5,3,0\nworkday:big,3120.0,0",
        encoding="utf-8",
    )
    assert read_shard_rows(p) == {"lever:a": ShardCost(12.5, 3, False)}
