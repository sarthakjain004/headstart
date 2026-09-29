"""Record what each scrape saw, tech or not, so Trends can be recomputed under any later rule
(ADR-0330).

A rule (the tech filter, the derivations, the classifier, the dedup rules) reads a Job's raw
fields. Until this module, those fields survived only while the Job was served, so a rule change
could never be run over the past: it could only be marked as a step. ``scrape_join`` sees every
scraped line before the tech filter, so it hands each one to :class:`ScrapedLines`, and
:func:`record_run` writes three things under ``data/facts/``:

* **Job facts** (``job_facts/{stamp}.parquet``): a row when a Job is first listed (``listed``),
  when its raw fields change (``changed``), when an authoritative read of its Board no longer lists
  it (``unlisted``), and when its Board is no longer a Scrapable Board (``off_board``). Only
  changes are written, so a quiet run writes little.
* **Board reads** (``board_reads/{stamp}.parquet``): every Board the run read, and whether the read
  was authoritative.
* **The Listed set** (``listed_jobs.parquet``): every currently listed id, its Board and a hash of
  its raw fields. It is state, rewritten each run, and exists only so the next run can tell what
  changed. ``merge`` uploads it with the run's facts in one commit, so a run whose upload fails
  loses both together and the next run diffs against the older set.

Nothing reads the facts yet. They are recorded now because a day not recorded is a day no later
rule can restate.

Run inside ``scrape_join``; there is no entry point of its own.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import typing
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from headstart import log
from headstart.boards.board_identity import board_key_of, lower_key
from headstart.ingest import REPO_ROOT
from headstart.ingest.index_plan import resolve_board
from headstart.ingest.observability import ShardReport
from headstart.jobs.job import Job

_log = log.get(__name__, __spec__)

FACTS_DIR = REPO_ROOT / "data" / "facts"
LISTED_JOBS = "listed_jobs.parquet"
JOB_FACTS = "job_facts"
BOARD_READS = "board_reads"
#: This run's scraped lines, gathered while the union streams and deleted once the run's facts are
#: written. Named ``.tmp`` so the ``merge`` upload's ``--exclude "*.tmp"`` never carries it.
SCRATCH = "scraped_lines.parquet.tmp"

#: The rule that decides a Job is no longer listed, and the one decision a fact carries: an
#: authoritative read of its Board missed it, in the eviction scope ``index sync`` uses
#: (``index_plan.unauthoritative_among`` and ``resolve_board``, case-folded; ADR-0053, ADR-0161,
#: ADR-0243), or its Board left the Scrapable Boards, as ``index prune``'s off-Board sweep reads
#: it (ADR-0023). A change to either rule bumps this.
SCOPE_VERSION = 1

Kind = Literal["listed", "changed", "unlisted", "off_board"]
Outcome = Literal["authoritative", "truncated", "error"]

#: The ``Job`` fields that are not facts about the posting: its id and ATS (the id carries both),
#: when this run fetched it, and its description, whose text lives in the description store.
_NOT_RAW = frozenset({"id", "ats", "scraped_at", "description"})
_HINTS = typing.get_type_hints(Job)
#: Every other ``Job`` field, as the scrape emitted it: what the rules read. Derived, so a field
#: added to ``Job`` is recorded from the run it lands in.
RAW_FIELDS = tuple(f.name for f in dataclasses.fields(Job) if f.name not in _NOT_RAW)
_BOOL_FIELDS = frozenset(
    name for name in RAW_FIELDS if bool in typing.get_args(_HINTS[name])
)
#: The Listed set's columns: enough to diff the next run against.
_LISTED = ("id", "board", "fields_hash")

_BATCH = 100_000


def _schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("id", pa.string()),
            ("board", pa.string()),
            ("fields_hash", pa.int64()),
            *(
                (name, pa.bool_() if name in _BOOL_FIELDS else pa.string())
                for name in RAW_FIELDS
            ),
            ("has_description", pa.bool_()),
        ]
    )


def _listed_schema():
    import pyarrow as pa

    schema = _schema()
    return pa.schema([schema.field(name) for name in _LISTED])


def _raw_value(name: str, value: object) -> object:
    if name in _BOOL_FIELDS:
        return value if isinstance(value, bool) else None
    return None if value is None else str(value)


def fields_hash(record: Mapping) -> int:
    """A signed 64-bit hash of ``record``'s raw fields and whether it carried a description,
    stable across runs and machines. Only fields with a value take part, so a field added to
    ``Job`` moves the hash only of the Jobs that state it."""
    values = {name: _raw_value(name, record.get(name)) for name in RAW_FIELDS}
    values["has_description"] = bool(record.get("description"))
    joined = "\x1f".join(
        f"{name}={value}" for name, value in values.items() if value is not None
    )
    return int.from_bytes(
        hashlib.blake2b(joined.encode("utf-8"), digest_size=8).digest(),
        "big",
        signed=True,
    )


class ScrapedLines:
    """Every scraped line's raw fields, gathered one at a time into a scratch file. Memory holds
    one batch of rows and the set of ids already seen; a line whose id was seen is skipped, as
    ``corpus.iter_jobs`` skips it downstream."""

    def __init__(self, path: Path) -> None:
        import pyarrow.parquet as pq

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._writer = pq.ParquetWriter(path, _schema(), compression="zstd")
        self._seen: set[str] = set()
        self._batch: dict[str, list] = {name: [] for name in _schema().names}
        #: Lines per Board, keyed case-folded, for each Board read's line count.
        self.board_lines: Counter[str] = Counter()

    def see(self, board: str, record: Mapping) -> None:
        job_id = record["id"]
        if job_id in self._seen:
            return
        self._seen.add(job_id)
        batch = self._batch
        batch["id"].append(job_id)
        batch["board"].append(board)
        batch["fields_hash"].append(fields_hash(record))
        for name in RAW_FIELDS:
            batch[name].append(_raw_value(name, record.get(name)))
        batch["has_description"].append(bool(record.get("description")))
        self.board_lines[lower_key(board)] += 1
        if len(batch["id"]) >= _BATCH:
            self._flush()

    def _flush(self) -> None:
        import pyarrow as pa

        if self._batch["id"]:
            self._writer.write_table(pa.table(self._batch, schema=_schema()))
            self._batch = {name: [] for name in _schema().names}

    def close(self) -> Path:
        self._flush()
        self._writer.close()
        return self.path


@dataclass(frozen=True)
class BoardRead:
    """One Board this run read, as its shard reported it."""

    scraper_key: str
    board: str | None
    outcome: Outcome
    reason: str | None
    #: Whether its Jobs this read did not return are ``unlisted``: the Board resolved and is in
    #: the run's eviction scope.
    in_scope: bool
    lines: int
    stated_total: int | None
    seconds: float | None


def board_reads(
    reports: Iterable[ShardReport], lines: Mapping[str, int], scope: Collection[str]
) -> list[BoardRead]:
    """Every Board a shard read this run, keyed by the scraper's ``{ats}:{slug}``. ``lines`` counts
    each Board's scraped lines and ``scope`` holds the run's eviction scope, both case-folded. A
    Board a shard never reached (its budget ran out first) is not a read, so it is not here."""
    reads: list[BoardRead] = []
    for report in reports:
        keys = [
            *report.boards_ok,
            *(k for k in report.errors if k not in report.boards_ok),
        ]
        for key in keys:
            board = board_key_of(key)
            error = report.errors.get(key)
            truncated = report.truncated.get(key)
            reason = error or truncated
            stated = report.observations.get(key, {}).get("stated_total")
            reads.append(
                BoardRead(
                    scraper_key=key,
                    board=board,
                    outcome="error"
                    if error
                    else "truncated"
                    if truncated
                    else "authoritative",
                    reason=str(reason)[:300] if reason else None,
                    in_scope=board is not None and lower_key(board) in scope,
                    lines=lines.get(lower_key(board), 0) if board else 0,
                    stated_total=stated if isinstance(stated, int) else None,
                    seconds=report.board_seconds.get(key),
                )
            )
    return reads


@dataclass(frozen=True)
class RunFacts:
    """How many facts of each kind one run wrote, and how many Jobs are listed after it."""

    listed: int
    changed: int
    unlisted: int
    off_board: int
    still_listed: int
    reads: int


def _run_metadata(stamp: str) -> dict[bytes, bytes]:
    meta = {
        "stamp": stamp,
        "scope_version": str(SCOPE_VERSION),
        "code_sha": os.environ.get("GITHUB_SHA", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    }
    return {k.encode(): v.encode() for k, v in meta.items()}


def _write_staged(table, path: Path, stamp: str) -> None:
    """Write ``table`` beside ``path`` and rename it over, so a killed run leaves no half file."""
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".parquet.tmp")
    try:
        pq.write_table(
            table.replace_schema_metadata(_run_metadata(stamp)),
            staged,
            compression="zstd",
        )
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def facts_stamp() -> str:
    """The stamp a run's facts carry: the moment ``scrape_join`` began the union, to the second.
    Not the run's Tick stamp (``ingest.run_ts``), which ``merge`` sets later."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def file_name(stamp: str) -> str:
    return f"{stamp.replace(':', '-')}.parquet"


def record_run(
    scraped: Path,
    facts_dir: Path,
    stamp: str,
    reads: list[BoardRead],
    scope: Collection[str],
    live: Mapping[str, str],
) -> RunFacts:
    """Diff this run's scraped lines against the Listed set and write the run's facts.

    ``scope`` is the run's eviction scope, case-folded. A listed id the scrape did not return is
    ``unlisted`` when its Board is in it, and ``off_board`` when a ledger is loaded (``live``) and
    its Board is not among the Scrapable Boards; either way it leaves the Listed set. Any other id
    keeps its place, since a Board this run did not read, or read short, is no evidence that the
    Job went. Ids are re-resolved through ``live`` each run and matched case-folded, as ``index
    sync`` matches them.

    All or nothing: the Listed set is written last, and the run's fact files are removed if any
    write fails, so the next run diffs against the older set and writes these changes again.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    seen = pq.read_table(scraped)
    listed_path = facts_dir / LISTED_JOBS
    prior = (
        pq.read_table(listed_path, columns=list(_LISTED))
        if listed_path.exists()
        else _listed_schema().empty_table()
    )

    matched = seen.select(["id", "fields_hash"]).join(
        prior.select(["id", "fields_hash"]).rename_columns(["id", "prior_hash"]),
        keys="id",
        join_type="left outer",
    )
    is_new = pc.is_null(matched["prior_hash"])
    is_changed = pc.and_(
        pc.invert(is_new), pc.not_equal(matched["fields_hash"], matched["prior_hash"])
    )

    gone = prior.join(seen.select(["id"]), keys="id", join_type="left anti")
    gone_boards = [lower_key(resolve_board(i, live)) for i in gone["id"].to_pylist()]
    unlisted = gone.filter(pa.array([b in scope for b in gone_boards], pa.bool_()))
    off_board = gone.filter(
        pa.array(
            [bool(live) and b not in scope and b not in live for b in gone_boards],
            pa.bool_(),
        )
    )
    kept = gone.filter(
        pa.array(
            [b not in scope and (not live or b in live) for b in gone_boards],
            pa.bool_(),
        )
    )

    def with_kind(rows, kind: Kind):
        return rows.append_column("kind", pa.array([kind] * rows.num_rows, pa.string()))

    def of_seen(ids, kind: Kind):
        return with_kind(seen.filter(pc.is_in(seen["id"], value_set=ids)), kind)

    def of_gone(rows, kind: Kind):
        columns = {
            name: rows[name]
            if name in _LISTED
            else pa.nulls(rows.num_rows, seen.schema.field(name).type)
            for name in seen.schema.names
        }
        return with_kind(pa.table(columns, schema=seen.schema), kind)

    listed_rows = of_seen(matched.filter(is_new)["id"], "listed")
    changed_rows = of_seen(matched.filter(is_changed)["id"], "changed")
    facts = pa.concat_tables(
        [
            listed_rows,
            changed_rows,
            of_gone(unlisted, "unlisted"),
            of_gone(off_board, "off_board"),
        ]
    )
    facts = facts.select(["kind", *seen.schema.names]).sort_by("id")

    reads_table = pa.table(
        {
            "scraper_key": [r.scraper_key for r in reads],
            "board": [r.board for r in reads],
            "outcome": [r.outcome for r in reads],
            "reason": [r.reason for r in reads],
            "in_scope": pa.array([r.in_scope for r in reads], pa.bool_()),
            "lines": pa.array([r.lines for r in reads], pa.int64()),
            "stated_total": pa.array([r.stated_total for r in reads], pa.int64()),
            "seconds": pa.array([r.seconds for r in reads], pa.float64()),
        }
    )
    still = pa.concat_tables(
        [kept.select(list(_LISTED)), seen.select(list(_LISTED))]
    ).sort_by("id")

    written: list[Path] = []
    try:
        for table, path in (
            (facts, facts_dir / JOB_FACTS / file_name(stamp)),
            (reads_table, facts_dir / BOARD_READS / file_name(stamp)),
        ):
            _write_staged(table, path, stamp)
            written.append(path)
        _write_staged(still, listed_path, stamp)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return RunFacts(
        listed=listed_rows.num_rows,
        changed=changed_rows.num_rows,
        unlisted=unlisted.num_rows,
        off_board=off_board.num_rows,
        still_listed=still.num_rows,
        reads=len(reads),
    )
