"""Tests for the measured scrape-cost ledger (ADR-0027).

The thing worth locking down is the estimate cascade — measured seconds, else the ATS median,
else the global median — because collapsing unmeasured Boards to one constant is exactly the
failure ADR-0026's model had. Plus the EWMA blend, the partial-harvest carry rule, and tolerance
of a shard's torn final row.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from headstart.board_cost import (
    BoardCost,
    ShardCost,
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
    torn, not old.

    This exercises the four-column schema, which is now the LEGACY one — the writer emits five
    since `errored` was added. The current schema's torn-tail case is
    :func:`test_a_row_torn_after_unfinished_is_dropped_not_read_as_a_clean_scrape`, and both are
    kept because a fragment of either shape can reach the join."""
    p = tmp_path / "board_cost.csv"
    p.write_text(
        "board,seconds,jobs,unfinished\nlever:a,12.5,3,0\nworkday:big,3120.0,0",
        encoding="utf-8",
    )
    assert read_shard_rows(p) == {"lever:a": ShardCost(12.5, 3, False)}


def test_an_errored_scrape_does_not_erase_the_last_known_job_count():
    """`harvest` records `n_fresh = 0` whatever the outcome, so an errored Board's 0 means "we
    never found out", not "there was nothing".

    It used to be written straight over the last good count. That was harmless while `jobs` was
    diagnostic; ADR-0145 made it the value gate's veto input, and `run_one`'s own comment had
    already named the consequence — "the value gate would drop it forever, on a Board that failed
    instantly". Errors run 19-40 a run, so this is not a corner.
    """
    prev = {
        "workday:big": BoardCost(seconds=1000.0, jobs=4321, updated_at="2026-09-01")
    }
    rows = update(
        prev,
        {"workday:big": ShardCost(seconds=1200.0, jobs=0, errored=True)},
        looked_at="2026-09-07",
    )
    assert rows["workday:big"].jobs == 4321  # the count survives
    assert (
        rows["workday:big"].seconds > 1000.0
    )  # the seconds are still real and still blend


def test_a_board_whose_only_measurement_failed_has_no_known_yield():
    """No prior row and a failed run means the yield is *unknown*, which is not the same as zero.

    Writing 0 here is what would let one bad first run gate a Board for a fortnight. None says the
    true thing, and ADR-0145's veto only fires on a measured 0.
    """
    rows = update(
        {},
        {"workday:new": ShardCost(seconds=1200.0, jobs=0, errored=True)},
        looked_at="2026-09-07",
    )
    assert rows["workday:new"].jobs is None


def test_a_first_ever_budget_kill_also_leaves_the_yield_unknown():
    """The unfinished twin of the case above — a giant killed mid-scrape on its first sighting.

    `jobs=before.jobs if before else now.jobs` used to write the kill's own 0 here, which reads as
    a measured empty Board when it is the opposite: a Board too big to finish.
    """
    rows = update(
        {},
        {"workday:giant": ShardCost(seconds=3300.0, jobs=0, unfinished=True)},
        looked_at="2026-09-07",
    )
    assert rows["workday:giant"].jobs is None
    assert rows["workday:giant"].seconds == 3300.0


def test_an_unknown_job_count_survives_the_csv_round_trip(tmp_path):
    """None has to reach the next run's planner, which reads this back off the dataset.

    An empty field, the same way the liveness ledger already writes an unknown count — not a
    sentinel, which would be one more value with two meanings.
    """
    path = tmp_path / "board_cost.csv"
    save(
        path,
        {
            "a:known": BoardCost(seconds=10.0, jobs=7, updated_at="2026-09-07"),
            "b:unknown": BoardCost(seconds=20.0, jobs=None, updated_at="2026-09-07"),
        },
    )
    back = load(path)
    assert back["a:known"].jobs == 7
    assert back["b:unknown"].jobs is None


def test_a_fragment_without_the_errored_column_reads_as_not_errored(tmp_path):
    """A shard fragment written before this column existed is a complete measurement, and the
    column's absence must not be read as a torn row — the same contract `unfinished` already has.
    """
    path = tmp_path / "board_cost.csv"
    path.write_text("board,seconds,jobs,unfinished\na:b,12.5,3,0\n", encoding="utf-8")
    rows = read_shard_rows(path)
    assert rows["a:b"] == ShardCost(
        seconds=12.5, jobs=3, unfinished=False, errored=False
    )


def test_a_row_torn_after_unfinished_is_dropped_not_read_as_a_clean_scrape(tmp_path):
    """The hole the `errored` column opened, and why it needed the same guard as `unfinished`.

    `errored` was first read as ``row.get("errored") or 0``, so its ABSENCE always meant False. On
    a current five-column fragment whose tail is torn after ``unfinished``, that reads as a
    complete, non-errored scrape returning 0 — which is exactly the finding ADR-0145's veto acts
    on. A Board that merely died mid-write would have earned a 14-day exclusion, which is the same
    failure the veto's own first draft was rejected for.
    """
    path = tmp_path / "board_cost.csv"
    path.write_text(
        "board,seconds,jobs,unfinished,errored\ngood:b,10.0,5,0,0\ntorn:b,1631.0,0,0\n",
        encoding="utf-8",
    )
    rows = read_shard_rows(path)
    assert "torn:b" not in rows
    assert rows["good:b"] == ShardCost(
        seconds=10.0, jobs=5, unfinished=False, errored=False
    )


def test_key_for_keeps_the_casing_its_scraper_builds():
    """ADR-0192: the cost ledger is keyed verbatim, the same key the priority ledger reads."""
    from headstart import board_cost, board_priority
    from headstart.scrapable_boards import ScrapableBoard

    board = ScrapableBoard("workday", "https://Acme.wd1.myworkdayjobs.com/External")
    assert board_cost.key_for(board) == "workday:Acme/External"
    assert board_cost.key_for("workday:Acme/External") == "workday:Acme/External"
    assert board_cost.key_for(board) == board_priority.key_for(board)


def test_update_stamps_the_run_to_the_second_not_the_day():
    """The Slice's rotation tail is ordered by this stamp (ADR-0229), and ~26 runs share a day.

    A bare date cannot tell this morning's look from tonight's, so the tail would fall back to a
    random draw among every Board looked at today — the long gaps the rotation exists to remove.
    """
    stamp = update({}, {"workday:acme": ShardCost(120.0, 300)})[
        "workday:acme"
    ].updated_at
    parsed = datetime.fromisoformat(stamp)
    assert "T" in stamp, f"{stamp!r} is a day, not a moment"
    assert parsed.utcoffset() == timedelta(0), (
        "stamped in UTC, so stamps sort as strings"
    )
