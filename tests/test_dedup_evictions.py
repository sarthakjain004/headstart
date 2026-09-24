"""The dedup eviction ledger (headstart.ingest.dedup_evictions, ADR-0210) and the one run
timestamp it shares with the trends ledger (headstart.ingest.run_ts)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest

from headstart.ingest import RUN_TS_ENV, dedup_evictions, run_ts


def _rows(path):
    return list(csv.reader(path.open(encoding="utf-8", newline="")))


def test_each_run_appends_one_row_per_board_and_rule(tmp_path):
    path = tmp_path / "state" / "dedup_evictions.csv"
    dedup_evictions.append(
        path,
        "2026-09-25T06:00:00+00:00",
        {
            "eightfold:jobs.nvidia.com:11": "backing-requisition",
            "eightfold:jobs.nvidia.com:12": "backing-requisition",
            "workday:acme/Campus:R-1": "workday-tenant",
            "workday:acme/Campus:R-2": "case-variant",
        },
        lambda job_id: job_id.rsplit(":", 1)[0],
    )
    dedup_evictions.append(
        path,
        "2026-09-25T12:00:00+00:00",
        {"workday:acme/Campus:R-3": "workday-tenant"},
        lambda job_id: job_id.rsplit(":", 1)[0],
    )
    assert _rows(path) == [
        ["ts", "board", "count", "rule"],
        [
            "2026-09-25T06:00:00+00:00",
            "eightfold:jobs.nvidia.com",
            "2",
            "backing-requisition",
        ],
        ["2026-09-25T06:00:00+00:00", "workday:acme/Campus", "1", "case-variant"],
        ["2026-09-25T06:00:00+00:00", "workday:acme/Campus", "1", "workday-tenant"],
        ["2026-09-25T12:00:00+00:00", "workday:acme/Campus", "1", "workday-tenant"],
    ]


def test_a_run_that_evicts_nothing_writes_nothing(tmp_path):
    path = tmp_path / "dedup_evictions.csv"
    dedup_evictions.append(path, "2026-09-25T06:00:00+00:00", {}, str)
    assert not path.exists()


def test_a_failed_write_leaves_the_ledger_as_it_was(tmp_path, monkeypatch):
    """Written to a temp file and renamed, so a crash mid-write never truncates the history."""
    path = tmp_path / "dedup_evictions.csv"
    dedup_evictions.append(path, "t0", {"a:b:1": "case-variant"}, lambda i: "a:b")
    before = path.read_bytes()

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(dedup_evictions.os, "replace", boom)
    with pytest.raises(OSError):
        dedup_evictions.append(path, "t1", {"a:b:2": "case-variant"}, lambda i: "a:b")
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]  # no temp file left behind


def test_run_ts_is_the_pipeline_stamp_when_one_is_set(monkeypatch):
    """`index prune` and `role_trends` run minutes apart; one stamp lets Trends join the two."""
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00Z")
    assert run_ts() == datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


def test_run_ts_is_now_outside_the_pipeline(monkeypatch):
    monkeypatch.delenv(RUN_TS_ENV, raising=False)
    before = datetime.now(UTC).replace(microsecond=0)
    assert before <= run_ts() <= datetime.now(UTC)
