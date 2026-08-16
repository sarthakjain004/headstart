"""Remember which family each served row was assigned to, and report the ones that moved.

`role_trends` re-derives every row's family from its vector on every tick. Frozen centroids make
that stable **for a given vector** — but not for a given *job*: ADR-0050's description backfill
re-embeds rows, the new vector can fall nearer a different centroid, and the job silently changes
family while keeping its `first_seen`. In the ledger that is indistinguishable from the old row
closing and a new one opening somewhere else, so a family can appear to shed jobs it never lost.

That is not hypothetical. Over 2026-08-11..16 `software-engineering` fell 68,199 -> 67,294 while
every other family rose; title-matched watch roles over the same window were flat (+0.2%), and at
one tick `hardware-embedded` gained 817 rows while the whole index gained 566 — a family cannot
outgrow the index unless rows arrived *from another family*.

So: record `id -> family` each tick, diff against the previous tick, and write the transitions to
their own ledger. A reassignment then reads as a reassignment instead of masquerading as a closure.

Deliberately a **side ledger**, not a column on the served table: this is a diagnostic about the
taxonomy, not a fact about the Job, and keeping it out of `_schema()` keeps the served contract
(README, the Space, ADR-0031's `first_seen`) untouched.

Two files under ``data/state/``:
  ``role_assignments.parquet``  the current tick's ``id -> family`` (overwritten each run)
  ``role_reassignments.csv``    append-only ``ts,version,family_from,family_to,count``

Version is the centroid version: a refit re-bases every assignment, so transitions must never be
compared across versions.
"""

from __future__ import annotations

import csv
from pathlib import Path

_COLUMNS = ("ts", "version", "family_from", "family_to", "count")


def load_previous(path: Path, version: int) -> dict[str, str] | None:
    """The previous tick's ``id -> family``, or None when there is nothing comparable.

    None (rather than an empty dict) for the first run, an unreadable file, or a different
    centroid version — all cases where "no transitions" is the honest answer and an empty diff
    would be a lie that reads as "nothing moved".
    """
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        if table.schema.metadata:
            stamped = table.schema.metadata.get(b"centroid_version")
            if stamped is not None and stamped.decode() != str(version):
                return None  # a refit re-based everything; transitions are meaningless across it
        return dict(zip(table["id"].to_pylist(), table["family"].to_pylist()))
    except Exception:  # noqa: BLE001 - a corrupt snapshot must not sink the run
        return None


def save(path: Path, assignments: dict[str, str], version: int) -> None:
    """Overwrite the snapshot with this tick's assignments, stamped with the centroid version."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    ids = list(assignments)
    table = pa.table(
        {"id": pa.array(ids, pa.string()),
         "family": pa.array([assignments[i] for i in ids], pa.string())},
        metadata={b"centroid_version": str(version).encode()},
    )  # fmt: skip
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(
        path
    )  # atomic: a killed run leaves the old snapshot, never a half-written one


def transitions(
    previous: dict[str, str] | None, current: dict[str, str]
) -> dict[tuple[str, str], int]:
    """Count ``(from, to)`` moves for ids present in **both** ticks.

    Ids only in one tick are genuine adds/evictions, not reassignments, and counting them here
    would re-introduce exactly the confusion this module exists to remove.
    """
    if previous is None:
        return {}
    moved: dict[tuple[str, str], int] = {}
    for job_id, now in current.items():
        was = previous.get(job_id)
        if was is not None and was != now:
            moved[(was, now)] = moved.get((was, now), 0) + 1
    return moved


def append_ledger(
    ledger: Path, moved: dict[tuple[str, str], int], version: int, ts: str
) -> int:
    """Append one row per non-empty transition. Header on first write. Returns rows written."""
    if not moved:
        return 0
    ledger.parent.mkdir(parents=True, exist_ok=True)
    fresh = not ledger.exists() or ledger.stat().st_size == 0
    with ledger.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(_COLUMNS)
        for (was, now), n in sorted(moved.items()):
            writer.writerow([ts, version, was, now, n])
    return len(moved)
