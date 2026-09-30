"""Tests for the Hugging Face open-apply-jobs miner (scripts/discover/mine_hf_open_apply_jobs.py):
its spellings, and that only a definitive not-found means a snapshot lacks a source."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_hf_open_apply_jobs as miner


def test_rows_follow_each_ledgers_own_spelling():
    assert miner.rows_for("gem", {"b-co", "a-co"}) == [
        ("a-co", "https://jobs.gem.com/a-co"),
        ("b-co", "https://jobs.gem.com/b-co"),
    ]
    assert miner.rows_for("rippling", {"acme"}) == [("acme", "ats.rippling.com/acme")]


def test_an_ashby_slug_with_a_space_keeps_it_in_the_tenant_and_writes_it_percent_encoded():
    """The 30 spaced rows already in the ledger are tenant `Blackpoint Cyber`, url `.../Blackpoint%20Cyber`."""
    assert miner.rows_for("ashby", {"Sine Engineering", "ambient.ai"}) == [
        ("Sine Engineering", "https://jobs.ashbyhq.com/Sine%20Engineering"),
        ("ambient.ai", "https://jobs.ashbyhq.com/ambient.ai"),
    ]


class _Rate429(OSError):
    """Stands for `HfHubHTTPError`, which is an OSError."""


class _FakeFs:
    """`ls` plays a script per path: each call takes the next step (the last one repeats); a step
    that is an exception is raised, any other step is the listing returned."""

    def __init__(self, table):
        self.table = {path: list(steps) for path, steps in table.items()}
        self.calls = []

    def ls(self, path, detail=False):
        self.calls.append(path)
        steps = self.table[path]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(miner.time, "sleep", lambda seconds: None)


def _dates(fs_table, date="date=2026-09-29", earlier="date=2026-09-28"):
    fs_table[miner.DATA] = [[f"{miner.DATA}/{earlier}", f"{miner.DATA}/{date}"]]
    return fs_table


def test_a_definitive_not_found_means_the_snapshot_lacks_that_source_and_the_previous_date_is_read(
    monkeypatch,
):
    table = _dates(
        {
            f"{miner.DATA}/date=2026-09-29/source=gem": [
                FileNotFoundError("no such folder")
            ],
            f"{miner.DATA}/date=2026-09-28/source=gem": [
                [f"{miner.DATA}/date=2026-09-28/source=gem/p.parquet"]
            ],
        }
    )
    monkeypatch.setattr(miner, "read_slugs", lambda fs, path: ["a", "b"])
    date, found = miner.snapshot_slugs(_FakeFs(table), "gem")
    assert (date, found) == ("date=2026-09-28", {"a", "b"})


def test_a_429_reads_as_an_error_to_retry_not_as_no_snapshot_that_date(monkeypatch):
    """`HfFileSystem.exists` swallowed the OSError, so a 429 or 5xx read as absence and the run
    silently fell back to an older snapshot."""
    folder = f"{miner.DATA}/date=2026-09-29/source=gem"
    table = _dates(
        {
            folder: [
                _Rate429("429 Too Many Requests"),
                _Rate429("503"),
                [f"{folder}/p.parquet"],
            ],
        }
    )
    fs = _FakeFs(table)
    monkeypatch.setattr(miner, "read_slugs", lambda fs, path: ["only"])
    date, found = miner.snapshot_slugs(fs, "gem")
    assert (date, found) == ("date=2026-09-29", {"only"})
    assert fs.calls.count(folder) == 3  # two failures retried, the third answered
    assert (
        f"{miner.DATA}/date=2026-09-28/source=gem" not in fs.calls
    )  # the older date was never asked


def test_a_call_that_never_succeeds_fails_the_run_loudly_after_the_attempts(
    monkeypatch,
):
    folder = f"{miner.DATA}/date=2026-09-29/source=gem"
    fs = _FakeFs(_dates({folder: [_Rate429("429")]}))
    with pytest.raises(miner.FetchFailed):
        miner.snapshot_slugs(fs, "gem")
    assert fs.calls.count(folder) == miner.ATTEMPTS


def test_every_call_is_paced(monkeypatch):
    pauses = []
    monkeypatch.setattr(miner.time, "sleep", pauses.append)
    assert miner.with_backoff("x", lambda: "ok") == "ok"
    assert pauses == [miner.PACE]


def test_a_source_no_snapshot_carries_is_an_error(monkeypatch):
    table = _dates(
        {
            f"{miner.DATA}/date=2026-09-29/source=gem": [FileNotFoundError("x")],
            f"{miner.DATA}/date=2026-09-28/source=gem": [FileNotFoundError("x")],
        }
    )
    with pytest.raises(SystemExit):
        miner.snapshot_slugs(_FakeFs(table), "gem")
