"""Tests for headstart.ingest.scrape_run.

The scrape-shard (``--assignment``) mode (ADR-0026) must read the planner's board list verbatim
and scrape exactly those boards into its own fragment dir — no slice selection. ``scrape_all`` is
faked, so no network / real scraping. Also pins the run's logging helpers: ``_Progress.on_board``
(live per-board lines) and ``_error_summary`` (the type x ATS grouping behind the warning line).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import headstart.ingest.scrape_run as scrape_run


class _Result:
    def __init__(self, n: int) -> None:
        self.unique = n
        self.boards = n
        self.errors: dict[str, str] = {}


def test_assignment_scrapes_exactly_the_listed_boards(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_scrape_all(companies, jobs_dir, progress_every=200, on_board=None):
        captured["companies"] = list(companies)
        captured["jobs_dir"] = jobs_dir
        captured["on_board"] = on_board
        return _Result(len(companies))

    monkeypatch.setattr(scrape_run, "scrape_all", fake_scrape_all)

    assignment = tmp_path / "shard-0.jsonl"
    rows = [("lever", "acme", "Acme"), ("workday", "big", "Big"), ("keka", "x", None)]
    assignment.write_text(
        "".join(
            json.dumps({"ats": a, "slug": s, "name": n}) + "\n" for a, s, n in rows
        ),
        encoding="utf-8",
    )
    outdir = tmp_path / "frag"
    monkeypatch.setattr(
        sys,
        "argv",
        ["scrape_run", "--assignment", str(assignment), "--outdir", str(outdir)],
    )

    assert scrape_run.main() == 0
    got = [(c.ats, c.slug, c.name) for c in captured["companies"]]
    assert got == rows  # exact board list, in order
    assert (
        Path(captured["jobs_dir"]) == outdir
    )  # scraped into the shard's own fragment dir
    # live per-board logging is wired, and it is the accumulator the shutdown path reads
    assert isinstance(captured["on_board"].__self__, scrape_run._Progress)


def test_log_board_failure_at_info_success_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="headstart.ingest.scrape_run")
    progress = scrape_run._Progress(2)
    progress.on_board("lever:acme", 0, "RuntimeError: boom", 4.2)
    progress.on_board("lever:beta", 12, None, 0.8)

    failed, ok = caplog.records
    assert (
        failed.levelno == logging.INFO
    )  # failures stream live, visible at default level
    assert failed.getMessage() == "lever:acme failed after 4s: RuntimeError: boom"
    assert ok.levelno == logging.DEBUG  # successes are per-board detail
    assert ok.getMessage() == "lever:beta: 12 jobs in 0.8s"


def test_log_board_slow_board_surfaces_at_info(caplog):
    # the cost ledger never records a board the shard budget kills, so the INFO line is the
    # only place a monster board gets named
    caplog.set_level(logging.INFO, logger="headstart.ingest.scrape_run")
    scrape_run._Progress(1).on_board("workday:giant/External", 9000, None, 1830.4)

    (slow,) = caplog.records
    assert slow.levelno == logging.INFO
    assert slow.getMessage() == "slow board workday:giant/External: 9000 jobs in 1830s"


def test_error_summary_groups_by_type_and_ats():
    errors = {
        "lever:a": "Timeout: slow",
        "lever:b": "Timeout: slower",
        "workday:c": "Timeout: slowest",
        "greenhouse:d": "HTTPError: 500",
    }
    assert scrape_run._error_summary(errors) == (
        "3 Timeout (lever 2, workday 1); 1 HTTPError (greenhouse 1)"
    )


def test_error_summary_caps_atses_at_three_with_more_tail():
    atses = ["a", "a", "a", "b", "b", "c", "d", "e"]
    errors = {f"{ats}:{i}": "Timeout: x" for i, ats in enumerate(atses)}
    assert scrape_run._error_summary(errors) == "8 Timeout (a 3, b 2, c 1, +2 more)"


def test_error_summary_no_tail_at_exactly_three_atses():
    errors = {"a:1": "E: x", "b:1": "E: y", "c:1": "E: z"}
    assert scrape_run._error_summary(errors) == "3 E (a 1, b 1, c 1)"


def test_error_summary_empty_and_colonless_message():
    assert scrape_run._error_summary({}) == ""
    # a message with no ":" groups under the whole message
    assert scrape_run._error_summary({"x:a": "boom"}) == "1 boom (x 1)"


def test_progress_tracks_what_is_left_undone():
    """The number a budget-killed shard exists to report: work deferred to the next run."""
    progress = scrape_run._Progress(assigned=5)
    progress.on_board("lever:a", 3, None, 1.0)
    progress.on_board("lever:b", 0, "Timeout: slow", 30.0)

    assert (progress.done, progress.undone, progress.jobs) == (2, 3, 3)
    assert progress.errors == {"lever:b": "Timeout: slow"}


def test_report_survives_the_time_budget_and_still_writes_its_numbers(tmp_path, caplog):
    """A shard killed mid-harvest used to report nothing at all — no counts, no errors, no
    sign of what it skipped. It must now say all three, on the way down."""
    caplog.set_level(logging.INFO, logger="headstart.ingest.scrape_run")
    progress = scrape_run._Progress(assigned=100)
    for i in range(40):
        progress.on_board(f"lever:{i}", 2, None, 1.5)

    scrape_run._report(
        progress, tmp_path, elapsed=3600.0, predicted=30.0, killed=True, shard="7"
    )

    messages = [r.getMessage() for r in caplog.records]
    assert any("60 deferred to the next run" in m for m in messages)
    assert any("done: 80 jobs from 40 boards" in m for m in messages)
    report = json.loads((tmp_path / "_shard_report.json").read_text())
    assert report["killed_by_budget"] is True
    assert (report["undone"], report["shard"], report["assigned"]) == (60, "7", 100)
    assert report["board_seconds"]["max"] == 1.5


def test_report_states_actual_against_the_planners_prediction(tmp_path, caplog):
    """The comparison nothing made: a cost model can drift by 3x in plain sight without it."""
    caplog.set_level(logging.INFO, logger="headstart.ingest.scrape_run")
    progress = scrape_run._Progress(assigned=1)
    progress.on_board("lever:a", 1, None, 2.0)

    scrape_run._report(progress, tmp_path, elapsed=1200.0, predicted=40.0, killed=False)

    assert any("actual/predicted 0.50x" in r.getMessage() for r in caplog.records)


def test_predicted_minutes_reads_the_shards_own_entry(tmp_path):
    (tmp_path / "plan.json").write_text(
        json.dumps({"per_shard_minutes": [10.0, 20.5, 30.0]}), encoding="utf-8"
    )
    assert scrape_run._predicted_minutes(str(tmp_path / "shard-1.jsonl")) == 20.5
    # an older plan without the field is absence, not an error — the shard just can't compare
    (tmp_path / "plan.json").write_text(json.dumps({"count": 3}), encoding="utf-8")
    assert scrape_run._predicted_minutes(str(tmp_path / "shard-1.jsonl")) is None
    assert scrape_run._predicted_minutes(None) is None
