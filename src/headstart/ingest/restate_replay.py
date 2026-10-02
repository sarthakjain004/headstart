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


def _stamped(directory: Path, *, wanted=None, columns=None):
    """Every fact file under ``directory``, oldest first, each with a ``run`` column holding the
    stamp its metadata names."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    paths = sorted(
        directory.glob("*.parquet"), key=lambda p: pq.read_schema(p).metadata[b"stamp"]
    )
    selected = None if wanted is None else pa.array(sorted(wanted), pa.string())
    for path in paths:
        file = pq.ParquetFile(path)
        stamp = file.schema_arrow.metadata[b"stamp"].decode()
        for batch in file.iter_batches(batch_size=8192, columns=columns):
            table = pa.Table.from_batches([batch]).replace_schema_metadata(None)
            if selected is not None:
                table = table.filter(pc.is_in(table["id"], value_set=selected))
            yield table.append_column(
                "run", pa.repeat(pa.scalar(stamp), table.num_rows)
            )


def eligible_ids(facts_dir: Path, is_tech) -> set[str]:
    """IDs ever admitted by today's gate. Later rejected edits and absences still replay."""
    import pyarrow.parquet as pq

    wanted = set()
    for path in sorted((facts_dir / job_facts.JOB_FACTS).glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=8192, columns=["id", "title", "department", "kind"]
        ):
            for row in batch.to_pylist():
                if row["kind"] in {"listed", "changed"} and is_tech(
                    row["title"], row["department"]
                ):
                    wanted.add(row["id"])
    return wanted


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


def job_versions(facts_dir: Path, *, wanted=None, columns=None):
    """Every Job version the facts under ``facts_dir`` describe, sorted by id then
    ``valid_from``: each ``listed`` or ``changed`` fact's raw fields, with the run it was recorded
    in as ``valid_from`` and the run and kind of the same Job's next fact as ``valid_to`` and
    ``ended_as``. None when there are no facts."""
    import pyarrow as pa
    import pyarrow.compute as pc

    tables = list(
        _stamped(facts_dir / job_facts.JOB_FACTS, wanted=wanted, columns=columns)
    )
    if not tables:
        return None
    facts = pa.concat_tables(tables).sort_by(
        [("id", "ascending"), ("run", "ascending")]
    )
    del tables

    def following(name):
        return pa.chunked_array(
            [
                *facts[name].slice(1).chunks,
                pa.nulls(min(1, len(facts)), facts[name].type),
            ]
        )

    followed = pc.equal(facts["id"], following("id"))
    next_run = pc.if_else(followed, following("run"), None)
    next_kind = pc.if_else(followed, following("kind"), None)

    opens = pc.is_in(facts["kind"], value_set=pa.array(["listed", "changed"]))
    versions = facts.append_column("valid_to", next_run)
    versions = versions.append_column("ended_as", next_kind)
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
