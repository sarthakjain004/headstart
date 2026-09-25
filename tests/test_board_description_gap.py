"""Round-trip tests for the ADR-0062 description-gap ledger.

Thin by design — the ledger is a CSV of counts. What matters is that an absent file degrades to
"reserve nothing" rather than raising, because that is the state every consumer sees before the
first run writes one, and the state the slice must fall back to once the gap drains.
"""

from __future__ import annotations

import pytest

from headstart.board_description_gap import load, save

TODAY = "2026-08-18"


def test_round_trip(tmp_path):
    path = tmp_path / "board_description_gap.csv"
    rows = {"greenhouse:acme": 12, "workday:x/careers": 400}
    save(path, rows, today=TODAY)
    assert load(path) == rows


def test_keys_are_normalised_on_both_sides(tmp_path):
    """The slice looks boards up through `key_for`, which lowercases. A key written or hand-edited
    in another casing would be one no lookup can ever reach — measured, that would have stranded
    1,693 of 13,708 gap boards."""
    path = tmp_path / "gap.csv"
    save(path, {"workday:x/Careers": 400}, today=TODAY)
    assert load(path) == {"workday:x/careers": 400}

    path.write_text(
        "board,unsettled,updated_at\nworkday:X/CAREERS,7,2026-08-18\n", encoding="utf-8"
    )
    assert load(path) == {"workday:x/careers": 7}


def test_case_variants_are_summed_into_one_row(tmp_path):
    """ADR-0023: the two casings are one Board, so their counts belong together — not as two
    half-counts the slice can match at most one of."""
    path = tmp_path / "gap.csv"
    save(path, {"workday:a/External": 30, "workday:a/external": 12}, today=TODAY)
    assert load(path) == {"workday:a/external": 42}
    assert len(path.read_text().strip().splitlines()) == 2  # header + one row


def test_absent_file_is_empty_not_an_error(tmp_path):
    assert load(tmp_path / "nope.csv") == {}


def test_saved_most_unsettled_first_for_stable_diffs(tmp_path):
    path = tmp_path / "gap.csv"
    save(path, {"a:1": 5, "b:2": 90, "c:3": 5}, today=TODAY)
    boards = [line.split(",")[0] for line in path.read_text().splitlines()[1:]]
    assert boards == ["b:2", "a:1", "c:3"]  # count desc, then board asc


def test_empty_ledger_writes_only_a_header(tmp_path):
    path = tmp_path / "gap.csv"
    save(path, {}, today=TODAY)
    assert load(path) == {}
    assert path.read_text().strip() == "board,unsettled,updated_at"


def test_key_for_lowercases_a_board_and_a_key_alike():
    """ADR-0192: the ledger owns its folding, so a Board and the key a Job id yields pair."""
    from headstart.board_description_gap import key_for
    from headstart.scrapable_boards import ScrapableBoard

    board = ScrapableBoard("workday", "https://Acme.wd1.myworkdayjobs.com/External")
    assert key_for(board) == key_for("workday:Acme/External") == "workday:acme/external"


def test_a_malformed_row_names_its_ledger_and_line(tmp_path):
    path = tmp_path / "gap.csv"
    path.write_text(
        "board,unsettled,updated_at\nx:a,1,2026-09-01\nx:b,many,2026-09-01\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"gap\.csv:3: "):
        load(path)
