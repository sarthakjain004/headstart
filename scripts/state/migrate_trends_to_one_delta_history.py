#!/usr/bin/env python3
"""Rewrite the Trends state on HF into ADR-0230's one Board-delta history (design §9, step 6).

Until ADR-0230, `role_trends` stored the same counts three ways and marked a re-base by switching
series version. This rewrites the stored history once, so the history needs no series versions,
no epoch file and no aggregate to read. Everything it touches sits under `data/state/`:

- **Every tick file in `role_trend_board_deltas/` is rewritten in place,** in the layout the
  step-6 writer uses (`trend_history.record_tick`): columns `(board, metric, family, band,
  delta)`, and the tick's `ts` and `methodology` in the file's metadata.
  - **A re-base becomes an ordinary delta.** A tick at a new series version held that version's
    whole count, because the writer found no snapshot at it (on 2026-09-25: one tick,
    2026-09-24T21:19:12, series 2 to 2001). It is rewritten as its change against the tick
    before it, so the history replays by plain summation.
  - **The epoch rows move into the ticks.** A tick written before #688 carries no methodology,
    so it takes the `trends_epochs.csv` row in force at it. A tick before the first row takes the
    first row, which the Space already reads as a baseline and not a boundary. The row's
    `centroid_version` is dropped, and the script refuses if that loses a boundary: it read 2 on
    every row, and no centroid decides anything since ADR-0220.
  - **The `ats` column goes.** It is the board_key's prefix on every row, and the script refuses
    to drop it from a file where it is not.
  - **A file already in the new layout is kept as it is.** The step-6 writer writes one per tick
    from the moment its PR merges, whether or not this has run yet.
- **The archive `role_trend_index_deltas_before_board_deltas.parquet` is written.** The
  aggregate's ticks from before per-Board counting began (615 ticks, 2026-08-11 to 2026-09-13
  11:14:57) exist nowhere else. They are kept as index-wide group deltas `(ts, metric, family,
  band, ats, delta)`, with their methodology in the file's metadata. `ats` stays here: an
  index-wide row has no Board to read it from.

**It verifies the files it wrote, read back from disk, before anything leaves this machine:**
- replaying the rewritten files gives `role_trend_board_counts.parquet` at its `as_of`, with 0
  differing keys;
- replaying the archive, then the rewritten files, gives `role_trends.parquet` at every tick it
  holds;
- the ticks whose methodology differs from the tick before are exactly the `trends_epochs.csv`
  boundaries.

**It deletes nothing.** The four files this history replaces retire later, through
`retire_superseded_trends_files.py`, once the Space from this step is live.

**Dry run by default.** It fetches the four inputs from HF, read-only, into a work directory,
migrates and verifies there, and reports. `--state-dir` dry-runs a local copy instead.

**`--apply` writes, and refuses unless the pipeline's chain is paused:** `pipeline.yml` disabled
and no run of it in flight. Nothing but the pipeline's `merge` writes `data/state`, and a run
that fetched before this commit and uploaded after it would put every old file back. Then it runs
under `state_guard` (ADR-0129): it records `data/state`'s fingerprint before the fetch, verifies
it again before the upload, and publishes every rewritten file and the archive in one commit.

The merge job super-squashes the dataset every run, so the old files cannot be restored from a
prior revision. The dry run's work directory keeps the fetched originals.

Usage:
    python scripts/state/migrate_trends_to_one_delta_history.py                   # dry run on HF
    python scripts/state/migrate_trends_to_one_delta_history.py --state-dir DIR   # dry run, local
    python scripts/state/migrate_trends_to_one_delta_history.py --apply           # rewrite on HF
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

# Never over Xet: it dies silently mid-transfer (see CLAUDE.md). Set before huggingface_hub loads.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import pyarrow as pa
import pyarrow.parquet as pq

from headstart import log, roles
from headstart.board_identity import ats_of

REPO = "imPoseidon/headstart-index"
STATE = "data/state"
DELTAS = "role_trend_board_deltas"
ARCHIVE = "role_trend_index_deltas_before_board_deltas.parquet"
AGGREGATE = "role_trends.parquet"
BOARD_COUNTS = "role_trend_board_counts.parquet"
EPOCHS = "trends_epochs.csv"

# The metrics whose deltas sum to a level. A tick file's other rows (ADR-0227's turnover and
# markers) are counts of that tick, and pass through unchanged.
LEVEL_METRICS = ("stock", "new")
# The old layout's series-version key, misnamed since ADR-0220 (design §3.4, D3).
_SERIES_KEY = b"centroid_version"
# The aggregate's one undecomposed row per tick: non-tech, unbanded and across every ATS.
_NOT_SPLIT = "all"

Key = tuple[str, str, str, str]  # (board, metric, family, band)
Row = tuple[str, str, str, str, int]  # a Key and its delta
IndexKey = tuple[str, str, str, str]  # (metric, family, band, ats)


@dataclass
class Tick:
    """One tick's file: its rows, and how it was counted."""

    ts: str
    name: str  # the file's name, kept so the rewrite replaces it in place
    rows: list[Row]
    methodology: dict | None
    series_version: str | None  # the old layout's; None once in the new layout


def read_tick(path: Path) -> Tick:
    """One tick file, in either layout. The old one carries the series version in its metadata,
    and `ts` and `ats` columns; the new one carries `ts` and `methodology` in its metadata."""
    table = pq.read_table(path)
    metadata = table.schema.metadata or {}
    columns = {name: table[name].to_pylist() for name in table.schema.names}
    rows = list(
        zip(
            columns["board"],
            columns["metric"],
            columns["family"],
            columns["band"],
            columns["delta"],
            strict=True,
        )
    )
    stamped = metadata.get(b"methodology")
    methodology = json.loads(stamped) if stamped else None
    if _SERIES_KEY not in metadata:
        return Tick(metadata[b"ts"].decode(), path.name, rows, methodology, None)
    stamps = set(columns["ts"]) | (
        {metadata[b"ts"].decode()} if b"ts" in metadata else set()
    )
    if len(stamps) != 1:
        raise ValueError(
            f"{path.name}: expected one tick stamp, found {sorted(stamps)}"
        )
    wrong = sum(
        1
        for board, ats in zip(columns["board"], columns["ats"])
        if ats_of(board) != ats
    )
    if wrong:
        raise ValueError(
            f"{path.name}: {wrong} row(s) carry an `ats` that is not their board_key's prefix, "
            "so dropping the column would lose it"
        )
    return Tick(
        stamps.pop(), path.name, rows, methodology, metadata[_SERIES_KEY].decode()
    )


def read_ticks(directory: Path) -> list[Tick]:
    ticks = sorted(
        (read_tick(path) for path in directory.glob("*.parquet")), key=lambda t: t.ts
    )
    if len({t.ts for t in ticks}) != len(ticks):
        raise ValueError(f"{directory}: two files hold one tick")
    return ticks


def _apply(counts: dict[Key, int], rows: list[Row]) -> None:
    """Add level ``rows`` to ``counts``, keeping only keys that are not zero."""
    for board, metric, family, band, delta in rows:
        key = (board, metric, family, band)
        value = counts.get(key, 0) + delta
        if value:
            counts[key] = value
        else:
            counts.pop(key, None)


def without_rebases(ticks: list[Tick]) -> tuple[list[Tick], list[str]]:
    """Every tick as a change against the tick before it, and the stamps of the re-bases rewritten.

    The old writer counted each tick against the sum of every earlier delta **at the tick's own
    series version** (`_load_board_counts` plus `_recover_board_counts`). So the level at a tick
    is that per-version sum, whatever the order of versions. A tick whose version differs from the
    tick before it is a re-base, and becomes that level less the level before it."""
    level: dict[Key, int] = {}
    by_version: dict[str, dict[Key, int]] = {}
    out: list[Tick] = []
    rebased: list[str] = []
    previous: Tick | None = None
    for tick in ticks:
        if tick.series_version is None:
            _apply(level, [r for r in tick.rows if r[1] in LEVEL_METRICS])
            out.append(tick)
        else:
            if previous is not None and previous.series_version is None:
                # The new writer counts against this history's replay, so an old-layout tick
                # after it would be read against the wrong base. Only a reverted writer does this.
                raise ValueError(
                    f"{tick.ts}: an old-layout tick after a new-layout one"
                )
            levels = [r for r in tick.rows if r[1] in LEVEL_METRICS]
            others = [r for r in tick.rows if r[1] not in LEVEL_METRICS]
            counts = by_version.setdefault(tick.series_version, {})
            _apply(counts, levels)
            if previous is not None and previous.series_version != tick.series_version:
                levels = [
                    (*key, counts.get(key, 0) - level.get(key, 0))
                    for key in sorted(counts.keys() | level.keys())
                    if counts.get(key, 0) != level.get(key, 0)
                ]
                rebased.append(tick.ts)
            _apply(level, levels)
            out.append(replace(tick, rows=levels + others, series_version=None))
        previous = tick
    return out, rebased


_EPOCH_COLUMNS = (
    "ts",
    "centroid_version",
    "family_map_fingerprint",
    "tech_filter_version",
    "derivations_version",
    "dedup_version",
    "family_classifier_version",
)


def read_epochs(path: Path) -> list[dict[str, str]]:
    """`trends_epochs.csv`'s rows, oldest first, in its current shape only: the pipeline upgrades
    an older header on every run, so another shape means the file is not what this expects."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != _EPOCH_COLUMNS:
            raise ValueError(f"{path.name}: unexpected header {reader.fieldnames}")
        rows = list(reader)
    if not rows or [r["ts"] for r in rows] != sorted(r["ts"] for r in rows):
        raise ValueError(f"{path.name}: expected rows, oldest first")
    return rows


def epoch_in_force(epochs: list[dict[str, str]], ts: str) -> dict[str, str]:
    """The epoch row a tick was counted under: the last one at or before it, or the first one for
    a tick before any, which the Space reads as a baseline rather than a boundary."""
    found = epochs[0]
    for row in epochs:
        if row["ts"] > ts:
            break
        found = row
    return found


def methodology_of(row: dict[str, str]) -> dict[str, int | str]:
    """An epoch row as the tick metadata #688 writes: its keys, and its integers as integers.
    The classifier column holds text before the classifier head: `none` in the centroid era and
    the title rules' fingerprint under series 2001."""
    classifier = row["family_classifier_version"]
    return {
        "family_list_fingerprint": row["family_map_fingerprint"],
        "family_classifier_version": int(classifier)
        if classifier.isdigit()
        else classifier,
        "tech_filter_version": int(row["tech_filter_version"]),
        "derivations_version": int(row["derivations_version"]),
        "dedup_version": int(row["dedup_version"]),
    }


def with_methodology(ticks: list[Tick], epochs: list[dict[str, str]]) -> list[Tick]:
    """Every tick with its methodology: its own when it has one, else its epoch row's.

    Refuses when the epoch rows it reads disagree on `centroid_version`, the one column the
    metadata drops, since dropping it would then erase a boundary."""
    used: list[dict[str, str]] = []
    out: list[Tick] = []
    for tick in ticks:
        if tick.methodology is None:
            row = epoch_in_force(epochs, tick.ts)
            used.append(row)
            tick = replace(tick, methodology=methodology_of(row))
        out.append(tick)
    centroid = {row["centroid_version"] for row in used}
    if len(centroid) > 1:
        raise ValueError(
            f"centroid_version moves between epoch rows ({sorted(centroid)})"
        )
    return out


def _codes(column: pa.ChunkedArray) -> tuple[object, list[str]]:
    encoded = column.combine_chunks().dictionary_encode()
    return encoded.indices.to_numpy(), encoded.dictionary.to_pylist()


def aggregate_ticks(path: Path) -> Iterator[tuple[str, dict[IndexKey, int]]]:
    """The aggregate ledger's non-zero groups at each tick, oldest first.

    One tick at a time, never one dict per row: as per-row dicts this file held 4.75 GB in the
    Space (design §4). Strings are dictionary-encoded, so each distinct one exists once."""
    import numpy as np

    table = pq.read_table(
        path, columns=["ts", "metric", "family", "band", "ats", "count"]
    )
    stamps = table["ts"].combine_chunks().cast(pa.int64()).to_numpy()
    counts = table["count"].to_numpy()
    codes = [_codes(table[name]) for name in ("metric", "family", "band", "ats")]
    order = np.argsort(stamps, kind="stable")
    ordered = stamps[order]
    starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]]).tolist()
    for start, end in zip(starts, [*starts[1:], len(ordered)], strict=True):
        rows = order[start:end]
        keys = zip(
            *(
                map(names.__getitem__, indices[rows].tolist())
                for indices, names in codes
            ),
            strict=True,
        )
        level: dict[IndexKey, int] = {}
        for key, n in zip(keys, counts[rows].tolist(), strict=True):
            level[key] = level.get(key, 0) + n
        ts = datetime.fromtimestamp(int(ordered[start]) / 1000, UTC).isoformat(
            timespec="seconds"
        )
        yield ts, {key: n for key, n in level.items() if n}


def index_archive(aggregate: Path, before: str) -> tuple[list[tuple], list[str]]:
    """The aggregate's ticks before ``before`` as index-wide group deltas, the first against
    nothing, and the stamps of those ticks."""
    rows: list[tuple] = []
    ticks: list[str] = []
    previous: dict[IndexKey, int] = {}
    for ts, level in aggregate_ticks(aggregate):
        if ts >= before:
            break
        rows.extend(
            (ts, *key, level.get(key, 0) - previous.get(key, 0))
            for key in sorted(level.keys() | previous.keys())
            if level.get(key, 0) != previous.get(key, 0)
        )
        ticks.append(ts)
        previous = level
    return rows, ticks


def _metadata(methodology: dict, ts: str | None = None) -> dict[bytes, bytes]:
    out = {b"methodology": json.dumps(methodology, sort_keys=True).encode()}
    if ts is not None:
        out[b"ts"] = ts.encode()
    return out


def _table(
    names: tuple[str, ...], rows: list[tuple], metadata: dict[bytes, bytes]
) -> pa.Table:
    columns = list(zip(*rows, strict=True)) if rows else [()] * len(names)
    schema = pa.schema(
        [(name, pa.int64() if name == "delta" else pa.string()) for name in names],
        metadata=metadata,
    )
    return pa.table(dict(zip(names, map(list, columns), strict=True)), schema=schema)


def write_tick(directory: Path, tick: Tick) -> None:
    """One tick's file in the new layout, under its old name so the upload replaces it."""
    table = _table(
        ("board", "metric", "family", "band", "delta"),
        tick.rows,
        _metadata(tick.methodology or {}, tick.ts),
    )
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / tick.name, compression="zstd")


def write_archive(path: Path, rows: list[tuple], methodology: dict) -> None:
    table = _table(
        ("ts", "metric", "family", "band", "ats", "delta"), rows, _metadata(methodology)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", use_dictionary=True)


@dataclass
class Migration:
    ticks: int
    rows_before: int
    rows_after: int
    rebased: list[str]
    archive_ticks: int
    archive_rows: int


def migrate(source: Path, out: Path) -> Migration:
    """Write ``source``'s Trends state (a `data/state` directory) to ``out`` in the new layout:
    every tick file under ``out/role_trend_board_deltas/``, and the archive beside it."""
    ticks = read_ticks(source / DELTAS)
    if not ticks:
        raise ValueError(f"{source / DELTAS}: no tick files")
    epochs = read_epochs(source / EPOCHS)
    migrated, rebased = without_rebases(ticks)
    migrated = with_methodology(migrated, epochs)
    for tick in migrated:
        write_tick(out / DELTAS, tick)
    archive_rows, archive_ticks = index_archive(source / AGGREGATE, before=ticks[0].ts)
    in_force = {
        json.dumps(methodology_of(epoch_in_force(epochs, ts))) for ts in archive_ticks
    }
    if len(in_force) > 1:
        raise ValueError(
            f"the archive's ticks span {len(in_force)} methodologies, not one"
        )
    archived = json.loads(in_force.pop()) if in_force else methodology_of(epochs[0])
    write_archive(out / ARCHIVE, archive_rows, archived)
    return Migration(
        ticks=len(ticks),
        rows_before=sum(len(t.rows) for t in ticks),
        rows_after=sum(len(t.rows) for t in migrated),
        rebased=rebased,
        archive_ticks=len(archive_ticks),
        archive_rows=len(archive_rows),
    )


def _index_key(board: str, metric: str, family: str, band: str) -> IndexKey:
    """Where a Board's row sums in the aggregate: non-tech as one row across every ATS."""
    if family == roles.NON_TECH:
        return (metric, family, _NOT_SPLIT, _NOT_SPLIT)
    return (metric, family, band, ats_of(board))


def _bump(counts: dict, key, delta: int) -> None:
    value = counts.get(key, 0) + delta
    if value:
        counts[key] = value
    else:
        counts.pop(key, None)


def _differing(ours: dict, theirs: dict) -> int:
    return sum(
        1 for key in ours.keys() | theirs.keys() if ours.get(key) != theirs.get(key)
    )


@dataclass
class Verification:
    """What replaying the new layout reproduced of the files it replaces."""

    board_counts_as_of: str = ""
    board_count_keys: int = 0
    board_count_keys_differing: int | None = (
        None  # None: the as_of tick is not in the history
    )
    aggregate_ticks: int = 0
    aggregate_ticks_compared: int = 0
    aggregate_ticks_differing: list[str] = field(default_factory=list)
    aggregate_keys_differing: int = 0
    counting_changes: list[str] = field(default_factory=list)
    epoch_boundaries: list[str] = field(default_factory=list)
    history_ticks: int = 0

    @property
    def clean(self) -> bool:
        return (
            self.board_counts_as_of != ""
            and self.board_count_keys_differing == 0
            and self.aggregate_ticks_compared == self.aggregate_ticks > 0
            and not self.aggregate_ticks_differing
            and self.counting_changes == self.epoch_boundaries
        )


def _archive_ticks(path: Path) -> Iterator[tuple[str, list[tuple]]]:
    table = pq.read_table(path)
    rows = zip(*(table[name].to_pylist() for name in table.schema.names), strict=True)
    ts, group = None, []
    for row in rows:
        if row[0] != ts and group:
            yield ts, group
            group = []
        ts = row[0]
        group.append(row[1:])
    if group:
        yield ts, group


def verify(migrated: Path, source: Path) -> Verification:
    """Replay the new-layout files in ``migrated`` and compare them with what ``source`` holds
    of the same facts: the Board-count snapshot, the aggregate at every tick, and the epochs."""
    result = Verification()
    ticks = read_ticks(migrated / DELTAS)
    if any(t.series_version is not None for t in ticks):
        raise ValueError(f"{migrated / DELTAS}: holds an old-layout tick")
    snapshot = pq.read_table(source / BOARD_COUNTS)
    result.board_counts_as_of = (
        (snapshot.schema.metadata or {}).get(b"as_of", b"").decode()
    )
    expected_boards: dict[Key, int] = {}
    for *key, count in zip(
        *(
            snapshot[name].to_pylist()
            for name in ("board", "metric", "family", "band", "count")
        ),
        strict=True,
    ):
        _bump(expected_boards, tuple(key), count)
    result.board_count_keys = len(expected_boards)

    aggregate = aggregate_ticks(source / AGGREGATE)
    pending = next(aggregate, None)
    last_aggregate_tick = ""

    def compare(ts: str, index: dict[IndexKey, int]) -> None:
        nonlocal pending, last_aggregate_tick
        while pending is not None and pending[0] <= ts:
            result.aggregate_ticks += 1
            last_aggregate_tick = pending[0]
            if pending[0] == ts:
                result.aggregate_ticks_compared += 1
                differing = _differing(index, pending[1])
                if differing:
                    result.aggregate_ticks_differing.append(ts)
                    result.aggregate_keys_differing += differing
            pending = next(aggregate, None)

    methodologies: list[tuple[str, dict]] = []
    archive = pq.read_metadata(migrated / ARCHIVE).metadata or {}
    archived = json.loads(archive[b"methodology"])
    index: dict[IndexKey, int] = {}
    for ts, rows in _archive_ticks(migrated / ARCHIVE):
        for metric, family, band, ats, delta in rows:
            _bump(index, (metric, family, band, ats), delta)
        compare(ts, index)
        methodologies.append((ts, archived))

    boards: dict[Key, int] = {}
    index = {}
    for tick in ticks:
        for board, metric, family, band, delta in tick.rows:
            if metric in LEVEL_METRICS:
                _bump(boards, (board, metric, family, band), delta)
                _bump(index, _index_key(board, metric, family, band), delta)
        compare(tick.ts, index)
        if tick.ts == result.board_counts_as_of:
            result.board_count_keys_differing = _differing(boards, expected_boards)
        methodologies.append((tick.ts, tick.methodology or {}))
    while pending is not None:
        result.aggregate_ticks += 1
        pending = next(aggregate, None)

    result.history_ticks = len(methodologies)
    result.counting_changes = [
        ts
        for (_, before), (ts, after) in pairwise(methodologies)
        if before != after and ts <= last_aggregate_tick
    ]
    result.epoch_boundaries = [row["ts"] for row in read_epochs(source / EPOCHS)[1:]]
    return result


def chain_refusal() -> str | None:
    """Why the pipeline's chain is not paused, or None when it is: `pipeline.yml` disabled (so
    neither the cron nor a chained dispatch can start a run) and no run of it in flight."""

    def gh(*args: str) -> str:
        return subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    state = gh(
        "api", "repos/{owner}/{repo}/actions/workflows/pipeline.yml", "--jq", ".state"
    )
    if state != "disabled_manually":
        return f"pipeline.yml is {state!r}; pause the chain first: gh workflow disable pipeline.yml"
    running = gh(
        "run", "list", "--workflow=pipeline.yml", "--limit", "20", "--json", "status",
        "--jq", '[.[] | select(.status != "completed")] | length',
    )  # fmt: skip
    if running != "0":
        return f"{running} pipeline run(s) still in flight; wait for them to finish"
    return None


def fetch(work: Path) -> Path:
    """The four inputs, fetched from HF into ``work``; returns the `data/state` under it."""
    from huggingface_hub import snapshot_download

    names = (DELTAS, AGGREGATE, BOARD_COUNTS, EPOCHS)
    snapshot_download(
        REPO,
        repo_type="dataset",
        local_dir=work,
        allow_patterns=[
            f"{STATE}/{DELTAS}/*",
            *(f"{STATE}/{name}" for name in names[1:]),
        ],
    )
    source = work / STATE
    # snapshot_download returns quietly when the Hub is unreachable, so ask what arrived.
    missing = [name for name in names if not (source / name).exists()]
    if missing:
        raise RuntimeError(f"not fetched from {REPO}: {', '.join(missing)}")
    return source


def publish(out: Path) -> str:
    """Every file under ``out``, under `data/state/`, in one commit: the rewritten ticks and the
    archive land together or not at all."""
    from huggingface_hub import CommitOperationAdd, HfApi

    operations = [
        CommitOperationAdd(f"{STATE}/{path.relative_to(out).as_posix()}", str(path))
        for path in sorted(out.rglob("*.parquet"))
    ]
    commit = HfApi().create_commit(
        REPO,
        operations,
        repo_type="dataset",
        commit_message="Rewrite Trends as one Board-delta history (ADR-0230)",
    )
    return f"{len(operations)} file(s) in {commit.oid}"


def _report(migration: Migration, check: Verification, out: Path) -> None:
    size = (out / ARCHIVE).stat().st_size
    print(
        f"ticks       {migration.ticks} file(s), {migration.rows_before:,} rows -> "
        f"{migration.rows_after:,}\n"
        f"re-bases    {len(migration.rebased)} rewritten as deltas: "
        f"{', '.join(migration.rebased) or 'none'}\n"
        f"archive     {migration.archive_ticks} tick(s), {migration.archive_rows:,} rows, "
        f"{size / 1e6:.2f} MB -> {ARCHIVE}\n"
        f"board counts @ {check.board_counts_as_of or '(no as_of)'}: "
        f"{check.board_count_keys_differing} of {check.board_count_keys:,} keys differ\n"
        f"aggregate   {check.aggregate_ticks_compared} of {check.aggregate_ticks} tick(s) "
        f"compared, {len(check.aggregate_ticks_differing)} differ "
        f"({check.aggregate_keys_differing} key(s))\n"
        f"counting changes {len(check.counting_changes)} over {check.history_ticks} tick(s); "
        f"epoch boundaries {len(check.epoch_boundaries)}; "
        f"{'identical' if check.counting_changes == check.epoch_boundaries else 'DIFFERENT'}",
        flush=True,
    )
    if check.counting_changes != check.epoch_boundaries:
        print(f"  counting changes: {check.counting_changes}", flush=True)
        print(f"  epoch boundaries: {check.epoch_boundaries}", flush=True)
    if check.aggregate_ticks_differing:
        print(
            f"  first ticks differing: {check.aggregate_ticks_differing[:5]}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="rewrite on HF (default: dry run)"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="dry-run this local data/state instead of fetching",
    )
    parser.add_argument(
        "--work-dir", type=Path, help="default: a new temporary directory"
    )
    args = parser.parse_args()
    if args.apply and args.state_dir:
        parser.error(
            "--apply migrates the copy it fetches under state_guard, not --state-dir"
        )
    from headstart.ingest import state_guard

    log.setup()
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="trends-migration-"))
    out = work / "migrated"
    if out.exists():
        parser.error(f"{out} exists; pass a fresh --work-dir")
    token = os.environ.get("HF_TOKEN")
    guard = work / "state_guard.json"

    if args.apply:
        refusal = chain_refusal()
        if refusal:
            print(f"REFUSING: {refusal}", file=sys.stderr, flush=True)
            return 1
        # Before the fetch, so a write between the two is caught (ADR-0129).
        state_guard.record(guard, REPO, STATE, token)

    source = args.state_dir or fetch(work / "fetched")
    print(f"migrating {source} -> {out}", flush=True)
    migration = migrate(source, out)
    check = verify(out, source)
    _report(migration, check, out)
    if not check.clean:
        print(
            "REFUSING: the rewritten history does not reproduce what it replaces",
            flush=True,
        )
        return 1
    if not args.apply:
        print(
            f"\ndry run: nothing written to {REPO}; pass --apply to publish", flush=True
        )
        return 0
    if state_guard.verify(guard, REPO, STATE, token):
        print(f"REFUSING: {STATE} changed on {REPO} since it was fetched", flush=True)
        return 1
    print(f"published {publish(out)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
