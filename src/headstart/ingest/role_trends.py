#!/usr/bin/env python3
"""Append this run's role-group counts to the trends ledger (ADR-0040) — merge stage.

Runs after ``index sync`` and ``index prune``, so it counts the **served stock**: every row
still in the ``jobs`` table is assigned to its nearest frozen centroid, that cluster is mapped
to a curated role family (``config/role_families.json``), the row is banded by the experience
columns the table already carries, and one ``(ts, version, family, band, ats, count)`` row per
non-empty group is appended to ``data/state/role_trends.csv`` — plus one unbanded, undecomposed
``(non-tech, all, all)`` diagnostic row. Series identity is ``(version, family)``; ``version``
changes only on an explicit centroid refit (a re-base, ADR-0040). ``ats`` (ADR-0075) lets the
Trends tab filter by which ATS posted a run; a pre-ADR-0075 row carries ``ats='all'`` on
migration, the same sentinel the diagnostic row itself always uses.

It also records which family each row landed in and reports the rows that **changed** family
since the last tick (ADR-0057, :mod:`headstart.ingest.role_assignments`). Counting stock alone
cannot tell a closure apart from a reassignment, and re-embedding (ADR-0050) moves real jobs
between families — so the transitions ride their own ledger rather than distorting this one.

Degrades rather than dies: without the centroid store or the family map on disk (the fit
hasn't shipped, or the join's state artifact was lost) it logs a warning and exits 0 — trends
must never sink a run that already scraped and embedded successfully.

Run: python -m headstart.ingest.role_trends
"""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from headstart import log, roles
from headstart.ingest import REPO_ROOT, observability, role_assignments
from headstart.ingest.index_plan import boards_by_canon, live_keep_set, resolve_board

_log = log.get(__name__, __spec__)

_DB = REPO_ROOT / "data" / "lancedb"
_CENTROIDS = REPO_ROOT / "data" / "state" / "role_centroids"
_FAMILIES = REPO_ROOT / "config" / "role_families.json"  # curated, in git (ADR-0040)
_WATCHLIST = REPO_ROOT / "config" / "role_watchlist.json"  # curated, in git (ADR-0051)
_LEDGER = REPO_ROOT / "data" / "state" / "role_trends.csv"
_BOARD_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_BOARD_COUNTS = REPO_ROOT / "data" / "state" / "role_trend_board_counts.parquet"
_BOARD_DELTAS = REPO_ROOT / "data" / "state" / "role_trend_board_deltas"
# id -> family snapshot + the transitions between snapshots (see role_assignments)
_ASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_assignments.parquet"
_REASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_reassignments.csv"

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


def _board_keys(ids: list[str], ledger: Path) -> list[str]:
    """Resolve each Job to the same Board identity used by index sync and prune."""
    live = boards_by_canon(live_keep_set(ledger))
    return [resolve_board(job_id, live) for job_id in ids]


def count_groups(
    rows,
    centroids,
    families: dict[int, str | None],
    watchlist: list[roles.WatchRole],
    new_after: str,
) -> tuple[dict[tuple[str, str, str, str], int], int, dict[str, str]]:
    """Count served rows into ``(metric, family, band, ats)`` groups; non-tech counted apart.

    Returns ``(counts, non_tech, assigned)`` — the last being ``id -> family`` for every row that
    landed in a real family, which :mod:`headstart.ingest.role_assignments` diffs against the
    previous tick so a job that *changed* family is not miscounted as one that closed.

    Two metrics per group (ADR-0051): ``stock`` — every live row — and ``new`` — the subset
    whose ``first_seen`` is at or after ``new_after``. Stock answers "how big is this field";
    new answers "is it hiring this week", and the two disagree exactly where it matters (a
    large family can be barely posting). ``first_seen`` survives an ADR-0050 re-embed, so an
    upgraded vector does not read as a fresh opening; rows predating ADR-0031 carry no stamp
    and are never "new", which under-counts the first week after that ADR and nothing after.

    ``ats`` (ADR-0075) lets a Trends request narrow its scope to a chosen set of ATSes — every
    served row carries one, unlike ``first_seen`` there is no pre-existing-table case to guard.

    Watch roles (ADR-0051) are counted by title into the same structure under
    ``watch:{name}``, independent of centroid assignment — a watched title counts even when
    the embedding filed it elsewhere, because the pattern is the definition.

    Rows whose cluster maps to None are the tech filter's known creep (ADR-0017 is
    recall-biased on purpose) — kept out of the role groups, but returned as one number so the
    ledger carries a filter-health series (ADR-0040)."""
    # Keep the original narrow helper for callers that only need the all-coverage series.
    ids = rows["id"].to_pylist()
    counts, non_tech, assigned, _board_counts = count_board_groups(
        rows, centroids, families, watchlist, new_after, ids
    )
    return counts, non_tech, assigned


def count_board_groups(
    rows,
    centroids,
    families: dict[int, str | None],
    watchlist: list[roles.WatchRole],
    new_after: str,
    boards: list[str],
) -> tuple[
    dict[tuple[str, str, str, str], int],
    int,
    dict[str, str],
    dict[tuple[str, str, str, str, str], int],
]:
    """Count the normal ledger plus each Board's own groups in one assignment pass."""
    vectors = np.stack(rows["vector"].to_numpy(zero_copy_only=False))
    clusters = roles.assign(vectors, centroids)
    ids = rows["id"].to_pylist()
    min_years = rows["min_years"].to_pylist()
    titles = rows["title"].to_pylist()
    employment = rows["employment_type"].to_pylist()
    atses = rows["ats"].to_pylist()
    seen = (
        rows["first_seen"].to_pylist()
        if "first_seen" in rows.schema.names
        else [None] * len(titles)
    )
    if len(boards) != len(ids):
        raise ValueError("Board identities must align with served rows")

    counts: dict[tuple[str, str, str, str], int] = {}
    board_counts: dict[tuple[str, str, str, str, str], int] = {}
    non_tech = 0
    assigned: dict[str, str] = {}

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

    for job_id, cluster, years, title, etype, first, ats, board in zip(
        ids, clusters, min_years, titles, employment, seen, atses, boards, strict=True
    ):
        is_new = bool(first) and first >= new_after
        band = roles.band(years, title, etype)
        for role in watchlist:
            if role.matches(title):
                bump(board, roles.WATCH_PREFIX + role.name, band, ats, is_new)
        family = families[int(cluster)]
        if family is None:
            non_tech += 1
            bump(board, roles.NON_TECH, "all", "all", False)
            continue
        assigned[job_id] = family
        bump(board, family, band, ats, is_new)
    return counts, non_tech, assigned, board_counts


def _migrate_ledger(ledger: Path) -> None:
    """Rewrite an old-shaped ledger in place onto the current seven-column schema.

    Two prior shapes are recognized, each stamped with whatever the schema after it added:
    a pre-ADR-0051 row (no ``metric``) gets ``metric='stock'`` AND ``ats='all'``; a
    pre-ADR-0075 row (``metric`` but no ``ats``) gets only ``ats='all'``. ``'all'`` (ADR-0075)
    means "not decomposed by ATS" — the same sentinel the non-tech diagnostic row already
    writes for itself every run, migrated or not. Every pre-ADR-0051 row was a stock
    measurement, so that backfill is exact, not a guess.

    The file is append-only and rides the HF state round trip, so the migration happens where
    the appends do — once, idempotently, before the first seven-column write.

    Rewriting the file whole is fine because this runs **once** — the next append sees the
    seven-column header and returns immediately. It is not sized by ADR-0040's "a few dozen
    rows per run", which ADR-0052's fifteen watched roles took to several hundred; if a
    retention or rollup policy ever lands, this is the function that has to care.
    """
    with ledger.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        # A 0-byte ledger has no header at all — a run killed between `open("a")` and the
        # first write leaves exactly that. Nothing to migrate, and the append below writes
        # no header for a file that exists, so let it be rewritten from scratch.
        header = tuple(next(reader, ()))
        if not header:
            ledger.unlink()
            return
        if header == _COLUMNS:
            return
        if header == _PRE_ATS_COLUMNS:
            rows = [
                [ts, version, metric, family, band, "all", count]
                for ts, version, metric, family, band, count in reader
            ]
        elif header == _PRE_METRIC_COLUMNS:
            rows = [
                [ts, version, "stock", family, band, "all", count]
                for ts, version, family, band, count in reader
            ]
        else:
            raise ValueError(f"{ledger}: unrecognized header {header}")
    tmp = ledger.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        writer.writerows(rows)
    tmp.replace(ledger)
    _log.info(
        f"migrated {ledger} to the seven-column ADR-0075 schema ({len(rows)} rows)"
    )


def append_ledger(
    ledger: Path,
    counts: dict[tuple[str, str, str, str], int],
    non_tech: int,
    version: int,
    ts: str,
) -> int:
    """Append one row per non-empty group + the non-tech diagnostic. Header on first write.

    The diagnostic rides the same file as ``(stock, non-tech, all, all)`` — one number per run,
    unbanded and undecomposed by ATS because either split means nothing for a Data Entry Clerk
    total. The chart filters it out; its trend is the tech filter's health over time. Returns
    rows written."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if ledger.exists():
        _migrate_ledger(ledger)
    # Checked AFTER the migration, which discards a 0-byte ledger: a file that existed but
    # held no header still needs one written here.
    fresh = not ledger.exists()
    with ledger.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(_COLUMNS)
        for (metric, family, band, ats), n in sorted(counts.items()):
            writer.writerow([ts, version, metric, family, band, ats, n])
        writer.writerow([ts, version, "stock", roles.NON_TECH, "all", "all", non_tech])
        fh.flush()
    return len(counts) + 1


_BOARD_COUNT_COLUMNS = ("board", "metric", "family", "band", "ats", "count")
_BOARD_DELTA_COLUMNS = ("ts", "board", "metric", "family", "band", "ats", "delta")


def _load_board_counts(
    path: Path, version: int
) -> tuple[dict[tuple[str, ...], int], str]:
    """Load the prior Board snapshot, recovering a delta written before its snapshot."""
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
    """Apply any durable delta newer than the snapshot after an interrupted prior write."""
    import pyarrow.parquet as pq

    for path in sorted(directory.glob("*.parquet")):
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        if metadata.get(b"centroid_version") != str(version).encode():
            continue
        for row in table.to_pylist():
            if row["ts"] <= as_of:
                continue
            key = tuple(row[k] for k in _BOARD_COUNT_COLUMNS[:-1])
            value = counts.get(key, 0) + row["delta"]
            if value:
                counts[key] = value
            else:
                counts.pop(key, None)
    return counts


def _append_board_deltas(
    directory: Path,
    previous: dict[tuple[str, ...], int],
    current: dict[tuple[str, ...], int],
    version: int,
    ts: str,
) -> int:
    """Append only changed Board groups; the file is the durable event before the snapshot."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    changed = [
        (*key, current.get(key, 0) - previous.get(key, 0))
        for key in sorted(previous.keys() | current.keys())
        if current.get(key, 0) != previous.get(key, 0)
    ]
    if not changed:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ts.replace(':', '-').replace('+00:00', 'Z')}.parquet"
    if path.exists():
        raise ValueError(f"{path}: a Board delta already exists for this measurement")
    table = pa.table(
        {
            "ts": [ts] * len(changed),
            "board": [row[0] for row in changed],
            "metric": [row[1] for row in changed],
            "family": [row[2] for row in changed],
            "band": [row[3] for row in changed],
            "ats": [row[4] for row in changed],
            "delta": [row[5] for row in changed],
        },
        metadata={b"centroid_version": str(version).encode()},
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


def main() -> int:
    log.setup()
    observability.context("role_trends")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--centroids", type=Path, default=_CENTROIDS)
    ap.add_argument("--families", type=Path, default=_FAMILIES)
    ap.add_argument("--watchlist", type=Path, default=_WATCHLIST)
    ap.add_argument("--ledger", type=Path, default=_LEDGER)
    ap.add_argument("--board-ledger", type=Path, default=_BOARD_LEDGER)
    ap.add_argument("--board-counts", type=Path, default=_BOARD_COUNTS)
    ap.add_argument("--board-deltas", type=Path, default=_BOARD_DELTAS)
    ap.add_argument("--assignments", type=Path, default=_ASSIGNMENTS)
    ap.add_argument("--reassignments", type=Path, default=_REASSIGNMENTS)
    args = ap.parse_args()

    # Both inputs are checked, not just the centroids: the map ships in git while the
    # centroids ride the state artifact, so they go missing for different reasons — and the
    # step is `continue-on-error`, which would turn an unguarded FileNotFoundError into a
    # green run that silently never accrues a row.
    missing = [
        str(p)
        for p in (
            args.centroids / "manifest.json",
            args.centroids
            / "centroids.f32",  # a half-landed store must not reach roles.load
            args.families,
        )
        if not p.exists()
    ]
    if missing:
        _log.warning(
            f"skipping trends this run — missing {', '.join(missing)} (fit centroids with "
            "the cluster-roles workflow; the family map ships in git, ADR-0040)"
        )
        return 0

    import lancedb

    from headstart.search import PROD_TABLE

    try:
        centroids, manifest = roles.load(args.centroids)
        families = roles.load_families(args.families, manifest)
        watchlist = roles.load_watchlist(
            args.watchlist, {f for f in families.values() if f is not None}
        )
    except ValueError as exc:
        # An unusable taxonomy is a real defect, not a missing prerequisite — most likely a
        # refit shipped without re-curating the map, which ADR-0040 treats as routine. The
        # workflow step is `continue-on-error`, so without this it would crash into a green
        # run with no annotation at all; ERROR + exit 1 makes it visible and still non-fatal.
        _log.error(f"role taxonomy unusable, no trends this run: {exc}")
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    if not n:  # nothing to count, and np.stack has no empty case
        _log.warning(f"served table '{PROD_TABLE}' is empty — no trend rows this run")
        return 0
    # Logged before the read, not after: pulling the 768-d vector column for the whole table is
    # the slow, memory-hungry part of this step, so it should not run unnarrated.
    named = len({f for f in families.values() if f is not None})
    _log.info(
        f"assigning {n} served rows to {named} families via {manifest['k']} clusters "
        f"(centroid version {manifest['version']})"
    )
    # first_seen may be absent on a pre-ADR-0031 table; select() would raise on the missing
    # column, so ask only for what exists and let count_groups treat absence as "never new".
    # ats carries no such case — every served row has had one since before this table existed.
    columns = ["id", "vector", "min_years", "title", "employment_type", "ats"]
    if "first_seen" in table.schema.names:
        columns.append("first_seen")
    rows = table.search().select(columns).limit(n).to_arrow()

    now = datetime.now(UTC)
    ts = now.isoformat(timespec="seconds")
    new_after = (now - timedelta(days=NEW_WINDOW_DAYS)).isoformat(timespec="seconds")
    try:
        boards = _board_keys(rows["id"].to_pylist(), args.board_ledger)
        counts, non_tech, assigned, board_counts = count_board_groups(
            rows, centroids, families, watchlist, new_after, boards
        )
        previous, as_of = _load_board_counts(args.board_counts, manifest["version"])
        previous = _recover_board_counts(
            previous, as_of, args.board_deltas, manifest["version"]
        )
        changed = _append_board_deltas(
            args.board_deltas, previous, board_counts, manifest["version"], ts
        )
        _save_board_counts(args.board_counts, board_counts, manifest["version"], ts)
    except (OSError, ValueError) as exc:
        _log.error(f"comparable Trends state unusable, no trends this run: {exc}")
        return 1
    written = append_ledger(args.ledger, counts, non_tech, manifest["version"], ts)
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
        previous = role_assignments.load_previous(args.assignments, manifest["version"])
        moved = role_assignments.transitions(previous, assigned)
        # Snapshot BEFORE the ledger, deliberately. The ledger is append-only, so if the snapshot
        # write failed after appending, the next tick would diff against the stale snapshot and
        # append the same transitions again — silently inflating the series with duplicates that
        # are indistinguishable from real repeated moves. This order can instead lose one tick's
        # transitions, which under-reports once and stays truthful.
        had_snapshot = args.assignments.exists()
        role_assignments.save(args.assignments, assigned, manifest["version"])
        rows_written = role_assignments.append_ledger(
            args.reassignments, moved, manifest["version"], ts
        )
        if previous is None:
            why = (
                "discarded the previous snapshot (unreadable, or a centroid refit re-based it)"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
