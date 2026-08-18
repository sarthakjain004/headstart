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

    def fake_scrape_all(companies, jobs_dir, progress_every=200, on_board=None, **_):
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
        progress,
        tmp_path,
        elapsed=3600.0,
        predicted=30.0,
        serial=90.0,
        killed=True,
        shard="7",
    )

    messages = [r.getMessage() for r in caplog.records]
    assert any("60 deferred to the next run" in m for m in messages)
    assert any("done: 80 jobs from 40 boards" in m for m in messages)
    report = json.loads((tmp_path / "_shard_report.json").read_text())
    assert report["killed_by_budget"] is True
    assert (report["undone"], report["shard"], report["assigned"]) == (60, "7", 100)
    assert report["board_seconds"]["max"] == 1.5


def test_report_carries_short_lists_into_the_shard_report(tmp_path):
    """The last hop before the join: a Board reported short by its scraper has to reach
    ``_shard_report.json``, beside the errors, or the signal dies on this runner (ADR-0053).

    Kept out of ``errors`` on purpose — the Board produced Jobs and did not fail, and counting
    it as a failure would make every error total in the run read wrong.
    """
    short = "HTTP 429 on page 31 — got 300 of 850 postings"
    progress = scrape_run._Progress(assigned=2)
    progress.on_board("eightfold:jobs.nvidia.com", 300, None, 61.0, short)
    progress.on_board("lever:acme", 5, None, 1.0)

    scrape_run._report(
        progress, tmp_path, elapsed=62.0, predicted=None, serial=None, killed=False
    )

    report = json.loads((tmp_path / "_shard_report.json").read_text())
    assert report["truncated"] == {"eightfold:jobs.nvidia.com": short}
    assert report["errors"] == {}


def test_report_states_actual_against_the_planners_prediction(tmp_path, caplog):
    """The comparison nothing made: a cost model can drift by 3x in plain sight without it."""
    caplog.set_level(logging.INFO, logger="headstart.ingest.scrape_run")
    progress = scrape_run._Progress(assigned=1)
    progress.on_board("lever:a", 1, None, 2.0)

    scrape_run._report(
        progress, tmp_path, elapsed=1200.0, predicted=40.0, serial=120.0, killed=False
    )

    assert any("actual/predicted 0.50x" in r.getMessage() for r in caplog.records)


def test_plan_minutes_reads_the_shards_own_entry(tmp_path):
    (tmp_path / "plan.json").write_text(
        json.dumps(
            {
                "per_shard_minutes": [10.0, 20.5, 30.0],
                "per_shard_serial_minutes": [40.0, 60.5, 90.0],
            }
        ),
        encoding="utf-8",
    )
    shard = str(tmp_path / "shard-1.jsonl")
    assert scrape_run._plan_minutes(shard, "per_shard_minutes") == 20.5
    # the serial sum is read from its own field: the join measures the fan-out's speedup against
    # it, and against the prediction the estimate would chase its own tail (ADR-0054)
    assert scrape_run._plan_minutes(shard, "per_shard_serial_minutes") == 60.5
    # an older plan without the field is absence, not an error — the shard just can't compare
    (tmp_path / "plan.json").write_text(json.dumps({"count": 3}), encoding="utf-8")
    assert scrape_run._plan_minutes(shard, "per_shard_minutes") is None
    assert scrape_run._plan_minutes(None, "per_shard_minutes") is None


def test_read_have_details_returns_none_when_the_planner_shipped_nothing(tmp_path):
    """A first run, or any run where the embed store has not merged yet: fetch every detail."""
    from headstart.ingest import scrape_run as sr

    assert sr._read_have_details(tmp_path / "held_details.txt.gz") is None


def test_read_have_details_loads_the_shipped_list(tmp_path):
    import gzip

    from headstart.ingest import scrape_run as sr

    path = tmp_path / "held_details.txt.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("eightfold:acme:1\n\neightfold:acme:2\n")
    assert sr._read_have_details(path) == {"eightfold:acme:1", "eightfold:acme:2"}


def _run_main(tmp_path, monkeypatch, scrape_all):
    """Drive `main` over a one-Board assignment with `scrape_all` stubbed."""
    monkeypatch.setattr(scrape_run, "scrape_all", scrape_all)
    assignment = tmp_path / "shard-0.jsonl"
    assignment.write_text(
        json.dumps({"ats": "lever", "slug": "acme", "name": "Acme"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_run",
            "--assignment",
            str(assignment),
            "--outdir",
            str(tmp_path / "frag"),
        ],
    )
    return scrape_run.main()


def test_a_budget_kill_returns_the_sentinel_and_still_writes_its_report(
    tmp_path, monkeypatch
):
    """The budget's SIGTERM arrives as `SystemExit`. The shard has still succeeded — banking a
    partial is the designed outcome — so `main` says so with `_BUDGET_KILLED` rather than an
    error, and the report is on disk before the entrypoint leaves.
    """

    def killed(*_a, **_k):
        raise SystemExit("signal 15")

    status = _run_main(tmp_path, monkeypatch, killed)

    assert status == scrape_run._BUDGET_KILLED
    report = json.loads((tmp_path / "frag" / "_shard_report.json").read_text())
    assert report["killed_by_budget"] is True


def test_a_clean_finish_returns_zero(tmp_path, monkeypatch):
    """The sentinel must not leak into the ordinary path."""

    class _R:
        errors: dict = {}
        truncated: dict = {}
        unique = 0
        boards = 1

    assert _run_main(tmp_path, monkeypatch, lambda *a, **k: _R()) == 0


def test_the_entrypoint_turns_a_budget_kill_into_a_green_exit(monkeypatch):
    """`_BUDGET_KILLED` is an in-band signal to the entrypoint, never an exit status: the step
    must stay green, and the process must leave without the thread-pool's atexit join."""
    codes = []
    monkeypatch.setattr(scrape_run.os, "_exit", codes.append)

    scrape_run._exit_without_joining_stragglers()

    assert codes == [0]


def test_progress_records_every_board_that_did_not_raise():
    """`boards_ok` is what tells the failures ledger a Board is alive when it produced no jobs.
    A zero-job success leaves no corpus lines and no error entry, so without this it looks
    identical to a Board that was never scraped — and its gone-streak would never clear."""
    progress = scrape_run._Progress(assigned=3)
    progress.on_board("greenhouse:busy", jobs=5, error=None, seconds=1.0)
    progress.on_board(
        "greenhouse:quiet", jobs=0, error=None, seconds=1.0
    )  # alive, empty
    progress.on_board(
        "greenhouse:dead", jobs=0, error="HTTPError: HTTP Error 404: ", seconds=1.0
    )

    assert progress.boards_ok == ["greenhouse:busy", "greenhouse:quiet"]
    assert set(progress.errors) == {"greenhouse:dead"}
