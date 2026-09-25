"""Tests for the one-off Trends state migration (scripts/state/migrate_trends_to_one_delta_history.py,
ADR-0230 step 6).

The fixture writes a small state in the old layout the way the old writer did: a Board-delta file
per tick counted against the running total **at its own series version**, so a new version's first
tick is a baseline; the aggregate ledger and the Board-count snapshot as sums of the same levels;
and an epoch row only where the methodology moved.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

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


T = [f"2026-09-{day:02d}T00:00:00+00:00" for day in range(10, 16)]
# Two ticks before per-Board counting, which only the aggregate holds.
ARCHIVED = {
    T[0]: {
        ("stock", "backend", "mid", "all"): 5,
        ("stock", "non-tech", "all", "all"): 2,
    },
    T[1]: {
        ("stock", "backend", "mid", "all"): 6,
        ("stock", "non-tech", "all", "all"): 2,
    },
}
ACME, BETA = "greenhouse:acme", "lever:beta"
# Board levels at each later tick, and the series version the old writer counted it at. The
# version-2001 tick moves acme's jobs to another family: a re-base.
LEVELS = {
    T[2]: ("2", {(ACME, "stock", "backend", "mid"): 3, (ACME, "new", "backend", "mid"): 1,
                 (BETA, "stock", "non-tech", "all"): 2}),
    T[3]: ("2", {(ACME, "stock", "backend", "mid"): 4, (ACME, "new", "backend", "mid"): 2,
                 (BETA, "stock", "non-tech", "all"): 2}),
    T[4]: ("2001", {(ACME, "stock", "software", "mid"): 4, (ACME, "new", "software", "mid"): 2,
                    (BETA, "stock", "non-tech", "all"): 3}),
    T[5]: ("2001", {(ACME, "stock", "software", "mid"): 5, (ACME, "new", "software", "mid"): 3,
                    (BETA, "stock", "non-tech", "all"): 3}),
}  # fmt: skip
# One turnover row (ADR-0227), which is a count of its tick, not a change in a level.
OPENED = (ACME, "opened", "software", "mid", 1)
EPOCH_HEADER = (
    "ts,centroid_version,family_map_fingerprint,tech_filter_version,derivations_version,"
    "dedup_version,family_classifier_version\n"
)
EPOCHS = EPOCH_HEADER + f"{T[3]},2,abc,1,13,1,none\n{T[4]},2,abc,1,13,1,3b5cc5d9183c\n"


def _index(levels: dict) -> dict:
    out: dict = {}
    for (board, metric, family, band), n in levels.items():
        key = (
            (metric, family, "all", "all")
            if family == "non-tech"
            else (metric, family, band, board.split(":")[0])
        )
        out[key] = out.get(key, 0) + n
    return out


def _write_old_tick(directory: Path, ts: str, version: str, rows: list[tuple]) -> None:
    names = ("ts", "board", "metric", "family", "band", "ats", "delta")
    full = [(ts, board, metric, family, band, board.split(":")[0], delta)
            for board, metric, family, band, delta in rows]  # fmt: skip
    columns = list(zip(*full)) if full else [()] * len(names)
    table = pa.table(
        {name: list(col) for name, col in zip(names, columns)},
        schema=pa.schema(
            [(n, pa.int64() if n == "delta" else pa.string()) for n in names],
            metadata={b"centroid_version": version.encode()},
        ),
    )
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / f"{ts.replace(':', '-')}.parquet")


@pytest.fixture
def state(tmp_path: Path) -> Path:
    """An old-layout `data/state`, written the way the old writer wrote it."""
    root = tmp_path / "state"
    by_version: dict[str, dict] = {}
    for ts, (version, levels) in LEVELS.items():
        before = by_version.get(version, {})
        rows = [
            (*key, levels.get(key, 0) - before.get(key, 0))
            for key in sorted(before.keys() | levels.keys())
            if levels.get(key, 0) != before.get(key, 0)
        ]
        if ts == T[4]:
            rows.append(OPENED)
        _write_old_tick(root / "role_trend_board_deltas", ts, version, rows)
        by_version[version] = levels
    aggregate = [
        (ts, version, *key, n)
        for ts, (version, index) in [
            *((ts, ("2", level)) for ts, level in ARCHIVED.items()),
            *(
                (ts, (version, _index(levels)))
                for ts, (version, levels) in LEVELS.items()
            ),
        ]
        for key, n in index.items()
    ]
    ts, version, metric, family, band, ats, count = zip(*aggregate)
    pq.write_table(
        pa.table({
            "ts": pa.array([datetime.fromisoformat(t) for t in ts],
                           pa.timestamp("ms", tz="UTC")),
            "version": pa.array([int(v) for v in version], pa.int64()),
            "metric": list(metric), "family": list(family), "band": list(band),
            "ats": list(ats), "count": pa.array(count, pa.int64()),
        }),
        root / "role_trends.parquet",
    )  # fmt: skip
    last = LEVELS[T[5]][1]
    rows = sorted((*key, key[0].split(":")[0], n) for key, n in last.items())
    names = ("board", "metric", "family", "band", "ats", "count")
    pq.write_table(
        pa.table(
            {name: [r[i] for r in rows] for i, name in enumerate(names)},
            metadata={b"centroid_version": b"2001", b"as_of": T[5].encode()},
        ),
        root / "role_trend_board_counts.parquet",
    )
    (root / "trends_epochs.csv").write_text(EPOCHS)
    return root


def _levels(tick) -> dict:
    return {r[:4]: r[4] for r in tick.rows if r[1] in ("stock", "new")}


def test_a_rebase_becomes_its_change_against_the_tick_before(mig, state, tmp_path):
    mig.migrate(state, tmp_path / "out")

    ticks = mig.read_ticks(tmp_path / "out" / "role_trend_board_deltas")
    rebase = ticks[2]
    assert rebase.ts == T[4]
    assert _levels(rebase) == {
        (ACME, "stock", "backend", "mid"): -4,
        (ACME, "new", "backend", "mid"): -2,
        (ACME, "stock", "software", "mid"): 4,
        (ACME, "new", "software", "mid"): 2,
        (BETA, "stock", "non-tech", "all"): 1,
    }
    # The turnover row is a count of its tick, so it passes through untouched.
    assert OPENED in rebase.rows


def test_the_rewritten_history_reproduces_every_file_it_replaces(mig, state, tmp_path):
    migration = mig.migrate(state, tmp_path / "out")
    check = mig.verify(tmp_path / "out", state)

    assert migration.rebased == [T[4]]
    assert check.board_count_keys_differing == 0
    assert check.aggregate_ticks_compared == check.aggregate_ticks == 6
    assert check.aggregate_ticks_differing == []
    assert check.counting_changes == check.epoch_boundaries == [T[4]]
    assert check.clean


def test_verification_fails_on_a_count_the_history_does_not_reproduce(
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


def test_every_tick_carries_its_methodology_and_no_series_version(mig, state, tmp_path):
    mig.migrate(state, tmp_path / "out")

    paths = sorted((tmp_path / "out" / "role_trend_board_deltas").glob("*.parquet"))
    metadata = [pq.read_schema(p).metadata for p in paths]
    assert all(b"centroid_version" not in m for m in metadata)
    assert [m[b"ts"].decode() for m in metadata] == T[2:]
    classifier = [
        json.loads(m[b"methodology"])["family_classifier_version"] for m in metadata
    ]
    # Before the first epoch row a tick takes that row, which the Space reads as a baseline.
    assert classifier == ["none", "none", "3b5cc5d9183c", "3b5cc5d9183c"]
    assert pq.read_schema(paths[0]).names == [
        "board",
        "metric",
        "family",
        "band",
        "delta",
    ]


def test_a_tick_that_names_its_own_methodology_keeps_it(mig, state, tmp_path):
    path = state / "role_trend_board_deltas" / f"{T[5].replace(':', '-')}.parquet"
    table = pq.read_table(path)
    own = {"family_classifier_version": 3, "family_list_fingerprint": "abc",
           "tech_filter_version": 1, "derivations_version": 13, "dedup_version": 1}  # fmt: skip
    metadata = {**table.schema.metadata, b"methodology": json.dumps(own).encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)

    mig.migrate(state, tmp_path / "out")

    ticks = mig.read_ticks(tmp_path / "out" / "role_trend_board_deltas")
    assert ticks[-1].methodology == own


def test_the_archive_holds_the_ticks_before_board_deltas_as_index_deltas(
    mig, state, tmp_path
):
    migration = mig.migrate(state, tmp_path / "out")

    archive = pq.read_table(tmp_path / "out" / mig.ARCHIVE).to_pylist()
    assert migration.archive_ticks == 2
    assert [(r["ts"], r["family"], r["ats"], r["delta"]) for r in archive] == [
        (T[0], "backend", "all", 5),
        (T[0], "non-tech", "all", 2),
        (T[1], "backend", "all", 1),
    ]


def test_an_ats_that_is_not_the_boards_prefix_is_refused(mig, state):
    path = state / "role_trend_board_deltas" / f"{T[2].replace(':', '-')}.parquet"
    table = pq.read_table(path)
    table = table.set_column(5, "ats", pa.array(["workday"] * table.num_rows))
    pq.write_table(table, path)

    with pytest.raises(ValueError, match="board_key's prefix"):
        mig.read_ticks(state / "role_trend_board_deltas")


def test_an_old_layout_tick_after_a_new_layout_one_is_refused(mig, state, tmp_path):
    ticks = mig.read_ticks(state / "role_trend_board_deltas")
    new_layout = mig.replace(ticks[1], series_version=None, methodology={})

    with pytest.raises(ValueError, match="after a new-layout one"):
        mig.without_rebases([ticks[0], new_layout, ticks[2]])


def test_a_new_layout_tick_passes_through(mig, state, tmp_path):
    ticks = mig.read_ticks(state / "role_trend_board_deltas")
    written_by_the_new_writer = mig.replace(
        ticks[3], series_version=None, methodology={}
    )

    out, rebased = mig.without_rebases([*ticks[:3], written_by_the_new_writer])

    assert out[3] is written_by_the_new_writer
    assert rebased == [T[4]]


def test_a_moving_centroid_version_is_refused(mig, state, tmp_path):
    (state / "trends_epochs.csv").write_text(
        EPOCH_HEADER + f"{T[3]},1,abc,1,13,1,none\n{T[4]},2,abc,1,13,1,none\n"
    )

    with pytest.raises(ValueError, match="centroid_version moves"):
        mig.migrate(state, tmp_path / "out")


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


def test_the_epoch_file_is_read_in_its_current_shape_only(mig, state):
    with (state / "trends_epochs.csv").open("w", newline="") as fh:
        csv.writer(fh).writerow(["ts", "centroid_version"])

    with pytest.raises(ValueError, match="unexpected header"):
        mig.read_epochs(state / "trends_epochs.csv")
