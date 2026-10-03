"""Replay the Job facts into Job versions, the first step of a Restatement (ADR-0330).

A **Job version** is one stretch of a Job's listing with the same raw fields. It runs from the run
whose facts listed or changed the Job (``valid_from``) to the run whose facts next changed it,
unlisted it or took it off-Board (``valid_to``, None while it is still listed), and records how it
ended (``ended_as``). Two things follow without any snapshot:

* the listed set at a run is the versions open at it;
* a Board's change at a run is the versions starting there less the versions ending there, which is
  the shape the Trends history keeps (ADR-0230).

Rules that read the whole listed set at once (the grace period, Dormant Boards, dedup) apply to the
versions afterwards, not here: this module only replays what the scrape saw.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from pathlib import Path

from headstart.boards.board_identity import lower_key
from headstart.ingest import job_facts

#: The columns a version carries beyond the fact's own: when it began and ended, and how.
VERSION_COLUMNS = ("valid_from", "valid_to", "ended_as")


def _stamped(directory: Path):
    """Every fact file under ``directory``, oldest first, each with a ``run`` column holding the
    stamp its metadata names."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    tables = []
    for path in directory.glob("*.parquet"):
        table = pq.read_table(path)
        stamp = table.schema.metadata[b"stamp"].decode()
        tables.append(
            table.replace_schema_metadata(None).append_column(
                "run", pa.array([stamp] * table.num_rows, pa.string())
            )
        )
    return sorted(tables, key=lambda t: t["run"][0].as_py() if t.num_rows else "")


def runs(facts_dir: Path) -> list[str]:
    """Every run that recorded facts, oldest first: the Restatement's ticks. A run with no change
    still wrote its Board reads, so the Board reads name the runs."""
    import pyarrow.parquet as pq

    return sorted(
        pq.read_schema(path).metadata[b"stamp"].decode()
        for path in (facts_dir / job_facts.BOARD_READS).glob("*.parquet")
    )


def board_reads(facts_dir: Path):
    """Every run's Board reads in one table, with the run each belongs to."""
    import pyarrow as pa

    tables = [t for t in _stamped(facts_dir / job_facts.BOARD_READS) if t.num_rows]
    if not tables:
        return None
    return pa.concat_tables(tables).sort_by([("run", "ascending")])


def job_versions(facts_dir: Path):
    """Every Job version the facts under ``facts_dir`` describe, sorted by id then
    ``valid_from``: each ``listed`` or ``changed`` fact's raw fields, with the run it was recorded
    in as ``valid_from`` and the run and kind of the same Job's next fact as ``valid_to`` and
    ``ended_as``. None when there are no facts."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    tables = [t for t in _stamped(facts_dir / job_facts.JOB_FACTS) if t.num_rows]
    if not tables:
        return None
    facts = pa.concat_tables(tables).sort_by(
        [("id", "ascending"), ("run", "ascending")]
    )

    ids = facts["id"].to_numpy(zero_copy_only=False)
    run = facts["run"].to_numpy(zero_copy_only=False)
    kind = facts["kind"].to_numpy(zero_copy_only=False)
    # The same Job's next fact, when there is one: it ends the version this fact began.
    followed = np.zeros(len(ids), dtype=bool)
    followed[:-1] = ids[1:] == ids[:-1]
    next_run = np.full(len(ids), None, dtype=object)
    next_kind = np.full(len(ids), None, dtype=object)
    next_run[:-1] = np.where(followed[:-1], run[1:], None)
    next_kind[:-1] = np.where(followed[:-1], kind[1:], None)

    opens = pc.is_in(facts["kind"], value_set=pa.array(["listed", "changed"]))
    versions = facts.append_column("valid_to", pa.array(next_run, pa.string()))
    versions = versions.append_column("ended_as", pa.array(next_kind, pa.string()))
    versions = versions.filter(opens).rename_columns(
        ["valid_from" if name == "run" else name for name in versions.column_names]
    )
    return versions.drop_columns(["kind"])


def open_at(versions, run: str):
    """The versions open at ``run``: the listed set as that run's facts left it."""
    import pyarrow.compute as pc

    begun = pc.less_equal(versions["valid_from"], run)
    # Kleene: an open version's `valid_to` is null, and plain `or_` would make the test null.
    not_ended = pc.or_kleene(
        pc.is_null(versions["valid_to"]), pc.greater(versions["valid_to"], run)
    )
    return versions.filter(pc.and_(begun, not_ended))


def first_reads(reads) -> dict[str, str]:
    """``{case-folded Board: its first run whose read did not fail}``: when its backlog first
    reached the index, so its Jobs then are Recounted, not Opened (ADR-0330)."""
    first: dict[str, str] = {}
    if reads is None:
        return first
    for board, run, outcome in zip(
        reads["board"].to_pylist(),
        reads["run"].to_pylist(),
        reads["outcome"].to_pylist(),
        strict=True,
    ):
        if board is not None and outcome != "error":
            key = lower_key(board)
            first[key] = min(first.get(key, run), run)
    return first
