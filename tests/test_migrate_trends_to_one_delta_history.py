"""Tests for the one-off Trends state migration (scripts/state/migrate_trends_to_one_delta_history.py,
ADR-0230 step 6): what it writes, and that its check catches a history that does not reproduce
the files it replaces. How it rewrites a tick is `trend_history_migration`'s, tested there.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import old_layout_trends_state as old
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "state"
    / "migrate_trends_to_one_delta_history.py"
)


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location(
        "migrate_trends_to_one_delta_history", _SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # the dataclasses resolve their annotations through it
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return old.write(tmp_path / "state")


def test_the_rewritten_history_reproduces_every_file_it_replaces(mig, state, tmp_path):
    migration = mig.migrate(state, tmp_path / "out")
    check = mig.verify(tmp_path / "out", state)

    assert migration.rebased == [old.T[4]]
    assert (migration.ticks, migration.archive_ticks) == (4, 2)
    assert check.board_count_keys_differing == 0
    assert check.aggregate_ticks_compared == check.aggregate_ticks == 6
    assert check.aggregate_ticks_differing == []
    assert check.counting_changes == check.epoch_boundaries == [old.T[4]]
    assert check.clean


def test_it_rewrites_every_tick_file_in_place_and_writes_the_archive(
    mig, state, tmp_path
):
    mig.migrate(state, tmp_path / "out")

    written = sorted(p.name for p in (tmp_path / "out" / mig.DELTAS).glob("*"))
    assert written == sorted(p.name for p in (state / mig.DELTAS).glob("*"))
    for name in written:
        schema = pq.read_schema(tmp_path / "out" / mig.DELTAS / name)
        assert schema.names == ["board", "metric", "family", "band", "delta"]
        assert b"centroid_version" not in schema.metadata
        assert json.loads(schema.metadata[b"methodology"])
    assert pq.read_metadata(tmp_path / "out" / mig.ARCHIVE).num_rows == 3


def test_the_check_fails_on_a_count_the_history_does_not_reproduce(
    mig, state, tmp_path
):
    mig.migrate(state, tmp_path / "out")
    snapshot = pq.read_table(state / "role_trend_board_counts.parquet")
    counts = snapshot["count"].to_pylist()
    counts[0] += 1
    pq.write_table(
        snapshot.set_column(5, "count", pa.array(counts, pa.int64())),
        state / "role_trend_board_counts.parquet",
    )

    check = mig.verify(tmp_path / "out", state)

    assert check.board_count_keys_differing == 1
    assert not check.clean


def test_the_check_fails_on_an_aggregate_tick_the_history_lacks(mig, state, tmp_path):
    mig.migrate(state, tmp_path / "out")
    (tmp_path / "out" / mig.DELTAS / old.tick_file(state, old.T[3]).name).unlink()

    check = mig.verify(tmp_path / "out", state)

    assert check.aggregate_ticks_compared == 5 < check.aggregate_ticks
    assert not check.clean


def test_the_check_refuses_a_file_still_in_the_old_layout(mig, state, tmp_path):
    with pytest.raises(ValueError, match="not in the step-6 layout"):
        mig.written_ticks(state / mig.DELTAS)


@pytest.mark.parametrize(
    ("answers", "refusal"),
    [
        (["active"], "pause the chain first"),
        (["disabled_manually", "1"], "still in flight"),
        (["disabled_manually", "0"], None),
    ],
)
def test_the_live_run_needs_the_chain_paused(mig, monkeypatch, answers, refusal):
    replies = iter(answers)

    class Done:
        def __init__(self) -> None:
            self.stdout = next(replies)

    monkeypatch.setattr(mig.subprocess, "run", lambda *a, **k: Done())

    found = mig.chain_refusal()

    assert (found is None) if refusal is None else (refusal in found)
