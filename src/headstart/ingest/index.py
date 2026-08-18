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
``posted_at``; sync adds the column to a table that predates it before writing (ADR-0031). Sync
reads the liveness ledger too (``--ledger``), but only to name Boards the same way prune does
(ADR-0049). It needs no keep-set guard of its own: a broken ledger degrades resolution on both
sides of its scope comparison at once, which narrows nothing and widens nothing.

**prune** sweeps what the board-scoped sync structurally cannot reach:

  1. **Rows on Boards no longer live.** A Board that left the scrape list (went dead, dropped from
     the liveness ledger, or belongs to a disabled ATS) is never re-scraped, so sync never
     revisits its rows. They linger forever.
  2. **Case-variant duplicate rows.** The same job indexed under more than one slug casing — Workday
     sites like ``.../External`` vs ``.../external`` produce ``company/External`` and ``company/external``
     Board keys, hence two ids for one job. Same lowercased Board + native id → keep one, drop the rest.

  Planning lives in :mod:`headstart.ingest.index_plan`; this is the CLI that runs it against the table.
  The keep-set is the live ledger (enabled ATSes), each Board key exactly as its scraper's
  ``board_key()`` builds it; ids are matched against it by prefix (ADR-0049), not by parsing them.
  Dry-run by default; ``--apply`` deletes. Run after ``sync``.
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
from headstart.corpus import iter_jobs
from headstart.ingest.doc_prep import PLANNER_ONLY_FIELDS
from headstart.ingest import (
    PENDING_UPGRADES_PATH,
    REPO_ROOT,
    observability,
    read_id_list,
)
from headstart.ingest.index_plan import (
    COLLAPSE_RATIO,
    apply_sync,
    boards_by_canon,
    in_predicate,
    live_keep_set,
    plan_prune,
    plan_sync,
    resolve_board,
    read_unauthoritative_boards,
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
# Written by embed_plan, consumed here and by embed_merge (ADR-0050).
_UPGRADES = PENDING_UPGRADES_PATH
# Written by scrape_join from the shard reports: the Boards whose scraped list is not authoritative
# this run, which must not be evicted from just because they emitted a partial list (ADR-0053).
_UNAUTHORITATIVE = REPO_ROOT / "data" / "state" / "unauthoritative_boards.json"

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


def _scan(table: Any, columns: list[str]) -> list[dict]:
    """Every row's ``columns``, never the vector, so a whole-table read stays cheap.

    ``limit`` defaults to 10 in LanceDB, so it must be set explicitly — and to at least 1, since
    ``limit(0)`` is rejected on an empty table.
    """
    return table.search().select(columns).limit(max(table.count_rows(), 1)).to_list()


def _all_ids(table: Any) -> list[str]:
    """Every id in the table."""
    return [r["id"] for r in _scan(table, ["id"])]


def _ids_and_stamps(table: Any) -> tuple[list[str], dict[str, str]]:
    """Every id, and the ``first_seen`` stamps that order the collapse guard's drain (ADR-0055).

    One scan yielding both, not two — they are columns of the same row and the planner needs them
    together. A row predating the ``first_seen`` column carries null and is simply absent from the
    mapping, which is what sorts it first in the drain: those are the oldest rows there are.
    """
    if _FIRST_SEEN_FIELD.name not in table.schema.names:
        return _all_ids(table), {}
    rows = _scan(table, ["id", _FIRST_SEEN_FIELD.name])
    stamps = {
        r["id"]: r[_FIRST_SEEN_FIELD.name]
        for r in rows
        if r.get(_FIRST_SEEN_FIELD.name)
    }
    return [r["id"] for r in rows], stamps


def _scraped_boards(
    scraped: str | Path, corpus_ids: set[str], live: dict[str, str]
) -> set[str]:
    """The Boards this run actually scraped, read from the *full* scrape (``data/jobs/{ats}.jsonl``
    — a non-recursive glob, so the ``tech/`` subdir is not double-counted).

    ``live`` is the :func:`boards_by_canon` lookup each id resolves through, so this scope lands in
    the same key space ``plan_sync`` classifies indexed rows into (ADR-0049).

    This is the eviction scope: a Board here but absent from the tech corpus was scraped and simply
    has no tech jobs now, so its stale tech rows are correctly evicted. Falls back to the corpus ids'
    Boards when the scrape dir has no ``.jsonl`` (a Wellfound-CSV or unit-test sync), keeping those
    paths working. (A Board scraped that yields *zero* jobs of any kind writes no ids and so isn't
    covered here — that rarer case is handled by the dead/absent-Board prune, ADR-0023.)"""
    path = Path(scraped)
    if path.is_dir() and any(path.glob("*.jsonl")):
        return {resolve_board(job["id"], live) for job in iter_jobs(path)}
    return {resolve_board(job_id, live) for job_id in corpus_ids}


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


# A scrape reason is a sentence or an exception repr, not a traceback.
_REASON_CHARS = 160


def _log_reasons(label: str, reasons: dict[str, str]) -> None:
    """One Board per line with *why* it is there — unlike :func:`_log_ids`, which batches bare ids.

    A reason is the whole point of the line, so batching would bury it; one per line is what lets
    `grep 'scope-excluded' | grep 429` split a rate-limited Board from a genuinely short page.
    Reasons are flattened and clipped because they carry arbitrary scraper text — a newline would
    break the one-Board-per-line contract the grep depends on.

    Logged at **warning**, matching the header these lines explain: at info they would vanish
    from a warning-filtered log and leave exactly the bare count that made 19 runs' worth of
    exclusions undiagnosable in the first place.
    """
    for board, reason in reasons.items():
        why = " ".join(str(reason).split()) or "no reason recorded"
        if len(why) > _REASON_CHARS:
            why = why[:_REASON_CHARS] + "…"
        _log.warning(f"{label}: {board} — {why}")


def _take_upgrades(table: Any, path: Path) -> dict[str, str | None]:
    """Delete the rows for ids being re-embedded, returning the ``first_seen`` each one carried.

    ``plan_sync`` computes ``add = fresh - index``, so an upgraded Job is only re-added once its
    old row is gone — but it is not a new listing, so its stamp has to survive the round trip
    (ADR-0050). Ids absent from the table (a first run, or a Job the prune already took) simply
    return no stamp and are stamped with the run's time like any other add.
    """
    ids = read_id_list(path)
    if not ids:
        return {}
    # Safe to name the column: sync adds it to a pre-ADR-0031 table before reaching here.
    rows = (
        table.search()
        .where(in_predicate("id", ids))
        .select(["id", _FIRST_SEEN_FIELD.name])
        .to_list()
    )
    taken = {r["id"]: r.get(_FIRST_SEEN_FIELD.name) for r in rows}
    apply_sync(table, [], list(taken))
    kept = sum(1 for v in taken.values() if v)
    _log.info(f"upgrades: replacing {len(taken)} rows, {kept} keeping first_seen")
    return taken


def sync(args: argparse.Namespace) -> int:
    metas, vectors = _load_store()
    row_of = {meta["id"]: i for i, meta in enumerate(metas)}
    dim = vectors.shape[1]
    _log.info(f"store: {len(metas)} embedded Jobs (dim {dim})")

    # Resolved against the live ledger so a Board is named the same here as in prune: an id whose
    # native part carries a colon otherwise lands on a phantom Board, and a closed posting there is
    # reachable by neither planner (ADR-0049). Empty ledger degrades to board_of, the prior rule.
    live = boards_by_canon(live_keep_set(args.ledger))
    corpus_ids = {job["id"] for job in iter_jobs(args.source)}
    boards = _scraped_boards(args.scraped, corpus_ids, live)
    # A Board whose scrape came back short emitted the pages it did get, so it looks scraped and
    # its missing rows look delisted. Drop it from the scope entirely: eviction should follow a
    # Board's scrape *outcome*, not the presence of a line (ADR-0053). The collapse guard in
    # `plan_sync` stays as the backstop for a truncation that reported nothing at all.
    # `excluded`, not "held": `SyncPlan.held` already names what the collapse guard withheld, and
    # these are two different mechanisms.
    unauthoritative = read_unauthoritative_boards(args.unauthoritative_boards)
    excluded = {b for b in boards if b.lower() in unauthoritative}
    if excluded:
        boards -= excluded
        _log.warning(
            f"scrape outcome: {len(excluded)} Board(s) returned a list that is not authoritative "
            "(truncated, or the scrape raised) and are excluded from the eviction scope — their "
            "missing rows are unscraped, not closed"
        )
        _log_reasons(
            "scope-excluded Board",
            {b: unauthoritative[b.lower()] for b in sorted(excluded)},
        )
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
        index_ids, stamps = _ids_and_stamps(table)
    else:
        table = db.create_table(PROD_TABLE, schema=_schema(dim))
        index_ids, stamps = [], {}
    _log.info(f"index: {len(index_ids)} rows in table '{PROD_TABLE}'")

    # `_schema()` only reaches tables this call creates, so a table built before `first_seen`
    # existed keeps its frozen schema — and `apply_sync` requires rows to match it exactly. Add the
    # column before writing any row that carries it. Idempotent, so it runs once and then no-ops
    # (ADR-0031); existing rows get null, which is honest — we don't know when we first saw them.
    if _FIRST_SEEN_FIELD.name not in table.schema.names:
        _log.info(f"adding '{_FIRST_SEEN_FIELD.name}' to the existing table")
        table.add_columns(_FIRST_SEEN_FIELD)

    # Replace the rows of Jobs being re-embedded with a description they previously lacked
    # (ADR-0050) — before planning, not after. `plan_sync` computes add = fresh - index, so an id
    # still listed in `index_ids` is excluded from the adds; deleting its row afterwards would take
    # the Job out of the table with nothing to put back. Their `first_seen` returns with them: the
    # Job never left the corpus, only its vector improved, and that column is served and filterable
    # (`seen_within`, and the alerts watermark), so re-stamping would surface every upgrade as a
    # new listing and re-notify subscribers — tens of thousands of them on the first run.
    taken = _take_upgrades(table, Path(args.upgrades))
    index_ids = [job_id for job_id in index_ids if job_id not in taken]

    plan = plan_sync(index_ids, fresh, boards, live, stamps)
    # `add` counts every row written, and an upgrade is a delete-then-re-add of a Job that never
    # left — so reading `add - evict` as growth overstates it by exactly the upgrade count. Over
    # 19 runs that read as +4,376 while the table actually fell by 388 rows. Spell out the split
    # and the net, so a run says in one line whether the served index grew.
    listings = len(plan.add) - len(taken)
    _log.info(
        f"plan: add {len(plan.add)} ({listings} new listings + {len(taken)} re-embedded), "
        f"evict {len(plan.delete)} -> net {listings - len(plan.delete):+d} rows"
    )
    withheld = sum(count for _, count in plan.held)
    if plan.held:
        # Loud on purpose: every held Board is a scrape that came back short, and the guard only
        # hides the symptom. Silence here would turn a broken scrape into a quiet no-op.
        _log.warning(
            f"collapse guard: withheld {withheld} evictions across {len(plan.held)} Boards that "
            f"each lost >{COLLAPSE_RATIO:.0%} of their rows in one run — that is a truncated "
            f"scrape, not a delisting (ADR-0046); each drained up to {COLLAPSE_RATIO:.0%} of its "
            "rows this run and will drain the rest over later runs (ADR-0055)"
        )
        for board, count in plan.held:
            _log.warning(f"  withheld {count} evictions on {board}")
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
            for field in PLANNER_ONLY_FIELDS:
                row.pop(
                    field, None
                )  # store-only meta; the table's schema has no column for it
            row["vector"] = vectors[row_of[job_id]].tolist()
            row[_FIRST_SEEN_FIELD.name] = taken.get(job_id) or stamp
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
        ]
        + (
            [
                f"- collapse guard withheld **{withheld:,}** evictions across "
                f"**{len(plan.held)}** Boards that came back short"
            ]
            if plan.held
            else []
        ),
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
    p_sync.add_argument(
        "--unauthoritative-boards",
        default=str(_UNAUTHORITATIVE),
        help="JSON of Boards whose scraped list is not authoritative, written by scrape_join; "
        "they are dropped from the eviction scope (ADR-0053). Missing file means no Board is "
        "protected",
    )
    p_sync.add_argument(
        "--upgrades",
        default=str(_UPGRADES),
        help="file of Job ids re-embedded with a newly-available description; their rows are "
        "replaced and their first_seen preserved (ADR-0050)",
    )
    p_sync.add_argument(
        "--ledger",
        default=str(_LEDGER),
        help="liveness ledger dir, for resolving ids to live Boards (default: data/validate/liveness)",
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
