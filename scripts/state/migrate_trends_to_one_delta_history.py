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
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

# Never over Xet: it dies silently mid-transfer (see CLAUDE.md). Set before huggingface_hub loads.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import pyarrow.parquet as pq

from headstart import log, roles, trend_history_migration
from headstart.board_identity import ats_of
from headstart.trend_history import (
    ARCHIVE,
    ARCHIVE_COLUMNS,
    DELTAS,
    LEVEL_METRICS,
    TICK_COLUMNS,
)
from headstart.trend_history_migration import AGGREGATE, EPOCHS

REPO = "imPoseidon/headstart-index"
STATE = "data/state"
BOARD_COUNTS = "role_trend_board_counts.parquet"

# The aggregate's one undecomposed row per tick: non-tech, unbanded and across every ATS.
_NOT_SPLIT = "all"

Key = tuple[str, str, str, str]  # (board, metric, family, band)
IndexKey = tuple[str, str, str, str]  # (metric, family, band, ats)


@dataclass
class Migration:
    ticks: int
    rows_before: int
    rows_after: int
    rebased: list[str]
    archive_ticks: int
    archive_rows: int


def migrate(source: Path, out: Path) -> Migration:
    """Write ``source``'s Trends state (a `data/state` directory) to ``out`` in the step-6
    layout: every tick file under ``out/role_trend_board_deltas/``, under its old name so the
    upload replaces it, and the archive beside it."""
    paths = sorted((source / DELTAS).glob("*.parquet"))
    if not paths:
        raise ValueError(f"{source / DELTAS}: no tick files")
    tables = [pq.read_table(path) for path in paths]
    name_of = {
        trend_history_migration.tick_stamp(table): path.name
        for table, path in zip(tables, paths, strict=True)
    }
    if len(name_of) != len(paths):
        raise ValueError(f"{source / DELTAS}: two files hold one tick")
    print(f"read {len(tables)} tick file(s); rewriting re-bases", flush=True)
    ticks, rebased = trend_history_migration.rewritten_ticks(tables, source / EPOCHS)
    (out / DELTAS).mkdir(parents=True)
    for table in ticks:
        name = name_of[trend_history_migration.tick_stamp(table)]
        pq.write_table(table, out / DELTAS / name, compression="zstd")
    print(f"wrote {len(ticks)} tick file(s); building the archive", flush=True)
    archive = trend_history_migration.archive_from_aggregate(
        source / AGGREGATE,
        before=trend_history_migration.tick_stamp(ticks[0]),
        epochs=source / EPOCHS,
    )
    pq.write_table(archive, out / ARCHIVE, compression="zstd", use_dictionary=True)
    return Migration(
        ticks=len(ticks),
        rows_before=sum(table.num_rows for table in tables),
        rows_after=sum(table.num_rows for table in ticks),
        rebased=rebased,
        archive_ticks=len(json.loads(archive.schema.metadata[b"ticks"])),
        archive_rows=archive.num_rows,
    )


def written_ticks(directory: Path) -> list[tuple[str, list[tuple], dict]]:
    """Each tick file under ``directory`` as ``(ts, rows, methodology)``, oldest first. Read
    here, apart from the migration's own reading, so the check does not share what it checks.
    Raises ValueError on a file not in the step-6 layout."""
    out = []
    for path in directory.glob("*.parquet"):
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        if tuple(table.schema.names) != TICK_COLUMNS or b"methodology" not in metadata:
            raise ValueError(f"{path.name}: not in the step-6 layout")
        rows = list(
            zip(*(table[name].to_pylist() for name in TICK_COLUMNS), strict=True)
        )
        out.append(
            (metadata[b"ts"].decode(), rows, json.loads(metadata[b"methodology"]))
        )
    return sorted(out, key=lambda tick: tick[0])


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
    """The archive's ticks, oldest first, each with its rows: none where nothing moved."""
    table = pq.read_table(path)
    by_tick: dict[str, list[tuple]] = {}
    for ts, *row in zip(
        *(table[name].to_pylist() for name in ARCHIVE_COLUMNS), strict=True
    ):
        by_tick.setdefault(ts, []).append(tuple(row))
    for ts in json.loads(table.schema.metadata[b"ticks"]):
        yield ts, by_tick.get(ts, [])


def verify(migrated: Path, source: Path) -> Verification:
    """Replay the new-layout files in ``migrated`` and compare them with what ``source`` holds
    of the same facts: the Board-count snapshot, the aggregate at every tick, and the epochs."""
    result = Verification()
    ticks = written_ticks(migrated / DELTAS)
    print(f"verifying {len(ticks)} tick file(s) and the archive", flush=True)
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

    aggregate = trend_history_migration.aggregate_ticks(source / AGGREGATE)
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
    for ts, rows, methodology in ticks:
        for board, metric, family, band, delta in rows:
            if metric in LEVEL_METRICS:
                _bump(boards, (board, metric, family, band), delta)
                _bump(index, _index_key(board, metric, family, band), delta)
        compare(ts, index)
        if ts == result.board_counts_as_of:
            result.board_count_keys_differing = _differing(boards, expected_boards)
        methodologies.append((ts, methodology))
    while pending is not None:
        result.aggregate_ticks += 1
        pending = next(aggregate, None)

    result.history_ticks = len(methodologies)
    result.counting_changes = [
        ts
        for (_, before), (ts, after) in pairwise(methodologies)
        if before != after and ts <= last_aggregate_tick
    ]
    epochs = trend_history_migration.read_epochs(source / EPOCHS)
    result.epoch_boundaries = [row["ts"] for row in epochs[1:]]
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
