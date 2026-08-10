"""Tests for headstart.ingest.scrape_run.

The scrape-shard (``--assignment``) mode (ADR-0026) must read the planner's board list verbatim
and scrape exactly those boards into its own fragment dir — no slice selection. ``scrape_all`` is
faked, so no network / real scraping. Also pins the run's logging helpers: ``_log_board`` (live
per-board lines) and ``_error_summary`` (the type x ATS grouping behind the warning line).
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
    assert (
        captured["on_board"] is scrape_run._log_board
    )  # live per-board logging is wired


def test_log_board_failure_at_info_success_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="headstart.ingest.scrape_run")
    scrape_run._log_board("lever:acme", 0, "RuntimeError: boom")
    scrape_run._log_board("lever:beta", 12, None)

    failed, ok = caplog.records
    assert (
        failed.levelno == logging.INFO
    )  # failures stream live, visible at default level
    assert failed.getMessage() == "lever:acme failed: RuntimeError: boom"
    assert ok.levelno == logging.DEBUG  # successes are per-board detail
    assert ok.getMessage() == "lever:beta: 12 jobs"


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
