"""Round-trip tests for the ADR-0062 description-gap ledger.

Thin by design — the ledger is a CSV of counts. What matters is that an absent file degrades to
"reserve nothing" rather than raising, because that is the state every consumer sees before the
first run writes one, and the state the slice must fall back to once the gap drains.
"""

from __future__ import annotations

from headstart.ingest.board_description_gap import load, save

TODAY = "2026-08-18"


def test_round_trip(tmp_path):
    path = tmp_path / "board_description_gap.csv"
    rows = {"greenhouse:acme": 12, "workday:x/Careers": 400}
    save(path, rows, today=TODAY)
    assert load(path) == rows


def test_absent_file_is_empty_not_an_error(tmp_path):
    assert load(tmp_path / "nope.csv") == {}


def test_saved_unsettled_desc_for_stable_diffs(tmp_path):
    path = tmp_path / "gap.csv"
    save(path, {"a:1": 5, "b:2": 90, "c:3": 5}, today=TODAY)
    boards = [line.split(",")[0] for line in path.read_text().splitlines()[1:]]
    assert boards == ["b:2", "a:1", "c:3"]  # count desc, then board asc


def test_empty_ledger_writes_only_a_header(tmp_path):
    path = tmp_path / "gap.csv"
    save(path, {}, today=TODAY)
    assert load(path) == {}
    assert path.read_text().strip() == "board,unsettled,updated_at"
