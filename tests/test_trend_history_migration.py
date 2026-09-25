"""Tests for reading the Trends history's older layout as ADR-0230 step 6's
(`headstart.trend_history_migration`), which the migration script writes and the reader replays.
"""

from __future__ import annotations

import json
from pathlib import Path

import old_layout_trends_state as old
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headstart import trend_history_migration as migration


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return old.write(tmp_path / "state")


def _tables(state: Path) -> list[pa.Table]:
    return [
        pq.read_table(p) for p in sorted((state / "role_trend_board_deltas").glob("*"))
    ]


def _levels(table: pa.Table) -> dict:
    return {
        tuple(row[k] for k in ("board", "metric", "family", "band")): row["delta"]
        for row in table.to_pylist()
        if row["metric"] in ("stock", "new")
    }


def test_a_rebase_becomes_its_change_against_the_tick_before(state):
    ticks, rebased = migration.rewritten_ticks(
        _tables(state), state / "trends_epochs.csv"
    )

    assert rebased == [old.T[4]]
    assert _levels(ticks[2]) == {
        (old.ACME, "stock", "backend", "mid"): -4,
        (old.ACME, "new", "backend", "mid"): -2,
        (old.ACME, "stock", "software", "mid"): 4,
        (old.ACME, "new", "software", "mid"): 2,
        (old.BETA, "stock", "non-tech", "all"): 1,
    }
    # The turnover row is a count of its tick, so it passes through untouched.
    assert old.OPENED in [tuple(r.values()) for r in ticks[2].to_pylist()]


def test_summed_in_order_the_ticks_give_each_ticks_level(state):
    ticks, _ = migration.rewritten_ticks(_tables(state), state / "trends_epochs.csv")

    level: dict = {}
    for table, (version, expected) in zip(ticks, old.LEVELS.values(), strict=True):
        for key, delta in _levels(table).items():
            level[key] = level.get(key, 0) + delta
        assert {k: v for k, v in level.items() if v} == expected


def test_every_tick_carries_its_methodology_and_no_series_version(state):
    ticks, _ = migration.rewritten_ticks(_tables(state), state / "trends_epochs.csv")

    metadata = [table.schema.metadata for table in ticks]
    assert all(b"centroid_version" not in m for m in metadata)
    assert [m[b"ts"].decode() for m in metadata] == old.T[2:]
    classifier = [
        json.loads(m[b"methodology"])["family_classifier_version"] for m in metadata
    ]
    # Before the first epoch row a tick takes that row, which readers take as a baseline.
    assert classifier == ["none", "none", "3b5cc5d9183c", "3b5cc5d9183c"]
    assert ticks[0].schema.names == list(migration.TICK_COLUMNS)


def test_a_tick_that_names_its_own_methodology_keeps_it(state):
    path = old.tick_file(state, old.T[5])
    table = pq.read_table(path)
    own = {"family_classifier_version": 3, "family_list_fingerprint": "abc",
           "tech_filter_version": 1, "derivations_version": 13, "dedup_version": 1}  # fmt: skip
    metadata = {**table.schema.metadata, b"methodology": json.dumps(own).encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)

    ticks, _ = migration.rewritten_ticks(_tables(state), state / "trends_epochs.csv")

    assert json.loads(ticks[-1].schema.metadata[b"methodology"]) == own


def test_a_step6_tick_is_kept_and_an_old_one_after_it_is_refused(state):
    tables = _tables(state)
    ticks, _ = migration.rewritten_ticks(tables[:3], state / "trends_epochs.csv")
    written_by_the_step6_writer = ticks[-1]

    kept, _ = migration.rewritten_ticks(
        [*tables[:2], written_by_the_step6_writer], state / "trends_epochs.csv"
    )
    assert kept[-1] is written_by_the_step6_writer

    with pytest.raises(ValueError, match="after a step-6 one"):
        migration.rewritten_ticks(
            [*tables[:2], written_by_the_step6_writer, tables[3]],
            state / "trends_epochs.csv",
        )


def test_an_ats_that_is_not_the_boards_prefix_is_refused(state):
    path = old.tick_file(state, old.T[2])
    table = pq.read_table(path)
    pq.write_table(
        table.set_column(5, "ats", pa.array(["workday"] * table.num_rows)), path
    )

    with pytest.raises(ValueError, match="board_key's prefix"):
        migration.rewritten_ticks(_tables(state), state / "trends_epochs.csv")


def test_a_moving_centroid_version_is_refused(state):
    (state / "trends_epochs.csv").write_text(
        old.EPOCH_HEADER
        + f"{old.T[3]},1,abc,1,13,1,none\n{old.T[4]},2,abc,1,13,1,none\n"
    )

    with pytest.raises(ValueError, match="centroid_version moves"):
        migration.rewritten_ticks(_tables(state), state / "trends_epochs.csv")


def test_the_archive_holds_the_ticks_before_board_deltas_as_index_deltas(state):
    archive = migration.archive_from_aggregate(
        state / "role_trends.parquet", old.T[2], state / "trends_epochs.csv"
    )

    assert [
        (r["ts"], r["family"], r["ats"], r["delta"]) for r in archive.to_pylist()
    ] == [
        (old.T[0], "backend", "all", 5),
        (old.T[0], "non-tech", "all", 2),
        (old.T[1], "backend", "all", 1),
    ]
    assert json.loads(archive.schema.metadata[b"methodology"])["dedup_version"] == 1


def test_the_epoch_file_is_read_in_its_current_shape_only(state):
    (state / "trends_epochs.csv").write_text("ts,centroid_version\n")

    with pytest.raises(ValueError, match="unexpected header"):
        migration.read_epochs(state / "trends_epochs.csv")
