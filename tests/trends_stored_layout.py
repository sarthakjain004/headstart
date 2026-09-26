"""Test fixtures' Trends state, written in the layout before ADR-0230 step 6, stored as step 6's.

The live history was migrated on 2026-09-26 and production no longer reads the older layout. Many
Trends tests still author their state compactly in it: an aggregate ledger `role_trends.parquet`,
tick files keyed by series version, and `trends_epochs.csv`. :func:`store_in_current_layout`
rewrites such a state directory in place into the layout `trend_history` reads, exactly as the
one-off migration rewrote the real history:

- a re-base becomes a delta, and every tick file carries its methodology (from the epoch row in
  force at it) and its `ts` in metadata, as `(board, metric, family, band, delta)`;
- the aggregate's ticks before the first tick file become the archive.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from headstart.trends.trend_history import ARCHIVE_COLUMNS, LEVEL_METRICS, TICK_COLUMNS

_KEY = TICK_COLUMNS[:-1]

# The files only the old layout has, under `data/state/`: the aggregate ledger, which holds the
# archive's ticks, and the epoch ledger, which holds the Methodology of ticks before #688.
AGGREGATE = "role_trends.parquet"
EPOCHS = "trends_epochs.csv"
# The old layout's series-version key: misnamed since ADR-0220, and gone with the versions.
_SERIES_KEY = b"centroid_version"
_EPOCH_COLUMNS = (
    "ts",
    "centroid_version",
    "family_map_fingerprint",
    "tech_filter_version",
    "derivations_version",
    "dedup_version",
    "family_classifier_version",
)


def is_old_layout(schema: pa.Schema) -> bool:
    return _SERIES_KEY in (schema.metadata or {})


def tick_stamp(table: pa.Table) -> str:
    """The tick a file holds: its metadata's `ts`, or (before #688) its rows' one `ts`."""
    metadata = table.schema.metadata or {}
    if b"ts" in metadata:
        return metadata[b"ts"].decode()
    stamps = pc.unique(table["ts"]).to_pylist()
    if len(stamps) != 1:
        raise ValueError(f"expected one tick stamp in a file, found {sorted(stamps)}")
    return stamps[0]


def tick_table(rows: pa.Table, ts: str, methodology: dict) -> pa.Table:
    """``rows`` as one tick's file in the step-6 layout."""
    metadata = {
        b"ts": ts.encode(),
        b"methodology": json.dumps(methodology, sort_keys=True).encode(),
    }
    return rows.select(list(TICK_COLUMNS)).replace_schema_metadata(metadata)


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
    """The epoch row a tick was counted under: the last one at or before it, else the first."""
    found = epochs[0]
    for row in epochs:
        if row["ts"] > ts:
            break
        found = row
    return found


def _methodology_at(epochs: list[dict[str, str]], ts: str) -> dict[str, int | str]:
    """The Methodology a tick without its own was counted under: its epoch row's, or none at all
    where no epoch ledger was ever written, which is how the readers before step 6 took it."""
    return methodology_of(epoch_in_force(epochs, ts)) if epochs else {}


def _epoch_rows(path: Path) -> list[dict[str, str]]:
    return read_epochs(path) if path.exists() else []


def methodology_of(row: dict[str, str]) -> dict[str, int | str]:
    """An epoch row as the methodology #688 stamps on a tick: its keys, its integers as integers.
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


def _is_level(table: pa.Table) -> pa.ChunkedArray:
    return pc.is_in(table["metric"], pa.array(LEVEL_METRICS))


def _summed(parts: list[pa.Table]) -> pa.Table:
    """Each key's level: ``parts``' level rows summed per ``(board, metric, family, band)``."""
    rows = pa.concat_tables(parts).select(list(TICK_COLUMNS))
    return rows.group_by(list(_KEY)).aggregate([("delta", "sum")])


def _change(after: pa.Table, before: pa.Table) -> pa.Table:
    """The level rows that take ``before`` to ``after``, sorted by key."""
    both = after.join(
        before, keys=list(_KEY), join_type="full outer", right_suffix="_before"
    )
    delta = pc.subtract(
        pc.fill_null(both["delta_sum"], 0), pc.fill_null(both["delta_sum_before"], 0)
    )
    rows = both.select(list(_KEY)).append_column("delta", delta)
    rows = rows.filter(pc.not_equal(rows["delta"], 0))
    return rows.sort_by([(key, "ascending") for key in _KEY])


def rewritten_ticks(
    tables: list[pa.Table], epochs: Path
) -> tuple[list[pa.Table], list[str]]:
    """Every tick file's table in the step-6 layout, oldest first, and the stamps of the re-bases
    it rewrote. A table already in that layout is kept as it is.

    The old writer counted each tick against the sum of every earlier delta **at the tick's own
    series version**, so the level at an old tick is that per-version sum, whatever order the
    versions came in. A tick whose version differs from the tick before it is a re-base, and
    becomes that level less the level before it. Raises ValueError on an old-layout tick after a
    step-6 one (the step-6 writer counts against this replay, so only a reverted writer does
    that), on an `ats` that is not the board_key's prefix, or on epoch rows that disagree on
    `centroid_version`, which the metadata drops."""
    ordered = sorted(tables, key=tick_stamp)
    epoch_rows: list[dict[str, str]] = []  # read the first time a tick needs them
    centroids: set[str] = set()
    out: list[pa.Table] = []
    levels_so_far: list[pa.Table] = []
    by_version: dict[bytes, list[pa.Table]] = {}
    rebased: list[str] = []
    unstamped = 0  # old-layout ticks no epoch ledger could give a Methodology
    previous: bytes | None = None
    for table in ordered:
        ts = tick_stamp(table)
        if not is_old_layout(table.schema):
            out.append(table)
            levels_so_far.append(table.filter(_is_level(table)))
            previous = None
            continue
        if out and previous is None:
            raise ValueError(f"{ts}: an old-layout tick after a step-6 one")
        prefix = pc.list_element(pc.split_pattern(table["board"], ":", max_splits=1), 0)
        wrong = pc.sum(pc.not_equal(prefix, table["ats"])).as_py() or 0
        if wrong:
            raise ValueError(
                f"{ts}: {wrong} row(s) carry an `ats` that is not their board_key's prefix"
            )
        metadata = table.schema.metadata or {}
        version = metadata[_SERIES_KEY]
        level = _is_level(table)
        levels = table.filter(level).select(list(TICK_COLUMNS))
        by_version.setdefault(version, []).append(levels)
        if out and previous != version:
            levels = _change(_summed(by_version[version]), _summed(levels_so_far))
            rebased.append(ts)
        levels_so_far.append(levels)
        if b"methodology" in metadata:
            methodology = json.loads(metadata[b"methodology"])
        else:
            epoch_rows = epoch_rows or _epoch_rows(epochs)
            if epoch_rows:
                centroids.add(epoch_in_force(epoch_rows, ts)["centroid_version"])
            methodology = _methodology_at(epoch_rows, ts)
            unstamped += not epoch_rows
        others = table.filter(pc.invert(level)).select(list(TICK_COLUMNS))
        out.append(tick_table(pa.concat_tables([levels, others]), ts, methodology))
        previous = version
    if len(centroids) > 1:
        raise ValueError(
            f"centroid_version moves between epoch rows ({sorted(centroids)})"
        )
    if unstamped:
        # Said aloud: without the epoch ledger these ticks carry no Methodology, so the counting
        # changes among them vanish from every line rather than fail.
        print(
            f"trend history: {unstamped} older tick(s) have no Methodology — "
            f"{epochs} is missing, so their counting changes are not marked",
            flush=True,
        )
    return out, rebased


def aggregate_ticks(path: Path, before: str | None = None):
    """The aggregate ledger's non-zero ``(metric, family, band, ats)`` groups at each tick before
    ``before`` (every tick when None), oldest first, one tick at a time. Strings stay
    dictionary-encoded until a tick is built: as one dict per row the file held 4.75 GB (§4)."""
    filters = (
        [("ts", "<", datetime.fromisoformat(before).astimezone(UTC))]
        if before
        else None
    )
    table = pq.read_table(
        path,
        columns=["ts", "metric", "family", "band", "ats", "count"],
        filters=filters,
    )
    if not table.num_rows:
        return
    stamps = table["ts"].combine_chunks().cast(pa.int64()).to_numpy()
    counts = table["count"].to_numpy()
    codes = []
    for name in ("metric", "family", "band", "ats"):
        encoded = table[name].combine_chunks().dictionary_encode()
        codes.append((encoded.indices.to_numpy(), encoded.dictionary.to_pylist()))
    order = np.argsort(stamps, kind="stable")
    ordered = stamps[order]
    starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]]).tolist()
    for start, end in zip(starts, [*starts[1:], len(ordered)], strict=True):
        rows = order[start:end]
        keys = zip(
            *(map(names.__getitem__, idx[rows].tolist()) for idx, names in codes),
            strict=True,
        )
        level: dict[tuple, int] = {}
        for key, n in zip(keys, counts[rows].tolist(), strict=True):
            level[key] = level.get(key, 0) + n
        ts = datetime.fromtimestamp(int(ordered[start]) / 1000, UTC)
        yield ts.isoformat(timespec="seconds"), {k: n for k, n in level.items() if n}


def archive_from_aggregate(
    aggregate: Path, before: str | None, epochs: Path
) -> pa.Table:
    """The aggregate's ticks before ``before`` as index-wide group deltas, the first against
    nothing. The table's metadata holds every tick's stamp, since a tick where nothing moved has
    no rows, and the methodology in force at them. Raises ValueError when those ticks span more
    than one methodology, which one metadata key cannot hold."""
    rows: list[tuple] = []
    ticks: list[str] = []
    in_force: set[str] = set()
    previous: dict[tuple, int] = {}
    epoch_rows = _epoch_rows(epochs)
    for ts, level in aggregate_ticks(aggregate, before):
        rows.extend(
            (ts, *key, level.get(key, 0) - previous.get(key, 0))
            for key in sorted(level.keys() | previous.keys())
            if level.get(key, 0) != previous.get(key, 0)
        )
        in_force.add(json.dumps(_methodology_at(epoch_rows, ts)))
        ticks.append(ts)
        previous = level
    if len(in_force) > 1:
        raise ValueError(f"the archive's ticks span {len(in_force)} methodologies")
    methodology = (
        json.loads(in_force.pop()) if in_force else _methodology_at(epoch_rows, "")
    )
    columns = list(zip(*rows, strict=True)) if rows else [()] * len(ARCHIVE_COLUMNS)
    return pa.table(
        {
            name: pa.array(column, pa.int64() if name == "delta" else pa.string())
            for name, column in zip(ARCHIVE_COLUMNS, columns, strict=True)
        },
        metadata={
            b"ticks": json.dumps(ticks).encode(),
            b"methodology": json.dumps(methodology, sort_keys=True).encode(),
        },
    )


def store_in_current_layout(state_dir: Path) -> Path:
    """Rewrite ``state_dir`` (a ``data/state`` directory) into the layout `trend_history` reads,
    removing the older layout's files. A directory already in that layout is left as it is."""
    from headstart.trends import trend_history

    deltas = state_dir / trend_history.DELTAS
    paths = sorted(deltas.glob("*.parquet")) if deltas.exists() else []
    tables = [pq.read_table(path) for path in paths]
    epochs = state_dir / EPOCHS
    if any(is_old_layout(table.schema) for table in tables):
        ticks, _ = rewritten_ticks(tables, epochs)
        for path in paths:
            path.unlink()
        for table in ticks:
            pq.write_table(table, trend_history.tick_path(deltas, tick_stamp(table)))
        tables = ticks
    aggregate = state_dir / AGGREGATE
    if aggregate.exists() and not (state_dir / trend_history.ARCHIVE).exists():
        first = min((tick_stamp(table) for table in tables), default=None)
        pq.write_table(
            archive_from_aggregate(aggregate, first, epochs),
            state_dir / trend_history.ARCHIVE,
        )
    aggregate.unlink(missing_ok=True)
    epochs.unlink(missing_ok=True)
    return state_dir
