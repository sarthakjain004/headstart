"""Record what each scrape saw, tech or not, so Trends can be recomputed under any later rule
(ADR-0330).

A rule (the tech filter, the derivations, the classifier, the dedup rules) reads a Job's raw
fields. Until this module, those fields survived only while the Job was served, so a rule change
could never be run over the past: it could only be marked as a step. ``scrape_join`` sees every
scraped line before the tech filter, so it hands each one to :class:`ScrapedLines`, and
:func:`record` writes three things under ``data/facts/``:

* **Job facts** (``jobs/{stamp}.parquet``): a row when a Job is first listed (``listed``), when its
  raw fields change (``changed``), and when an authoritative read of its Board no longer lists it
  (``unlisted``). Only changes are written, so a quiet run writes little.
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

import hashlib
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from headstart import log
from headstart.boards.board_identity import board_key_of, lower_key
from headstart.ingest import REPO_ROOT
from headstart.ingest.index_plan import resolve_board
from headstart.ingest.observability import ShardReport

_log = log.get(__name__, __spec__)

FACTS_DIR = REPO_ROOT / "data" / "facts"
LISTED_JOBS = "listed_jobs.parquet"
JOB_FACTS = "jobs"
BOARD_READS = "board_reads"

#: The rule that decides a Job is no longer listed: an authoritative read of its Board missed it,
#: with ids matched to Boards the way ``index sync`` matches its eviction scope (ADR-0053,
#: ADR-0161, ADR-0243). It is the one decision a fact carries, so a change to it bumps this.
SCOPE_VERSION = 1

#: The raw fields every rule reads, as the scrape emitted them. A change in any of them is a
#: ``changed`` fact. The description is left out: its text lives in the description store, and a
#: listing's own text changes too often to be worth a fact of its own.
RAW_FIELDS = (
    "company",
    "title",
    "location",
    "remote",
    "department",
    "url",
    "posted_at",
    "experience",
    "employment_type",
    "salary",
    "requisition",
)

_BATCH = 100_000


def _schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("id", pa.string()),
            ("board", pa.string()),
            ("fields_hash", pa.int64()),
            *((name, pa.string()) for name in RAW_FIELDS if name != "remote"),
            ("remote", pa.bool_()),
            ("has_description", pa.bool_()),
        ]
    )


def fields_hash(record: Mapping) -> int:
    """A signed 64-bit hash of ``record``'s raw fields, stable across runs and machines."""
    joined = "\x1f".join(
        "" if record.get(name) is None else str(record.get(name)) for name in RAW_FIELDS
    )
    return int.from_bytes(
        hashlib.blake2b(joined.encode("utf-8"), digest_size=8).digest(),
        "big",
        signed=True,
    )


class ScrapedLines:
    """Every scraped line's raw fields, gathered one at a time into a scratch file, so the join's
    memory holds a batch rather than 3.6 million records. A line whose id was already seen is
    skipped, as ``corpus.iter_jobs`` skips it downstream."""

    def __init__(self, path: Path) -> None:
        import pyarrow.parquet as pq

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._writer = pq.ParquetWriter(path, _schema(), compression="zstd")
        self._seen: set[str] = set()
        self._batch: dict[str, list] = {name: [] for name in _schema().names}
        #: Lines per Board, keyed lower-cased, for each Board read's line count.
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
            value = record.get(name)
            if name == "remote":
                batch[name].append(value if isinstance(value, bool) else None)
            else:
                batch[name].append(None if value is None else str(value))
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
    outcome: str  # "authoritative" | "short" | "error"
    reason: str | None
    lines: int
    stated_total: int | None
    seconds: float | None


def board_reads(
    reports: Iterable[ShardReport], lines: Mapping[str, int]
) -> list[BoardRead]:
    """Every Board a shard read this run, keyed by the scraper's ``{ats}:{slug}``. ``lines`` counts
    each Board's scraped lines by its lower-cased Board key. A Board a shard never reached (its
    budget ran out first) is not a read, so it is not here."""
    reads: list[BoardRead] = []
    for report in reports:
        keys = [
            *report.boards_ok,
            *(k for k in report.errors if k not in report.boards_ok),
        ]
        for key in keys:
            board = board_key_of(key)
            error = report.errors.get(key)
            short = report.truncated.get(key)
            reason = error or short
            stated = report.observations.get(key, {}).get("stated_total")
            reads.append(
                BoardRead(
                    scraper_key=key,
                    board=board,
                    outcome="error" if error else "short" if short else "authoritative",
                    reason=str(reason)[:300] if reason else None,
                    lines=lines.get(lower_key(board), 0) if board else 0,
                    stated_total=stated if isinstance(stated, int) else None,
                    seconds=report.board_seconds.get(key),
                )
            )
    return reads


@dataclass(frozen=True)
class Recorded:
    listed: int
    changed: int
    unlisted: int
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


def _write(table, path: Path, stamp: str) -> None:
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


def now_stamp() -> str:
    """The stamp a run's facts carry: the moment ``scrape_join`` began the union, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def file_name(stamp: str) -> str:
    return f"{stamp.replace(':', '-')}.parquet"


def record(
    scraped: Path,
    facts_dir: Path,
    stamp: str,
    reads: list[BoardRead],
    scope: Iterable[str],
    live: Mapping[str, str],
) -> Recorded:
    """Diff this run's scraped lines against the Listed set and write the run's facts.

    ``scope`` is the Boards whose read this run was authoritative. A listed id whose Board is in it
    and which the scrape did not return is ``unlisted``; an id on any other Board keeps its place,
    since a Board this run did not read, or read short, is no evidence that the Job went. Ids are
    matched to Boards the way ``index sync`` matches them: re-resolved through ``live`` each run
    and compared case-folded, so a Board the ledger now spells differently still covers its ids.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    seen = pq.read_table(scraped)
    listed_path = facts_dir / LISTED_JOBS
    prior = (
        pq.read_table(listed_path, columns=["id", "board", "fields_hash"])
        if listed_path.exists()
        else pa.table(
            {"id": [], "board": [], "fields_hash": []},
            schema=pa.schema(
                [
                    ("id", pa.string()),
                    ("board", pa.string()),
                    ("fields_hash", pa.int64()),
                ]
            ),
        )
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
    new_ids = matched.filter(is_new)["id"]
    changed_ids = matched.filter(is_changed)["id"]

    gone = prior.join(seen.select(["id"]), keys="id", join_type="left anti")
    folded_scope = {lower_key(board) for board in scope}
    in_scope = pa.array(
        [
            lower_key(resolve_board(job_id, live)) in folded_scope
            for job_id in gone["id"].to_pylist()
        ],
        type=pa.bool_(),
    )
    unlisted = gone.filter(in_scope)
    kept = gone.filter(pc.invert(in_scope))

    def facts_of(ids, kind: str):
        rows = seen.filter(pc.is_in(seen["id"], value_set=ids))
        return rows.append_column("kind", pa.array([kind] * rows.num_rows, pa.string()))

    listed_rows = facts_of(new_ids, "listed")
    changed_rows = facts_of(changed_ids, "changed")
    unlisted_rows = pa.table(
        {
            name: (
                unlisted[name]
                if name in ("id", "board", "fields_hash")
                else pa.nulls(unlisted.num_rows, seen.schema.field(name).type)
            )
            for name in seen.schema.names
        },
        schema=seen.schema,
    ).append_column("kind", pa.array(["unlisted"] * unlisted.num_rows, pa.string()))
    facts = pa.concat_tables([listed_rows, changed_rows, unlisted_rows])
    facts = facts.select(["kind", *seen.schema.names]).sort_by("id")
    _write(facts, facts_dir / JOB_FACTS / file_name(stamp), stamp)

    read_table = pa.table(
        {
            "scraper_key": [r.scraper_key for r in reads],
            "board": [r.board for r in reads],
            "outcome": [r.outcome for r in reads],
            "reason": [r.reason for r in reads],
            "lines": pa.array([r.lines for r in reads], pa.int64()),
            "stated_total": pa.array([r.stated_total for r in reads], pa.int64()),
            "seconds": pa.array([r.seconds for r in reads], pa.float64()),
        }
    )
    _write(read_table, facts_dir / BOARD_READS / file_name(stamp), stamp)

    still = pa.concat_tables(
        [
            kept.select(["id", "board", "fields_hash"]),
            seen.select(["id", "board", "fields_hash"]),
        ]
    ).sort_by("id")
    _write(still, listed_path, stamp)
    return Recorded(
        listed=listed_rows.num_rows,
        changed=changed_rows.num_rows,
        unlisted=unlisted.num_rows,
        still_listed=still.num_rows,
        reads=len(reads),
    )
