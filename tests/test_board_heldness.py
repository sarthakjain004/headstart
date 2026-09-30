"""Tests for the ledger-heldness and staging helper the discovery miners share
(scripts/discover/board_heldness.py). A script under `scripts/discover`, imported by name."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import board_heldness as bh


def _isolate(monkeypatch, tmp_path, ledger_rows):
    """Point the helper at a tmp ledger for `bamboohr` and a tmp pool."""
    ledgers = tmp_path / "liveness"
    ledgers.mkdir()
    with (ledgers / "bamboohr.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("ats", "tenant", "url", "status", "jobs", "checked_at"))
        writer.writerows(ledger_rows)
    monkeypatch.setattr(bh, "LEDGERS", ledgers)
    monkeypatch.setattr(bh, "POOL", tmp_path / "pool")
    monkeypatch.setattr(bh, "BASELINE", tmp_path / "pool" / ".baseline_held")
    monkeypatch.setattr(bh, "SOURCES", tmp_path / "pool" / ".sources")
    monkeypatch.setattr(bh, "_HELD", {})


def test_a_dead_row_still_holds_its_board_and_case_does_not_matter(
    monkeypatch, tmp_path
):
    _isolate(
        monkeypatch,
        tmp_path,
        [("bamboohr", "Acme", "https://acme.bamboohr.com", "dead", "", "2026-09-01")],
    )
    assert bh.held_keys("bamboohr") == {"bamboohr:acme": "dead"}


def test_staging_writes_only_unheld_boards_and_counts_the_rest(monkeypatch, tmp_path):
    _isolate(
        monkeypatch,
        tmp_path,
        [("bamboohr", "held", "https://held.bamboohr.com", "live", "3", "2026-09-01")],
    )
    counts = bh.stage_unheld(
        "bamboohr",
        [
            ("held", "https://held.bamboohr.com"),
            ("fresh", "https://fresh.bamboohr.com"),
            ("Fresh", "https://fresh.bamboohr.com"),
        ],
        "a_source",
    )
    assert dict(counts) == {
        "candidates": 3,
        "held": 1,
        "unheld_at_baseline": 1,
        "staged": 1,
        "duplicate": 1,
    }
    pool = list(csv.DictReader((tmp_path / "pool" / "bamboohr.csv").open()))
    assert [row["tenant"] for row in pool] == ["fresh"]
    credited = (tmp_path / "pool" / ".sources" / "a_source-bamboohr.tsv").read_text()
    assert credited == "bamboohr\tfresh\thttps://fresh.bamboohr.com\n"


def test_a_rerun_stages_nothing_twice_and_credits_a_board_the_ledger_later_gained(
    monkeypatch, tmp_path
):
    """The baseline is what the ledger held before any landing, so a Board landed since (by
    another source) is still credited to a source that found it, though it is now held."""
    _isolate(monkeypatch, tmp_path, [])
    bh.stage_unheld("bamboohr", [("fresh", "https://fresh.bamboohr.com")], "first")
    assert bh.baseline_keys("bamboohr") == set()
    # the landing writes the ledger; a second source now finds the same Board
    with (bh.LEDGERS / "bamboohr.csv").open("a", newline="") as handle:
        csv.writer(handle).writerow(
            (
                "bamboohr",
                "fresh",
                "https://fresh.bamboohr.com",
                "live",
                "2",
                "2026-09-29",
            )
        )
    monkeypatch.setattr(bh, "_HELD", {})
    counts = bh.stage_unheld(
        "bamboohr", [("fresh", "https://fresh.bamboohr.com")], "second"
    )
    assert counts["held"] == 1 and counts["unheld_at_baseline"] == 1
    assert counts["staged"] == 0
    assert len(list(csv.DictReader((tmp_path / "pool" / "bamboohr.csv").open()))) == 1


def test_the_hits_reach_disk_even_when_the_candidate_stream_fails(
    monkeypatch, tmp_path
):
    """Every hit row is flushed as found and both files are closed however the loop ends."""
    _isolate(monkeypatch, tmp_path, [])

    def candidates():
        yield "one", "https://one.bamboohr.com"
        raise ConnectionError("link dropped")

    try:
        bh.stage_unheld("bamboohr", candidates(), "flaky")
    except ConnectionError:
        pass
    assert (
        (tmp_path / "pool" / "bamboohr.csv")
        .read_text()
        .splitlines()[-1]
        .endswith("one.bamboohr.com")
    )
    assert (
        (tmp_path / "pool" / ".sources" / "flaky-bamboohr.tsv")
        .read_text()
        .startswith("bamboohr\tone")
    )
