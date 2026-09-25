#!/usr/bin/env python3
"""Append this run's role-group counts to the trends ledger (ADR-0040) — merge stage.

Runs after ``index sync`` and ``index prune``, so it counts the **served stock**: every row
still in the ``jobs`` table gets a role family from its title and its served description
``vector``, through the classifier head in ``config/role_family_classifier/`` (ADR-0220,
ADR-0224, :mod:`headstart.ingest.role_family_classifier`), and titles already encoded under that
head come from a cache kept in ``data/state``. The row is banded
by the experience columns the table already carries, and one ``(ts, version, family, band, ats,
count)`` row per non-empty group is appended to ``data/state/role_trends.parquet`` — plus one
unbanded, undecomposed ``(non-tech, all, all)`` diagnostic row. Series identity is ``(version,
family)``; ``version`` changes only on a re-base, a new classifier head (see
:func:`series_version`). ``ats`` (ADR-0075) lets the Trends tab filter by which ATS posted a
run; a pre-ADR-0075 row carries ``ats='all'`` on
migration, the same sentinel the diagnostic row itself always uses.

It also records which family each row landed in and reports the rows that **changed** family
since the last tick (ADR-0057, :mod:`headstart.ingest.role_assignments`). Counting stock alone
cannot tell a closure apart from a reassignment, and a retitled or re-described posting moves
between families — so the transitions ride their own ledger rather than distorting this one.

The same snapshot gives each tick's **turnover** (ADR-0227, :mod:`headstart.ingest.job_turnover`).
The ids that arrived since the last tick, and the ids that left, are booked as Opened, Closed or
Recounted per Board, family, band and ATS. They go into the tick's Board-delta file as rows of
their own metrics, beside the level changes, so a net change can be read with what made it.

The ledger is **Parquet, not CSV** (ADR-0120). It is append-only but the merge job re-uploads
it whole every run, so its on-disk size is a per-run upload cost: measured on the real ledger,
zstd + dictionary encoding took 172,537,804 bytes of CSV to 3,430,805 — 50.3x — against a
storage budget CLAUDE.md names as this workflow's binding constraint. A pre-ADR-0120 CSV
ledger sitting beside it is read once and folded in, so no history is lost on the cutover.

Degrades rather than dies: without the classifier head or the family list on disk it logs a
warning and exits 0, and while a new head's title cache is still warming up it fills the cache
and counts nothing — trends must never sink a run that already scraped and embedded
successfully.

Run: python -m headstart.ingest.role_trends
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from headstart import log, roles, tech_filter
from headstart.ingest import (
    EVICTION_QUEUE_PATH,
    REPO_ROOT,
    UNAUTHORITATIVE_BOARDS_PATH,
    job_turnover,
    role_assignments,
    role_family_classifier,
    run_ts,
    trends_epochs,
)
from headstart.ingest.doc_prep import DERIVATIONS_VERSION
from headstart.ingest.index_plan import (
    DEDUP_VERSION,
    boards_by_canon,
    live_keep_set,
    read_unauthoritative_boards,
    resolve_board,
)
from headstart.ingest.role_assignments import Placement

_log = log.get(__name__, __spec__)

_DB = REPO_ROOT / "data" / "lancedb"
_FAMILIES = REPO_ROOT / "config" / "role_families.json"  # curated, in git (ADR-0220)
# the trained head and its manifest, in git; a new head is a new version (ADR-0220)
_CLASSIFIER = REPO_ROOT / "config" / "role_family_classifier"
# normalised title -> family under the current head, carried between runs in the state artifact
_TITLE_CACHE = REPO_ROOT / "data" / "state" / "role_title_families.parquet"
# Encoding time one run may spend filling the cache: a few thousand new titles take well under a
# minute, and a new head's ~270k-title backlog fills over a few runs instead of timing one out.
_CLASSIFY_BUDGET_SECONDS = 720.0
# Trends counts nothing until this share of served rows has a decided title (a new head's warm-up).
_MIN_TITLE_COVERAGE = 0.99
_WATCHLIST = REPO_ROOT / "config" / "role_watchlist.json"  # curated, in git (ADR-0051)
_LEDGER = REPO_ROOT / "data" / "state" / "role_trends.parquet"
_BOARD_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_BOARD_COUNTS = REPO_ROOT / "data" / "state" / "role_trend_board_counts.parquet"
_BOARD_DELTAS = REPO_ROOT / "data" / "state" / "role_trend_board_deltas"
# id -> family snapshot + the transitions between snapshots (see role_assignments)
_ASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_assignments.parquet"
_REASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_reassignments.csv"
# methodology boundaries: a row only when the definition changed, not every tick (trends_epochs)
_EPOCHS = REPO_ROOT / "data" / "state" / "trends_epochs.csv"

_COLUMNS = ("ts", "version", "metric", "family", "band", "ats", "count")
_PRE_ATS_COLUMNS = (
    "ts",
    "version",
    "metric",
    "family",
    "band",
    "count",
)  # pre-ADR-0075, migrated on write
_PRE_METRIC_COLUMNS = (
    "ts",
    "version",
    "family",
    "band",
    "count",
)  # pre-ADR-0051, migrated on write

# The flow window (ADR-0051): a row is "new" when its `first_seen` is within this many days of
# the measurement. A rolling week, not a per-run diff — the back-to-back cadence makes per-run deltas
# pipeline noise, and "how many roles appeared this week" is the question a job hunter has.
NEW_WINDOW_DAYS = 7


# Series versions before ADR-0220 were 1 and 2 (centroid fits) and 2001 (title rules over
# centroid 2). A classifier head's series sit above all of them.
_CLASSIFIER_SERIES_BASE = 3000


def series_version(head_version: int) -> int:
    """The series identity every ledger here is stamped with (ADR-0040, ADR-0220).

    A row's family is one classifier head's verdict, so a new head starts new series. Only equality and order are ever read: the Space stitches versions into one history
    by their spans (ADR-0221), and every snapshot compares its stamp for equality. The base keeps
    every head's series above the older eras'."""
    return _CLASSIFIER_SERIES_BASE + head_version


def _columns(rows) -> tuple[list, ...]:
    """The served columns the counts read, row-aligned. ``first_seen`` is absent when the table
    predates ADR-0031; those rows are stock, never new."""
    titles = rows["title"].to_pylist()
    seen = (
        rows["first_seen"].to_pylist()
        if "first_seen" in rows.schema.names
        else [None] * len(titles)
    )
    return (
        rows["id"].to_pylist(),
        rows["min_years"].to_pylist(),
        titles,
        rows["employment_type"].to_pylist(),
        rows["ats"].to_pylist(),
        seen,
    )


def count_board_groups(
    rows,
    families: list[str | None],
    watchlist: list[roles.WatchRole],
    new_after: str,
    boards: list[str],
) -> tuple[
    dict[tuple[str, str, str, str], int],
    int,
    dict[str, Placement],
    dict[tuple[str, str, str, str, str], int],
]:
    """Count served rows into ``(metric, family, band, ats)`` groups, and each Board's
    contribution to them, in one pass; non-tech counted apart.

    ``families`` is each row's family, aligned with ``rows``; ``None`` marks a non-tech row.

    Returns ``(counts, non_tech, placed, board_counts)``. ``placed`` is where each tech row was
    counted, its family with the rest of its Board-delta key (ADR-0227):
    :mod:`headstart.ingest.role_assignments` diffs it against the previous tick, so a job that
    *changed* family is not miscounted as one that closed.

    Two metrics per group (ADR-0051): ``stock`` — every live row — and ``new`` — the subset
    whose ``first_seen`` is at or after ``new_after``. Stock answers "how big is this field";
    new answers "is it hiring this week", and the two disagree exactly where it matters (a
    large family can be barely posting). ``first_seen`` survives an ADR-0050 re-embed, so a
    re-embedded row does not read as a fresh opening; rows predating ADR-0031 carry no stamp
    and are never "new", which under-counts the first week after that ADR and nothing after.

    ``ats`` (ADR-0075) lets a Trends request narrow its scope to a chosen set of ATSes — every
    served row carries one, unlike ``first_seen`` there is no pre-existing-table case to guard.

    Watch roles (ADR-0051) are counted by title into the same structure under
    ``watch:{name}``, whatever family the row landed in — the pattern is the definition — but
    only on tech rows (ADR-0215): the chart excludes non-tech, and a watch line counting a
    grocery "Front End" clerk contradicted it. They are never a row's placement: a row "moving"
    between them is a title edit, not a reassignment.

    Non-tech rows are the tech filter's known creep (ADR-0017 is recall-biased on purpose) — kept
    out of the role groups, but returned as one number so the ledger carries a filter-health
    series (ADR-0040), and counted per Board under ``(non-tech, all)`` in ``board_counts``."""
    ids, min_years, titles, employment, atses, seen = _columns(rows)
    if len(boards) != len(ids):
        raise ValueError("Board identities must align with served rows")
    counts: dict[tuple[str, str, str, str], int] = {}
    board_counts: dict[tuple[str, str, str, str, str], int] = {}
    placed: dict[str, Placement] = {}
    non_tech = 0

    def bump(board: str, family: str, band: str, ats: str, is_new: bool) -> None:
        key = ("stock", family, band, ats)
        counts[key] = counts.get(key, 0) + 1
        board_key = (board, *key)
        board_counts[board_key] = board_counts.get(board_key, 0) + 1
        if is_new:
            key = ("new", family, band, ats)
            counts[key] = counts.get(key, 0) + 1
            board_key = (board, *key)
            board_counts[board_key] = board_counts.get(board_key, 0) + 1

    for job_id, years, title, etype, first, ats, board, family in zip(
        ids, min_years, titles, employment, seen, atses, boards, families, strict=True
    ):
        # ISO-8601 UTC on both sides, so string order is time order.
        is_new = bool(first) and first >= new_after
        if family is None:
            non_tech += 1
            key = (board, "stock", roles.NON_TECH, "all", ats)
            board_counts[key] = board_counts.get(key, 0) + 1
            continue
        band = roles.band(years, title, etype)
        for role in watchlist:
            if role.matches(title):
                bump(board, roles.WATCH_PREFIX + role.name, band, ats, is_new)
        placed[job_id] = Placement(board, family, band, ats)
        bump(board, family, band, ats, is_new)
    return counts, non_tech, placed, board_counts


def _ledger_schema():
    """The ledger's Arrow schema (ADR-0120). ``ts`` is a real instant rather than the 25-byte
    ISO string the CSV stored; the 50x saving is dictionary encoding plus zstd across every
    column, not this typing, which on its own is worth ~17 bytes per distinct stamp.

    Milliseconds, not seconds, because **Parquet has no second-resolution timestamp** — the
    format's logical types start at MILLIS, so a ``timestamp[s]`` column is silently written as
    ``timestamp[ms]`` and reads back that way. Declaring seconds here would therefore make the
    *second* append raise on ``concat_tables``: the ledger read from disk would be ``ms`` and
    this run's fresh rows ``s``. Measured, not reasoned — it failed exactly that way. Every
    stamp is a whole second regardless, so the extra resolution stores nothing and costs
    nothing."""
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("ts", pa.timestamp("ms", tz="UTC")),
            pa.field("version", pa.int64()),
            pa.field("metric", pa.string()),
            pa.field("family", pa.string()),
            pa.field("band", pa.string()),
            pa.field("ats", pa.string()),
            pa.field("count", pa.int64()),
        ]
    )


def _to_table(rows: list[tuple]):
    """``rows`` — seven-tuples in ``_COLUMNS`` order — as an Arrow table on ``_ledger_schema()``.

    Values arrive as strings from a legacy CSV and as native ints from this run's own counts,
    so each column is coerced rather than trusted. ``ts`` is parsed with ``fromisoformat``,
    which reads the exact ``+00:00`` whole-second shape the ledger has always written."""
    import pyarrow as pa

    ts, version, metric, family, band, ats, count = zip(*rows) if rows else ((),) * 7
    return pa.table(
        {
            "ts": pa.array(
                [datetime.fromisoformat(str(t)) for t in ts],
                pa.timestamp("ms", tz="UTC"),
            ),
            "version": pa.array([int(v) for v in version], pa.int64()),
            "metric": pa.array([str(m) for m in metric], pa.string()),
            "family": pa.array([str(f) for f in family], pa.string()),
            "band": pa.array([str(b) for b in band], pa.string()),
            "ats": pa.array([str(a) for a in ats], pa.string()),
            "count": pa.array([int(c) for c in count], pa.int64()),
        },
        schema=_ledger_schema(),
    )


def _legacy_rows(csv_ledger: Path) -> list[tuple]:
    """Every row of a pre-ADR-0120 CSV ledger, on the current seven-column shape.

    Three prior shapes are recognized, each stamped with whatever the schema after it added:
    a pre-ADR-0051 row (no ``metric``) gets ``metric='stock'`` AND ``ats='all'``; a
    pre-ADR-0075 row (``metric`` but no ``ats``) gets only ``ats='all'``; the seven-column
    shape is taken as it stands. ``'all'`` (ADR-0075) means "not decomposed by ATS" — the same
    sentinel the non-tech diagnostic row already writes for itself every run, migrated or not.
    Every pre-ADR-0051 row was a stock measurement, so that backfill is exact, not a guess.

    A 0-byte CSV has no header at all — a run killed between ``open("a")`` and the first write
    left exactly that. There is nothing to fold in, so it reads as empty rather than raising.
    """
    with csv_ledger.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = tuple(next(reader, ()))
        if not header:
            return []
        if header == _COLUMNS:
            return [tuple(row) for row in reader]
        if header == _PRE_ATS_COLUMNS:
            return [
                (ts, version, metric, family, band, "all", count)
                for ts, version, metric, family, band, count in reader
            ]
        if header == _PRE_METRIC_COLUMNS:
            return [
                (ts, version, "stock", family, band, "all", count)
                for ts, version, family, band, count in reader
            ]
        raise ValueError(f"{csv_ledger}: unrecognized header {header}")


def append_ledger(
    ledger: Path,
    counts: dict[tuple[str, str, str, str], int],
    non_tech: int,
    version: int,
    ts: str,
) -> int:
    """Append one row per non-empty group + the non-tech diagnostic to the Parquet ledger.

    The diagnostic rides the same file as ``(stock, non-tech, all, all)`` — one number per run,
    unbanded and undecomposed by ATS because either split means nothing for a Data Entry Clerk
    total. The chart filters it out; its trend is the tech filter's health over time. Returns
    rows written.

    Parquet has no append, so the file is read and rewritten whole (ADR-0120) — which is what
    the merge job's folder upload does to it anyway. At 3.4 MB for 2.47M rows that costs less
    than the 172 MB CSV cost to *append* to, so the rewrite is the cheap half of this change,
    not a price paid for it.

    A pre-ADR-0120 CSV sitting beside the ledger is folded in on the first Parquet write, once:
    afterwards the Parquet exists and is read in preference, so the CSV is never consulted
    again. It is deliberately NOT deleted here — the merge job uploads ``data/state`` as a
    folder *without* ``--delete``, so removing it locally would not retire the remote copy and
    would only make a re-run re-migrate. Retiring it is a one-time ``HfApi().delete_file`` once
    the first Parquet has landed (ADR-0120)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ledger.parent.mkdir(parents=True, exist_ok=True)
    fresh = [
        (ts, version, metric, family, band, ats, n)
        for (metric, family, band, ats), n in sorted(counts.items())
    ]
    fresh.append((ts, version, "stock", roles.NON_TECH, "all", "all", non_tech))

    tables = []
    if ledger.exists():
        tables.append(pq.read_table(ledger))
    else:
        legacy = ledger.with_suffix(".csv")
        if legacy.exists():
            carried = _legacy_rows(legacy)
            tables.append(_to_table(carried))
            _log.info(
                f"folded {len(carried)} rows from {legacy} into {ledger} (ADR-0120); "
                "retire the remote CSV once this run's upload lands"
            )
    tables.append(_to_table(fresh))

    tmp = ledger.with_suffix(ledger.suffix + ".tmp")
    pq.write_table(
        pa.concat_tables(tables), tmp, compression="zstd", use_dictionary=True
    )
    # atomic: a killed run leaves the previous ledger, never a half-written one
    tmp.replace(ledger)
    return len(fresh)


_BOARD_COUNT_COLUMNS = ("board", "metric", "family", "band", "ats", "count")
# The delta ledger's level metrics: a Board's `stock` and `new` counts, whose deltas sum to a level.
# A tick's file also holds rows that are not: that tick's turnover and markers (ADR-0227).
_LEVEL_METRICS = ("stock", "new")


def _load_board_counts(
    path: Path, version: int
) -> tuple[dict[tuple[str, ...], int], str]:
    import pyarrow.parquet as pq

    counts: dict[tuple[str, ...], int] = {}
    as_of = ""
    if path.exists():
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        if metadata.get(b"centroid_version") == str(version).encode():
            as_of = (metadata.get(b"as_of") or b"").decode()
            if tuple(table.schema.names) != _BOARD_COUNT_COLUMNS:
                raise ValueError(f"{path}: unexpected Board-count schema")
            for row in table.to_pylist():
                counts[tuple(row[k] for k in _BOARD_COUNT_COLUMNS[:-1])] = row["count"]
    return counts, as_of


def _recover_board_counts(
    counts: dict[tuple[str, ...], int], as_of: str, directory: Path, version: int
) -> dict[tuple[str, ...], int]:
    import pyarrow.parquet as pq

    for path in sorted(directory.glob("*.parquet")):
        table = pq.read_table(path)
        if (table.schema.metadata or {}).get(b"centroid_version") != str(
            version
        ).encode():
            continue
        for row in table.to_pylist():
            # A tick's file also carries its turnover and markers (ADR-0227), not levels.
            if row["ts"] <= as_of or row["metric"] not in _LEVEL_METRICS:
                continue
            key = tuple(row[k] for k in _BOARD_COUNT_COLUMNS[:-1])
            value = counts.get(key, 0) + row["delta"]
            if value:
                counts[key] = value
            else:
                counts.pop(key, None)
    return counts


def _delta_path(directory: Path, ts: str) -> Path:
    """The Board-delta file of the tick stamped ``ts``."""
    return directory / f"{ts.replace(':', '-').replace('+00:00', 'Z')}.parquet"


_DELTA_SCHEMA = (
    ("ts", "string"),
    ("board", "string"),
    ("metric", "string"),
    ("family", "string"),
    ("band", "string"),
    ("ats", "string"),
    ("delta", "int64"),
)


def _append_board_deltas(
    directory: Path,
    previous: dict[tuple[str, ...], int],
    current: dict[tuple[str, ...], int],
    version: int,
    ts: str,
    turnover: dict[job_turnover.Key, int],
    methodology: dict[str, int | str],
) -> int:
    """Write this tick's level changes, and its ``turnover`` (ADR-0227) as rows of their own
    metrics, to one file. A turnover row carries its tick's count, not a change in a level.

    The file is written on every tick, empty when nothing moved, so the directory holds exactly
    one file per tick (ADR-0230). Its metadata says which tick it is and how it was counted: the
    series version, the tick's stamp, and the ``methodology`` the epoch ledger compares."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    changed = [
        (*key, current.get(key, 0) - previous.get(key, 0))
        for key in sorted(previous.keys() | current.keys())
        if current.get(key, 0) != previous.get(key, 0)
    ] + [(*key, n) for key, n in sorted(turnover.items())]
    directory.mkdir(parents=True, exist_ok=True)
    path = _delta_path(directory, ts)
    if path.exists():
        raise ValueError(f"{path}: a Board delta already exists for this measurement")
    rows = [(ts, *row) for row in changed]
    table = pa.table(
        {name: [row[i] for row in rows] for i, (name, _) in enumerate(_DELTA_SCHEMA)},
        schema=pa.schema(
            [(name, pa.type_for_alias(kind)) for name, kind in _DELTA_SCHEMA],
            metadata={
                b"centroid_version": str(version).encode(),
                b"ts": ts.encode(),
                b"methodology": json.dumps(methodology, sort_keys=True).encode(),
            },
        ),
    )
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return len(changed)


def _save_board_counts(
    path: Path, counts: dict[tuple[str, ...], int], version: int, ts: str
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted((*key, count) for key, count in counts.items())
    table = pa.table(
        {name: [row[i] for row in rows] for i, name in enumerate(_BOARD_COUNT_COLUMNS)},
        metadata={b"centroid_version": str(version).encode(), b"as_of": ts.encode()},
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def _counted_boards(path: Path) -> set[str]:
    """Every Board the previous tick counted any row of, at whatever series version: a Board
    missing from it was found this tick, so its backlog is Recounted, not Opened (ADR-0227)."""
    import pyarrow.parquet as pq

    if not path.exists():
        return set()
    return set(pq.read_table(path, columns=["board"]).column("board").to_pylist())


def _turnover_this_tick(
    placed: dict[str, Placement],
    first_seen: dict[str, str | None],
    live: dict[str, str],
    *,
    snapshot: Path,
    board_counts: Path,
    eviction_queue: Path,
    unauthoritative_boards: Path,
) -> tuple[dict[job_turnover.Key, int], str | None]:
    """This tick's turnover and its Unauthoritative-Board markers, keyed like the delta ledger
    (ADR-0227), and the stamp of the snapshot it diffed. ``first_seen`` covers every served row.
    There is no turnover without a comparable snapshot: on the first tick after ADR-0227, and
    after an unreadable one."""
    turnover: dict[job_turnover.Key, int] = {}
    # One marker per Board whose scrape could not show an absence (ADR-0053): none of its
    # closures counts this tick, and the Space says so rather than let it read as all opening.
    for lowered in read_unauthoritative_boards(unauthoritative_boards):
        turnover[job_turnover.unscoped_marker(live.get(lowered, lowered))] = 1
    loaded = role_assignments.load_placements(snapshot)
    if loaded is None:
        _log.info(
            "turnover: no comparable snapshot, so opened and closed start next run (ADR-0227)"
        )
        return turnover, None
    previous, previous_as_of = loaded
    booked = job_turnover.turnover(
        previous,
        placed,
        previous_as_of=previous_as_of,
        first_seen=first_seen,
        counted_boards=_counted_boards(board_counts),
        evicted=job_turnover.queued_evictions(eviction_queue).keys(),
    )
    totals = {
        metric: sum(n for key, n in booked.items() if key[1] == metric)
        for metric in job_turnover.METRICS
    }
    _log.info(
        f"turnover since {previous_as_of}: opened {totals[job_turnover.OPENED]}, closed "
        f"{totals[job_turnover.CLOSED]}, recounted +{totals[job_turnover.RECOUNTED_IN]} "
        f"−{totals[job_turnover.RECOUNTED_OUT]}; closures not counted on {len(turnover)} "
        "Unauthoritative Board(s) (ADR-0227)"
    )
    turnover.update(booked)
    return turnover, previous_as_of


def _served_row_logits(table, ids: list[str], head) -> np.ndarray:
    """Each served row's row part of the head's logits (ADR-0224), aligned with ``ids``, one batch
    at a time so each batch's vectors shrink to one logit per family. Raises ``ValueError`` when
    ids repeat or the vectors do not cover the rows exactly: a row left unfilled would otherwise
    be decided from uninitialised memory."""
    position = {job_id: i for i, job_id in enumerate(ids)}
    if len(position) != len(ids):
        raise ValueError(f"{len(ids) - len(position)} served ids repeat")
    out = np.empty((len(ids), len(head.families)), dtype=np.float32)
    filled = np.zeros(len(ids), dtype=bool)
    for batch_ids, vectors in role_family_classifier.served_vector_batches(table):
        try:
            rows = [position[job_id] for job_id in batch_ids]
        except KeyError as exc:
            raise ValueError(f"served id {exc} has a vector but no row") from None
        out[rows] = head.row_logits(vectors)
        filled[rows] = True
    if not filled.all():
        raise ValueError(f"{int((~filled).sum())} served rows have no vector")
    return out


def main() -> int:
    log.setup()
    log.context("role_trends")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--classifier", type=Path, default=_CLASSIFIER)
    ap.add_argument("--title-cache", type=Path, default=_TITLE_CACHE)
    ap.add_argument("--families", type=Path, default=_FAMILIES)
    ap.add_argument("--watchlist", type=Path, default=_WATCHLIST)
    ap.add_argument("--ledger", type=Path, default=_LEDGER)
    ap.add_argument("--board-ledger", type=Path, default=_BOARD_LEDGER)
    ap.add_argument("--board-counts", type=Path, default=_BOARD_COUNTS)
    ap.add_argument("--board-deltas", type=Path, default=_BOARD_DELTAS)
    ap.add_argument("--assignments", type=Path, default=_ASSIGNMENTS)
    ap.add_argument("--reassignments", type=Path, default=_REASSIGNMENTS)
    ap.add_argument("--epochs", type=Path, default=_EPOCHS)
    # sync's evictions not yet booked, and this run's Unauthoritative Boards (ADR-0227)
    ap.add_argument("--eviction-queue", type=Path, default=EVICTION_QUEUE_PATH)
    ap.add_argument(
        "--unauthoritative-boards", type=Path, default=UNAUTHORITATIVE_BOARDS_PATH
    )
    args = ap.parse_args()

    # The step is `continue-on-error`, which would turn an unguarded FileNotFoundError into a
    # green run that silently never accrues a row, so every input is checked up front.
    missing = [
        str(p)
        for p in (
            args.classifier / "manifest.json",
            args.classifier / "head.npz",
            args.families,
        )
        if not p.exists()
    ]
    if missing:
        _log.warning(
            f"skipping trends this run — missing {', '.join(missing)} (the classifier head and "
            "the family list ship in git, ADR-0220)"
        )
        return 0

    import lancedb

    from headstart.embedding_conventions import MODEL as EMBED_MODEL
    from headstart.embedding_conventions import PROD_TABLE

    try:
        family_names = roles.load_families(args.families)
        head = role_family_classifier.Head(args.classifier)
        head.check_families(family_names)
        # Rewritten here, before any warm-up return, so this job's later stages and the Space read
        # the epoch file in its current shape from the first run under a new head on.
        trends_epochs.upgrade_older_header(args.epochs)
        watchlist = roles.load_watchlist(args.watchlist, set(family_names))
    except ValueError as exc:
        # An unusable taxonomy is a real defect, not a missing prerequisite. The workflow step is
        # `continue-on-error`, so without this it would crash into a green run with no annotation
        # at all; ERROR + exit 1 makes it visible and still non-fatal.
        _log.error(f"role taxonomy unusable, no trends this run: {exc}")
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    if not n:
        _log.warning(f"served table '{PROD_TABLE}' is empty — no trend rows this run")
        return 0
    # The head read the served `vector` as it was trained on: another embedder or width would feed
    # its row part numbers it never learned, and still yield a confident family.
    row_width = table.schema.field("vector").type.list_size
    if (head.row_vector_model, head.row_vector_dim) != (EMBED_MODEL, row_width):
        _log.error(
            f"role taxonomy unusable, no trends this run: the head was trained on "
            f"{head.row_vector_dim}-wide {head.row_vector_model} vectors, the served table holds "
            f"{row_width}-wide {EMBED_MODEL} ones — retrain the head (ADR-0224)"
        )
        return 1
    version = series_version(head.version)
    # The "assigning N served rows to K families" prefix is parsed by
    # scripts/runlog/fanout_merge.py (tests/test_log_contract.py pins it); what follows is free.
    _log.info(
        f"assigning {n} served rows to {len(family_names)} families by title and description "
        f"(classifier head {head.version}, series version {version})"
    )
    # first_seen may be absent on a pre-ADR-0031 table; select() would raise on the missing
    # column, so ask only for what exists and let count_board_groups treat absence as
    # "never new".
    # ats carries no such case — every served row has had one since before this table existed.
    columns = ["id", "min_years", "title", "employment_type", "ats"]
    if "first_seen" in table.schema.names:
        columns.append("first_seen")
    rows = table.search().select(columns).limit(n).to_arrow()

    titles = rows["title"].to_pylist()
    cache = role_family_classifier.load_cache(args.title_cache, head.version)
    try:
        added = role_family_classifier.fill(
            cache,
            head,
            titles,
            _CLASSIFY_BUDGET_SECONDS,
            lambda filled: role_family_classifier.save_cache(args.title_cache, filled),
        )
    except Exception as exc:  # noqa: BLE001 - a failed fill keeps what the cache already holds
        _log.error(f"title classifier failed: {type(exc).__name__}: {exc}")
        added = 0
    covered = role_family_classifier.coverage(cache, titles)
    _log.info(
        f"title cache: {added} titles classified this run; {covered:.1%} of served rows decided"
    )
    if covered < _MIN_TITLE_COVERAGE:
        # A new head starts with an empty cache. Counting now would chart every undecided title
        # as unclassified-tech and then read its decision next run as hiring, so Trends waits.
        _log.warning(
            f"classifier warming up: {covered:.1%} of served rows have a decided title, "
            f"{_MIN_TITLE_COVERAGE:.0%} needed — no trend rows this run"
        )
        return 0

    try:
        row_logits = _served_row_logits(table, rows["id"].to_pylist(), head)
    except ValueError as exc:
        _log.error(f"role families undecidable, no trends this run: {exc}")
        return 1
    decided = role_family_classifier.decide_rows(cache, head, titles, row_logits)
    families = [None if family == roles.NON_TECH else family for family in decided]

    # The run's one stamp, which `index prune` also wrote its dedup evictions under (ADR-0210).
    now = run_ts()
    # How this tick counts (ADR-0164, ADR-0230): the stamps the epoch ledger compares, carried by
    # the tick's own Board-delta file too, so a reader needs no second file to learn them.
    methodology: dict[str, int | str] = {
        "family_list_fingerprint": roles.family_list_fingerprint(args.families),
        "family_classifier_version": head.version,
        "tech_filter_version": tech_filter.TECH_FILTER_VERSION,
        "derivations_version": DERIVATIONS_VERSION,
        "dedup_version": DEDUP_VERSION,
    }
    ts = now.isoformat(timespec="seconds")
    new_after = (now - timedelta(days=NEW_WINDOW_DAYS)).isoformat(timespec="seconds")
    try:
        live = boards_by_canon(live_keep_set(args.board_ledger))
        ids, *_, first_seen = _columns(rows)
        # The Board identity index sync and prune resolve through.
        boards = [resolve_board(job_id, live) for job_id in ids]
        counts, non_tech, placed, board_counts = count_board_groups(
            rows, families, watchlist, new_after, boards
        )
        assigned = {job_id: p.family for job_id, p in placed.items()}
        # Read before the snapshot is overwritten below: the transitions diff it (ADR-0057), and
        # so does this tick's turnover (ADR-0227).
        previous_families = role_assignments.load_previous(args.assignments, version)
        had_snapshot = args.assignments.exists()
        turnover, booked_through = _turnover_this_tick(
            placed,
            dict(zip(ids, first_seen, strict=True)),
            live,
            snapshot=args.assignments,
            board_counts=args.board_counts,
            eviction_queue=args.eviction_queue,
            unauthoritative_boards=args.unauthoritative_boards,
        )
        previous, as_of = _load_board_counts(args.board_counts, version)
        previous = _recover_board_counts(previous, as_of, args.board_deltas, version)
        changed = _append_board_deltas(
            args.board_deltas,
            previous,
            board_counts,
            version,
            ts,
            turnover,
            methodology,
        )
        # The snapshot turnover diffs, and the level changes, must move together (ADR-0227):
        # the tick's file without its snapshot would book this tick's turnover again next tick,
        # and the snapshot without the file would leave next tick's stock change covering two
        # ticks while its turnover covered one. So a failed snapshot takes the file back out.
        try:
            role_assignments.save(args.assignments, placed, version, ts)
        except BaseException:
            _delta_path(args.board_deltas, ts).unlink(missing_ok=True)
            raise
        _save_board_counts(args.board_counts, board_counts, version, ts)
        # Entries the published snapshot already covers are dropped, never this run's: see
        # `job_turnover.drop_evictions_through`. After the snapshot, so a failed save keeps them.
        if booked_through is not None:
            job_turnover.drop_evictions_through(args.eviction_queue, booked_through)
    except (OSError, ValueError) as exc:
        _log.error(f"comparable Trends state unusable, no trends this run: {exc}")
        return 1
    written = append_ledger(args.ledger, counts, non_tech, version, ts)
    stock_top = sorted(
        ((k, c) for k, c in counts.items() if k[0] == "stock"), key=lambda kv: -kv[1]
    )[:5]
    fresh_total = sum(c for k, c in counts.items() if k[0] == "new")
    _log.info(
        f"appended {written} rows @ {ts} -> {args.ledger} | top: "
        # `ats` is in the key (ADR-0075), so it belongs in the label: without it two ATSes' rows
        # for one family and band render identically and read as a double-count.
        + ", ".join(
            f"{family}/{band}/{ats} {c}" for (_, family, band, ats), c in stock_top
        )
        + f" | new in {NEW_WINDOW_DAYS}d: {fresh_total}"
    )
    _log.info(
        f"non-tech: {non_tech} of {n} served rows ({100 * non_tech / n:.1f}% — the "
        "ADR-0017 filter's creep) excluded from the chart"
    )
    _log.info(f"comparable coverage: {changed:,} Board-group delta(s) @ {ts}")

    # Which rows CHANGED family since the last tick. Without this, a re-embedded job that moves
    # from one family to another is indistinguishable in the stock series from a closure plus an
    # unrelated new posting — which is how a 622-row "software-engineering decline" turned out to
    # be largely redistribution. Diagnostic only: never fails the run.
    try:
        moved = role_assignments.transitions(previous_families, assigned)
        # The snapshot was written above, BEFORE this ledger, deliberately. The ledger is
        # append-only, so if the snapshot write failed after appending, the next tick would diff
        # against the stale snapshot and append the same transitions again — silently inflating
        # the series with duplicates that are indistinguishable from real repeated moves. This
        # order can instead lose one tick's transitions, which under-reports once and stays
        # truthful.
        rows_written = role_assignments.append_ledger(
            args.reassignments, moved, version, ts
        )
        if previous_families is None:
            why = (
                "discarded the previous snapshot (unreadable, or a re-base: a new classifier "
                "head)"
                if had_snapshot
                else "first snapshot"
            )
            _log.info(
                f"assignments: {why} — wrote {len(assigned)} rows to {args.assignments}; "
                "transitions start next run"
            )
        else:
            total = sum(moved.values())
            top = sorted(moved.items(), key=lambda kv: -kv[1])[:3]
            _log.info(
                f"assignments: {total} of {len(assigned)} rows changed family "
                f"({100 * total / max(len(assigned), 1):.2f}%), {rows_written} transition rows"
                + (
                    " | top: " + ", ".join(f"{a}->{b} {c}" for (a, b), c in top)
                    if top
                    else ""
                )
            )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never sink a good run
        _log.warning(f"assignment diff skipped: {type(exc).__name__}: {exc}")

    # Which methodology moved since the last tick, if any (ADR-0164) — a new classifier head, an
    # edited family list, or a tech-filter, derivations or dedup version bump (ADR-0188) each
    # change what a count means, and only the head leaves any other mark on this ledger.
    # Diagnostic only: never fails the run.
    try:
        wrote_epoch = trends_epochs.append_if_changed(
            args.epochs,
            ts,
            centroid_version=trends_epochs.ABSENT,  # no centroid fit decides anything
            # the epoch column keeps its older name for the family-list fingerprint
            family_map_fingerprint=methodology["family_list_fingerprint"],
            family_classifier_version=methodology["family_classifier_version"],
            tech_filter_version=methodology["tech_filter_version"],
            derivations_version=methodology["derivations_version"],
            dedup_version=methodology["dedup_version"],
        )
        if wrote_epoch:
            _log.info(f"epochs: methodology boundary recorded @ {ts} -> {args.epochs}")
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never sink a good run
        _log.warning(f"epoch stamp skipped: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
