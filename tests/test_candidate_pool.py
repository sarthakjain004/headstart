"""Tests for the candidate pool the discovery miners fill (scripts/discover/candidate_pool.py).
A script under `scripts/discover`, imported by name."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import candidate_pool as pool


def _isolate(monkeypatch, tmp_path, ledger_rows):
    """Point the pool at a tmp ledger for `bamboohr` and a tmp pool directory."""
    ledgers = tmp_path / "liveness"
    ledgers.mkdir()
    with (ledgers / "bamboohr.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("ats", "tenant", "url", "status", "jobs", "checked_at"))
        writer.writerows(ledger_rows)
    monkeypatch.setattr(pool, "LEDGERS", ledgers)
    monkeypatch.setattr(pool, "POOL", tmp_path / "pool")
    monkeypatch.setattr(pool, "BASELINE", tmp_path / "pool" / ".baseline_held")
    monkeypatch.setattr(pool, "SOURCES", tmp_path / "pool" / ".sources")
    monkeypatch.setattr(pool, "_HELD", {})


def test_a_dead_row_still_holds_its_board_and_case_does_not_matter(
    monkeypatch, tmp_path
):
    _isolate(
        monkeypatch,
        tmp_path,
        [("bamboohr", "Acme", "https://acme.bamboohr.com", "dead", "", "2026-09-01")],
    )
    assert pool.held_keys("bamboohr") == {"bamboohr:acme": "dead"}


def test_staging_writes_only_unheld_boards_and_counts_the_rest(monkeypatch, tmp_path):
    _isolate(
        monkeypatch,
        tmp_path,
        [("bamboohr", "held", "https://held.bamboohr.com", "live", "3", "2026-09-01")],
    )
    counts = pool.stage_unheld(
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
    staged = list(csv.DictReader((tmp_path / "pool" / "bamboohr.csv").open()))
    assert [row["tenant"] for row in staged] == ["fresh"]
    credited = (tmp_path / "pool" / ".sources" / "a_source-bamboohr.tsv").read_text()
    assert credited == "bamboohr\tfresh\thttps://fresh.bamboohr.com\n"


def test_a_candidate_no_scraper_can_read_is_counted_and_never_staged(
    monkeypatch, tmp_path
):
    """An ATS with no scraper (or a slug its `slug_from` rejects) names no Board."""
    _isolate(monkeypatch, tmp_path, [])
    monkeypatch.setattr(
        pool,
        "key_of",
        lambda ats, tenant, url: None if tenant == "bad" else f"{ats}:{tenant}",
    )
    counts = pool.stage_unheld(
        "bamboohr", [("bad", "https://bad"), ("ok", "https://ok.bamboohr.com")], "s"
    )
    assert counts["unreadable"] == 1 and counts["staged"] == 1
    staged = list(csv.DictReader((tmp_path / "pool" / "bamboohr.csv").open()))
    assert [row["tenant"] for row in staged] == ["ok"]


def test_a_rerun_stages_nothing_twice_and_credits_a_board_the_ledger_later_gained(
    monkeypatch, tmp_path
):
    """The baseline is what the ledger held before any landing, so a Board landed since (by
    another source) is still credited to a source that found it, though it is now held."""
    _isolate(monkeypatch, tmp_path, [])
    pool.stage_unheld("bamboohr", [("fresh", "https://fresh.bamboohr.com")], "first")
    assert pool.baseline_keys("bamboohr") == set()
    # the landing writes the ledger; a second source now finds the same Board
    with (pool.LEDGERS / "bamboohr.csv").open("a", newline="") as handle:
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
    monkeypatch.setattr(pool, "_HELD", {})
    counts = pool.stage_unheld(
        "bamboohr", [("fresh", "https://fresh.bamboohr.com")], "second"
    )
    assert counts["held"] == 1 and counts["unheld_at_baseline"] == 1
    assert counts["staged"] == 0
    assert len(list(csv.DictReader((tmp_path / "pool" / "bamboohr.csv").open()))) == 1


def test_a_rerun_that_finds_fewer_hosts_keeps_the_credits_an_earlier_run_wrote(
    monkeypatch, tmp_path
):
    """The credit file used to be reopened with "w", so a rerun that found fewer Boards dropped
    the credit of every one it did not find again."""
    _isolate(monkeypatch, tmp_path, [])
    pool.stage_unheld(
        "bamboohr",
        [("one", "https://one.bamboohr.com"), ("two", "https://two.bamboohr.com")],
        "s",
    )
    counts = pool.stage_unheld("bamboohr", [("one", "https://one.bamboohr.com")], "s")
    assert counts["unheld_at_baseline"] == 1  # what this run found
    credited = (
        (tmp_path / "pool" / ".sources" / "s-bamboohr.tsv").read_text().split("\n")
    )
    assert sorted(line.split("\t")[1] for line in credited if line) == ["one", "two"]


def test_the_hits_reach_disk_even_when_the_candidate_stream_fails(
    monkeypatch, tmp_path
):
    """Every hit row is flushed as found and both files are closed however the loop ends."""
    _isolate(monkeypatch, tmp_path, [])

    def candidates():
        yield "one", "https://one.bamboohr.com"
        raise ConnectionError("link dropped")

    try:
        pool.stage_unheld("bamboohr", candidates(), "flaky")
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


def test_rows_are_grouped_by_ats_and_staged_per_ats_with_the_claude_md_follow_ups(
    monkeypatch, capsys
):
    staged = []
    monkeypatch.setattr(
        pool,
        "stage_unheld",
        lambda ats, pairs, source: (
            staged.append((ats, pairs, source)) or {"staged": len(pairs)}
        ),
    )
    rows = [
        ("recruitee", "a", "https://a.recruitee.com"),
        ("bamboohr", "b", "https://b.bamboohr.com"),
        ("recruitee", "c", "https://c.recruitee.com"),
    ]
    assert pool.group_by_ats(rows) == {
        "recruitee": [
            ("a", "https://a.recruitee.com"),
            ("c", "https://c.recruitee.com"),
        ],
        "bamboohr": [("b", "https://b.bamboohr.com")],
    }
    pool.stage_by_ats(iter(rows), "src")
    assert [(ats, source) for ats, _, source in staged] == [
        ("bamboohr", "src"),
        ("recruitee", "src"),
    ]
    out = capsys.readouterr().out
    assert "bamboohr {'staged': 1}" in out and "recruitee {'staged': 2}" in out
    assert "dedupe_boards.py --ats recruitee --workers 4" in out
    assert "clearcompany" not in out  # no reminder for a family that was not staged
