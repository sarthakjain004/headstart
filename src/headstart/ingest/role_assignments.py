"""Remember which family each served row was assigned to, and report the ones that moved.

`role_trends` re-derives every row's family on every tick. A job can change family while keeping
its `first_seen`: under centroids a re-embedded description moved it (ADR-0050), and since
ADR-0220, when the family is the title's verdict, a retitled posting does. In the ledger that is
indistinguishable from the old row closing and a new one opening somewhere else, so a family can
appear to shed jobs it never lost.

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
  ``role_assignments.parquet``  the current tick's ``id -> family``, with each row's Board, band
                                and ATS beside it (overwritten each run)
  ``role_reassignments.csv``    append-only ``ts,version,family_from,family_to,count``

The Board, band and ATS columns, and the ``as_of`` stamp, are what job turnover diffs
(ADR-0227, :mod:`headstart.ingest.job_turnover`). A job that left is booked under the key it had
when it was last counted, so the snapshot has to remember that key.

Version is the series version (`role_trends.series_version`): a new classifier head re-bases
every assignment, so transitions must never be compared across versions.
The snapshot still stamps it under the key ``centroid_version``, the name it had when the two were
the same number.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

_COLUMNS = ("ts", "version", "family_from", "family_to", "count")


class Placement(NamedTuple):
    """Where one served tech row was counted on a tick: its Board-delta key, less the metric."""

    board: str
    family: str
    band: str
    ats: str


def load_previous(path: Path, version: int) -> dict[str, str] | None:
    """The previous tick's ``id -> family``, or None when there is nothing comparable.

    None (rather than an empty dict) for the first run, an unreadable file, a snapshot carrying no
    version stamp, or one stamped with a different series version — all cases where "no
    transitions" is the honest answer and an empty diff would be a lie that reads as "nothing
    moved". An **unstamped** snapshot is rejected for the same reason a mismatched one is: this
    guard exists precisely for files whose provenance cannot be vouched for, and one with no
    provenance at all is the least vouchable of them.
    """
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        stamped = metadata.get(b"centroid_version")
        if stamped is None or stamped.decode() != str(version):
            return None  # a refit re-based everything; transitions are meaningless across it
        return dict(zip(table["id"].to_pylist(), table["family"].to_pylist()))
    except Exception:  # noqa: BLE001 - a corrupt snapshot must not sink the run
        return None


def save(
    path: Path, placements: dict[str, Placement], version: int, as_of: str
) -> None:
    """Overwrite the snapshot with this tick's placements, stamped with the series version and
    with the tick itself (``as_of``)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    ids = list(placements)
    table = pa.table(
        {
            "id": pa.array(ids, pa.string()),
            **{
                field: pa.array(
                    [getattr(placements[i], field) for i in ids], pa.string()
                )
                for field in Placement._fields
            },
        },
        metadata={
            b"centroid_version": str(version).encode(),
            b"as_of": as_of.encode(),
        },
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(
        path
    )  # atomic: a killed run leaves the old snapshot, never a half-written one


def load_placements(path: Path) -> tuple[dict[str, Placement], str] | None:
    """The previous tick's ``id -> Placement`` and its stamp, at **any** series version.

    Not version-guarded, unlike :func:`load_previous`. A new classifier head changes which family
    a row is in, but not whether the id was served. Returns None for a missing, unreadable or
    unstamped snapshot, or for one written before ADR-0227 added the placement columns. Turnover
    then starts on the next tick, rather than reading the whole index as opened."""
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        as_of = ((table.schema.metadata or {}).get(b"as_of") or b"").decode()
        if not as_of or not set(Placement._fields) <= set(table.schema.names):
            return None
        columns = [table[field].to_pylist() for field in Placement._fields]
        placed = {
            job_id: Placement(*values)
            for job_id, *values in zip(table["id"].to_pylist(), *columns, strict=True)
        }
        return placed, as_of
    except Exception:  # noqa: BLE001 - a corrupt snapshot must not sink the run
        return None


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
