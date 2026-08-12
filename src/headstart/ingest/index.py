#!/usr/bin/env python3
"""Maintain the production ``jobs`` LanceDB table — the merge stage's three table steps.

Stage 5 runs these back-to-back against the same table, so they live in one module (ADR-0028)::

    python -m headstart.ingest.index sync              # ADR-0014, ADR-0019
    python -m headstart.ingest.index prune [--apply]   # ADR-0023
    python -m headstart.ingest.index compact           # ADR-0020, ADR-0023

**sync** reconciles the table against the embedding store incrementally: fresh ids are the corpus
ids that have a vector, and the scraped-Board set is taken from the *full* scrape (``data/jobs/``),
not the tech subset — so a Board that was scraped but dropped to zero *tech* jobs still has its
closed postings evicted (a Board only in the tech snapshot would leave those rows stranded). A
posting that vanished from a scraped Board is evicted; Boards absent from the scrape are never
touched (partial-harvest safety). On the first run the table is created empty and the plan is
all-add; the identical path does true incremental add/evict on every later run — no
overwrite-rebuild (ADR-0019). Corpus ids without a vector (non-English, or not yet embedded) are
reported and skipped — run ``embed_run --resume`` first to close that gap. Each row added is
stamped ``first_seen`` with the run's time, which is when *we* indexed it rather than the company's
``posted_at``; sync adds the column to a table that predates it before writing (ADR-0031).

**prune** sweeps what the board-scoped sync structurally cannot reach:

  1. **Rows on Boards no longer live.** A Board that left the scrape list (went dead, dropped from
     the liveness ledger, or belongs to a disabled ATS) is never re-scraped, so sync never
     revisits its rows. They linger forever.
  2. **Case-variant duplicate rows.** The same job indexed under more than one slug casing — Workday
     sites like ``.../External`` vs ``.../external`` produce ``company/External`` and ``company/external``
     Board keys, hence two ids for one job. Same lowercased Board + native id → keep one, drop the rest.

  Planning lives in :mod:`headstart.ingest.index_plan`; this is the CLI that runs it against the table.
  The keep-set is the live ledger (enabled ATSes) mapped into the index's ``board_of`` key space via
  each scraper's ``board_key()``. Dry-run by default; ``--apply`` deletes. Run after ``sync``.
  Refuses to apply if the keep-set looks too small to trust (a broken ledger must not evict the
  index) — that abort exits 1.

**compact** rebuilds the store fresh to reclaim on-disk size. ``sync``/``prune`` add and delete rows,
and each cycle downloads the whole ``data/lancedb`` from the dataset. ``table.optimize()`` merges
fragments and drops old *versions*, but it does **not** delete fragment files that aren't part of the
local version history — and a week of additive uploads (pre-ADR-0023 ``--delete``) left thousands of
such untracked orphans, so the download-then-optimize path plateaued at ~14 GB. Rewriting each table
into a fresh directory keeps only the live fragments (measured: 1.9 GB → 0.23 GB), and the
``--delete`` upload then prunes the remote to match. Cheap relative to the download: a couple of
hundred MB rewritten in seconds.

Exit: 0 clean/dry-run, 1 on a safety abort.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from headstart import log
from headstart.corpus import board_of, iter_jobs
from headstart.ingest import REPO_ROOT, observability
from headstart.ingest.index_plan import (
    apply_sync,
    live_keep_set,
    plan_prune,
    plan_sync,
)
from headstart.search import PROD_TABLE

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_SOURCE = REPO_ROOT / "data" / "jobs" / "tech"
_SCRAPED = (
    REPO_ROOT / "data" / "jobs"
)  # full (pre-tech-filter) scrape — the true scraped-Board set
_DB = REPO_ROOT / "data" / "lancedb"
_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"

_ADD_CHUNK = 2048  # rows per add batch — bounds peak memory and streams progress
_MIN_KEEP_BOARDS = (
    1000  # a healthy ledger has ~40k live Boards; refuse to prune below this
)
# When *we* first indexed the Job, as an ISO-8601 UTC string — not `posted_at`, which is the
# company's posting date and says nothing about when we found it (ADR-0031). Held as a module
# constant because both `_schema` (new tables) and `sync`'s migration (the live table) need it.
_FIRST_SEEN_FIELD = pa.field("first_seen", pa.string())


# One row per Job: canonical typed metadata (ADR-0007) + inline experience numbers (ADR-0019) +
# the vector. min_years/max_years are nullable ints — null for the Jobs no number was found for.
def _schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("ats", pa.string()),
            pa.field("company", pa.string()),
            pa.field("title", pa.string()),
            pa.field("location", pa.string()),
            pa.field("remote", pa.bool_()),
            pa.field("employment_type", pa.string()),
            pa.field("experience", pa.string()),  # raw string for display ("5+")
            pa.field("min_years", pa.int32()),  # parsed, filterable
            pa.field("max_years", pa.int32()),
            pa.field(
                "experience_source", pa.string()
            ),  # "field" | "regex" | "seniority" | null
            pa.field("salary", pa.string()),  # raw string for display (ADR-0019)
            pa.field("department", pa.string()),
            pa.field("url", pa.string()),
            pa.field("posted_at", pa.string()),
            _FIRST_SEEN_FIELD,
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ]
    )


def _load_store() -> tuple[list[dict], np.ndarray]:
    """The committed embedding store: row-aligned metadata + vectors, or a hard error telling
    the user how to repair it (``embed_run --resume`` reconciles and re-commits)."""
    manifest_path = _STORE / "manifest.json"
    if not manifest_path.exists():
        log.fail(
            _log,
            f"no committed store at {_STORE} (manifest.json missing) — "
            "run python -m headstart.ingest.embed_run first (--resume finishes an interrupted run)",
        )
    dim = json.loads(manifest_path.read_text())["dim"]
    metas = [
        json.loads(line) for line in (_STORE / "meta.jsonl").open(encoding="utf-8")
    ]
    vectors = np.fromfile(_STORE / "embeddings.f32", dtype="float32")
    if vectors.size != len(metas) * dim:
        log.fail(
            _log,
            f"store is inconsistent ({vectors.size} floats for {len(metas)} metadata rows, dim {dim}) — "
            "run python -m headstart.ingest.embed_run --resume to reconcile it",
        )
    return metas, vectors.reshape(-1, dim)


def _all_ids(table: Any) -> list[str]:
    """Every id in the table. ``limit`` defaults to 10 in LanceDB, so it must be set explicitly —
    and to at least 1, since ``limit(0)`` is rejected on an empty table."""
    return [
        r["id"]
        for r in table.search()
        .select(["id"])
        .limit(max(table.count_rows(), 1))
        .to_list()
    ]


def _scraped_boards(scraped: str | Path, corpus_ids: set[str]) -> set[str]:
    """The Boards this run actually scraped, in ``board_of`` key space, read from the *full* scrape
    (``data/jobs/{ats}.jsonl`` — a non-recursive glob, so the ``tech/`` subdir is not double-counted).

    This is the eviction scope: a Board here but absent from the tech corpus was scraped and simply
    has no tech jobs now, so its stale tech rows are correctly evicted. Falls back to the corpus ids'
    Boards when the scrape dir has no ``.jsonl`` (a Wellfound-CSV or unit-test sync), keeping those
    paths working. (A Board scraped that yields *zero* jobs of any kind writes no ids and so isn't
    covered here — that rarer case is handled by the dead/absent-Board prune, ADR-0023.)"""
    path = Path(scraped)
    if path.is_dir() and any(path.glob("*.jsonl")):
        return {board_of(job["id"]) for job in iter_jobs(path)}
    return {board_of(job_id) for job_id in corpus_ids}


_IDS_PER_LINE = 100  # batched id logging: skimmable lines, any single id still greps


def _log_ids(label: str, ids: list[str]) -> None:
    """Every id behind a table change, ~100 per line — the merge log is the only record of
    *which* Jobs came and went (counts alone forced the churn investigation to reconstruct
    this statistically), and batching keeps 4k ids to ~40 lines."""
    for start in range(0, len(ids), _IDS_PER_LINE):
        chunk = ids[start : start + _IDS_PER_LINE]
        _log.info(
            f"{label} [{start + 1}-{start + len(chunk)} of {len(ids)}]: "
            + " ".join(chunk)
        )


def sync(args: argparse.Namespace) -> int:
    metas, vectors = _load_store()
    row_of = {meta["id"]: i for i, meta in enumerate(metas)}
    dim = vectors.shape[1]
    _log.info(f"store: {len(metas)} embedded Jobs (dim {dim})")

    corpus_ids = {job["id"] for job in iter_jobs(args.source)}
    boards = _scraped_boards(args.scraped, corpus_ids)
    fresh = corpus_ids & row_of.keys()
    unembedded = len(corpus_ids) - len(fresh)
    _log.info(
        f"corpus: {len(corpus_ids)} Jobs on {len(boards)} Boards; {len(fresh)} have vectors"
        + (
            f" — {unembedded} without (non-English, or run embed_run --resume)"
            if unembedded
            else ""
        )
    )

    db = lancedb.connect(args.db)
    if PROD_TABLE in db.list_tables().tables:
        table = db.open_table(PROD_TABLE)
        index_ids = _all_ids(table)
    else:
        table = db.create_table(PROD_TABLE, schema=_schema(dim))
        index_ids = []
    _log.info(f"index: {len(index_ids)} rows in table '{PROD_TABLE}'")

    # `_schema()` only reaches tables this call creates, so a table built before `first_seen`
    # existed keeps its frozen schema — and `apply_sync` requires rows to match it exactly. Add the
    # column before writing any row that carries it. Idempotent, so it runs once and then no-ops
    # (ADR-0031); existing rows get null, which is honest — we don't know when we first saw them.
    if _FIRST_SEEN_FIELD.name not in table.schema.names:
        _log.info(f"adding '{_FIRST_SEEN_FIELD.name}' to the existing table")
        table.add_columns(_FIRST_SEEN_FIELD)

    plan = plan_sync(index_ids, fresh, boards)
    _log.info(f"plan: add {len(plan.add)}, evict {len(plan.delete)}")
    _log_ids("evict", sorted(plan.delete))

    apply_sync(table, [], plan.delete)  # evictions first (chunked internally)
    # One stamp for the whole run: every Job added here arrived in the same scrape, and
    # `sync` is the only place rows are ever added, so each row is stamped exactly once. A Job that
    # is evicted and later reappears is stamped afresh — it is newly visible again (ADR-0031).
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    add_ids = sorted(plan.add)
    _log_ids("add", add_ids)
    for start in range(0, len(add_ids), _ADD_CHUNK):
        chunk = add_ids[start : start + _ADD_CHUNK]
        rows = []
        for job_id in chunk:
            row = dict(metas[row_of[job_id]])
            row["vector"] = vectors[row_of[job_id]].tolist()
            row[_FIRST_SEEN_FIELD.name] = stamp
            rows.append(row)
        apply_sync(table, rows, ())
        _log.info(f"added {min(start + _ADD_CHUNK, len(add_ids))}/{len(add_ids)}")

    final = table.count_rows()
    _log.info(f"done: table '{PROD_TABLE}' now holds {final} rows at {args.db}")
    observability.summary(
        "Index sync",
        [
            f"- added **{len(plan.add):,}**, evicted **{len(plan.delete):,}**",
            f"- served table now holds **{final:,}** rows",
        ],
    )
    return 0


def prune(args: argparse.Namespace) -> int:
    keep = live_keep_set(args.ledger)
    _log.info(f"keep-set: {len(keep)} live Boards (enabled ATSes)")
    if len(keep) < _MIN_KEEP_BOARDS:
        _log.error(
            f"ABORT: keep-set has only {len(keep)} Boards (< {_MIN_KEEP_BOARDS}) — the ledger "
            "looks broken/empty; refusing to prune so a bad ledger can't evict the index."
        )
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    index_ids = _all_ids(table)
    off_board, duplicate = plan_prune(index_ids, keep)
    evict = off_board + duplicate
    _log.info(
        f"index: {len(index_ids)} rows | evict {len(evict)} "
        f"({len(off_board)} off-Board + {len(duplicate)} duplicate) -> {len(index_ids) - len(evict)} remain"
    )

    # Which ATSes are losing rows, on every run rather than only a dry run. An eviction deletes
    # rows a user could be looking at, and "evict 4,312" alone never said whose they were.
    for label, ids in (("off-Board", off_board), ("duplicate", duplicate)):
        if not ids:
            continue
        by_ats = Counter(jid.split(":", 1)[0] for jid in ids)
        ranked = ", ".join(f"{ats} {n}" for ats, n in by_ats.most_common(5))
        extra = f", +{len(by_ats) - 5} more" if len(by_ats) > 5 else ""
        _log.info(
            f"evict {label}: {len(ids)} rows across {len(by_ats)} ATSes ({ranked}{extra})"
        )

    if not args.apply:
        for label, ids in (("off-Board", off_board), ("duplicate", duplicate)):
            for jid in ids[:8]:
                _log.info(f"  [{label}] {jid}")
        _log.info("dry-run — pass --apply to delete")
        return 0

    _log_ids("prune off-Board", off_board)
    _log_ids("prune duplicate", duplicate)
    apply_sync(table, [], evict)
    final = table.count_rows()
    _log.info(f"done: pruned {len(evict)} rows; table '{PROD_TABLE}' now holds {final}")
    observability.summary(
        "Index prune",
        [
            f"- evicted **{len(evict):,}** ({len(off_board):,} off-Board, "
            f"{len(duplicate):,} duplicate)",
            f"- served table now holds **{final:,}** rows",
        ],
    )
    return 0


def compact(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    db = lancedb.connect(db_path)
    names = list(db.list_tables().tables)
    if not names:
        _log.info("no tables to compact")
        return 0

    rebuilt = db_path.with_name(db_path.name + ".rebuild")
    shutil.rmtree(rebuilt, ignore_errors=True)
    fresh = lancedb.connect(rebuilt)
    for name in names:
        rows = db.open_table(name).to_arrow()  # only the live version's rows
        fresh.create_table(name, rows)
        _log.info(f"rebuilt '{name}': {fresh.open_table(name).count_rows()} rows")

    # Swap the rebuilt store in for the bloated one (orphan fragments dropped with the old dir).
    shutil.rmtree(db_path)
    rebuilt.rename(db_path)
    _log.info(f"compacted: rebuilt {len(names)} table(s) fresh at {db_path}")
    return 0


def main() -> int:
    log.setup()
    observability.context("index")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="step", required=True)

    # --db is declared per subcommand rather than on `ap`: argparse only accepts a top-level
    # option *before* the subcommand name, so hoisting it would reject
    # `index prune --apply --db X` — the argument order prune_index.py accepted.
    def _add_db(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--db", default=str(_DB), help="lancedb dir (default: data/lancedb)"
        )

    p_sync = sub.add_parser("sync", help="reconcile the table against the store")
    _add_db(p_sync)
    p_sync.add_argument(
        "--source",
        default=str(_SOURCE),
        help="corpus snapshot: a {ats}.jsonl directory or a Wellfound CSV (default: data/jobs/tech)",
    )
    p_sync.add_argument(
        "--scraped",
        default=str(_SCRAPED),
        help="full-scrape {ats}.jsonl dir defining the scraped-Board eviction scope "
        "(default: data/jobs); falls back to --source's Boards when it has no .jsonl files",
    )
    p_sync.set_defaults(fn=sync)

    p_prune = sub.add_parser("prune", help="dead-Board sweep + case-variant dedup")
    _add_db(p_prune)
    p_prune.add_argument(
        "--apply", action="store_true", help="delete (default: dry-run report only)"
    )
    p_prune.add_argument(
        "--ledger",
        default=str(_LEDGER),
        help="liveness ledger dir (default: data/validate/liveness)",
    )
    p_prune.set_defaults(fn=prune)

    p_compact = sub.add_parser(
        "compact", help="rebuild the table fresh to reclaim size"
    )
    _add_db(p_compact)
    p_compact.set_defaults(fn=compact)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
