#!/usr/bin/env python3
"""Blend this run's measurements into the ``data/state/`` board ledgers (ADR-0028).

All run in the join stage, all read what this run produced, and all leave Boards the run didn't
touch untouched — the partial-harvest rule (ADR-0022). They stay separate subcommands because the
workflow treats their failures differently: a cost- or failures-ledger failure is
``continue-on-error`` (it costs one run of memory), a priority failure is not::

    python -m headstart.ingest.update_ledgers priority   # ADR-0022
    python -m headstart.ingest.update_ledgers cost       # ADR-0027
    python -m headstart.ingest.update_ledgers failures   # consecutive-gone quarantine
    python -m headstart.ingest.update_ledgers gap        # ADR-0062

**priority** runs after the tech filter: every Board present in the harvest snapshot
(``data/jobs``) gets its EWMA score refreshed from its tech-subset count (``data/jobs/tech``);
Boards the run didn't scrape carry their rows unchanged. The ledger drives the next harvest's
slice ordering and the embed's within-bucket ordering.

**cost** runs right after the fragments land. Each scrape shard timed every Board it scraped and
streamed the rows to ``board_cost.csv`` inside its own fragment dir; this reads all of them and
EWMA-blends them into ``data/state/board_cost.csv``, which rides the HF state round-trip and is what
the *next* run's ``scrape_plan`` bin-packs on. A shard that died mid-write contributes every row it
did flush; only a torn final line is skipped.

**failures** reads the shard reports' per-Board errors, keeps only the *gone* class (404/410 —
see :mod:`headstart.ingest.board_failures` for why a 429 or a timeout must not count), and tracks
consecutive gone-runs per Board. At :data:`~headstart.ingest.board_failures.QUARANTINE_AT` strikes
the Board leaves the next run's scrape slice; any successful scrape clears it. This is the loop
nothing else closes: the liveness ledger is only written by manual probes, and the priority ledger
carries an unscraped-looking Board unchanged.

**gap** runs after ``update_descriptions``, and is the one ledger read from the *stored* corpus
rather than this run's: it counts, per Board, the embedded Jobs whose description the ADR-0050
store has never settled. Those Jobs' derived columns cannot be repaired without the text, so the
next run's ``scrape_plan`` reserves part of its exploration tail for the Boards holding them
(ADR-0062). Recomputed from scratch every run, so it empties itself as the gap closes.

Seed the priority ledger from a full local corpus with::

    python -m headstart.ingest.update_ledgers priority --jobs data/jobs/tech
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from headstart import log
from headstart.board_cost import ShardCost, ats_medians, read_shard_rows
from headstart.board_cost import load as load_cost
from headstart.board_cost import save as save_cost
from headstart.board_cost import update as update_cost
from headstart.board_priority import load as load_priority
from headstart.board_priority import save as save_priority
from headstart.board_priority import update as update_priority
from headstart.corpus import board_of, iter_jobs
from headstart.harvest import COST_FILENAME
from headstart import board_description_gap
from headstart.ingest import REPO_ROOT, board_failures, observability
from headstart.ingest.update_descriptions import settled_ids

_log = log.get(__name__, __spec__)

_JOBS = REPO_ROOT / "data" / "jobs"
_TECH = REPO_ROOT / "data" / "jobs" / "tech"
_FRAGMENTS = REPO_ROOT / "data" / "scrape" / "fragments"
_PRIORITY_LEDGER = REPO_ROOT / "data" / "state" / "board_priority.csv"
_COST_LEDGER = REPO_ROOT / "data" / "state" / "board_cost.csv"
_FAILURES_LEDGER = REPO_ROOT / "data" / "state" / "board_failures.csv"
_GAP_LEDGER = REPO_ROOT / "data" / "state" / "board_description_gap.csv"
_META = REPO_ROOT / "data" / "embeddings" / "jobs" / "meta.jsonl"
_DESCRIPTIONS = REPO_ROOT / "data" / "descriptions"


def priority(args: argparse.Namespace) -> int:
    snapshot_boards = {board_of(j["id"]) for j in iter_jobs(args.jobs)}
    tech_counts = Counter(board_of(j["id"]) for j in iter_jobs(args.tech))
    prev = load_priority(args.ledger)
    rows = update_priority(prev, tech_counts, snapshot_boards)
    save_priority(args.ledger, rows)

    new = sum(1 for b in snapshot_boards if b in rows and b not in prev)
    pruned = sum(1 for b in snapshot_boards if b in prev and b not in rows)
    carried = sum(1 for b in prev if b not in snapshot_boards)
    _log.info(
        f"priority: {len(snapshot_boards)} boards in snapshot | "
        f"{len(rows)} ledger rows ({new} new, {pruned} pruned, {carried} carried) "
        f"-> {args.ledger}"
    )
    top = sorted(rows.items(), key=lambda kv: -kv[1].score)[:10]
    for board, p in top:
        _log.info(f"  {p.score:9.1f}  {board} ({p.last_tech_jobs} tech jobs)")
    return 0


def cost(args: argparse.Namespace) -> int:
    measured: dict[str, ShardCost] = {}
    shards = 0
    if args.fragments.is_dir():
        for path in sorted(args.fragments.glob(f"*/{COST_FILENAME}")):
            rows = read_shard_rows(path)
            if rows:
                shards += 1
            measured.update(rows)
            _log.info(f"cost: {path.parent.name}: {len(rows)} timed boards")

    prev = load_cost(args.ledger)
    rows = update_cost(prev, measured)
    save_cost(args.ledger, rows)

    new = sum(1 for b in measured if b not in prev)
    total = sum(c.seconds for c in rows.values())
    _log.info(
        f"cost: {len(measured)} boards timed across {shards} shard(s) | "
        f"{len(rows)} ledger rows ({new} new) | Σ {total / 60:.0f} board-minutes -> {args.ledger}"
    )
    for ats, med in sorted(ats_medians(rows).items(), key=lambda kv: -kv[1])[:8]:
        _log.info(f"  {med:8.1f}s median  {ats}")
    return 0


def failures(args: argparse.Namespace) -> int:
    reports = observability.read_shards(args.fragments)
    gone: dict[str, str] = {}
    alive: set[str] = set()
    for report in reports:
        for key, reason in (report.get("errors") or {}).items():
            board = board_failures.board_key_of(key)
            if board is not None and board_failures.is_gone(str(reason)):
                gone[board] = str(reason)
        # boards_ok carries the zero-job successes the corpus can't: alive-and-empty must
        # clear a streak, or a board that empties after a few 404s stays one strike from
        # quarantine forever
        for key in report.get("boards_ok") or []:
            board = board_failures.board_key_of(key)
            if board is not None:
                alive.add(board)
    # `board_of` yields the board_key shape the ids were built from, so both sides of the
    # update pair in the same key space (ADR-0049). The union with boards_ok is belt and
    # braces: pre-change shard reports carry no boards_ok, and the corpus still clears any
    # board that produced lines.
    produced = alive | {board_of(j["id"]) for j in iter_jobs(args.jobs)}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    prev = board_failures.load(args.ledger)
    rows = board_failures.update(prev, gone, produced, now)
    board_failures.save(args.ledger, rows)

    quarantined = board_failures.quarantined(rows)
    cleared = sum(1 for b in prev if b not in rows)
    _log.info(
        f"failures: {len(gone)} board(s) reported gone (404/410) across {len(reports)} shard(s) | "
        f"{len(rows)} ledger rows ({cleared} cleared by a successful scrape) | "
        f"{len(quarantined)} at/over {board_failures.QUARANTINE_AT} strikes -> {args.ledger}"
    )
    for board in sorted(quarantined)[:20]:
        row = rows[board]
        _log.info(f"  quarantined  {board} ({row.strikes} strikes, {row.last_reason})")
    return 0


def gap(args: argparse.Namespace) -> int:
    from headstart.scrapers.registry import DISABLED_ATS

    if not args.meta.exists():
        _log.warning(f"gap: no {args.meta} yet — nothing embedded, so no gap to record")
        return 0

    held = settled_ids(args.descriptions)
    if not held:
        # The join fetches the description store on a warn-only fallback, so an empty one here
        # means the download failed, not that nothing is settled. Writing the ledger now would
        # mark *every* embedded Board as gap-ful and hand the next run's scrape a slice built
        # from a missing file — worse than no boost at all.
        _log.warning(
            f"gap: {args.descriptions} holds nothing — the store is missing, not empty; "
            "leaving the ledger as it is"
        )
        return 0

    counts: Counter[str] = Counter()
    rows = unreachable = 0
    with args.meta.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows += 1
            if rows % 100_000 == 0:
                _log.info(f"  gap: scanned {rows:,} stored rows")
            if row["id"] in held:
                continue
            # A disabled ATS is never in any scrape slice, so its rows can only leave the index by
            # eviction — counting them would reserve slots no Board selection can ever spend.
            if row.get("ats") in DISABLED_ATS:
                unreachable += 1
                continue
            # Lowercased, like every other Board-key comparison in the plan path (ADR-0049): the
            # liveness ledger's casing and the one baked into a Job id need not agree, and the
            # slice looks this up through `board_identity`. Measured against a real store, 1,693
            # of 13,708 gap Boards — 45,375 Jobs, 23% of the backlog — matched the live slice
            # only case-insensitively, so keying this as-observed would strand every one of them.
            # It also folds ADR-0023's case-variant pairs (`.../External` and `.../external` are
            # one Board) into a single row instead of two half-counts.
            counts[board_of(row["id"]).lower()] += 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    board_description_gap.save(args.ledger, dict(counts), today=today)
    jobs = sum(counts.values())
    _log.info(
        f"gap: {rows:,} stored rows | {len(held):,} settled | {jobs:,} unsettled across "
        f"{len(counts):,} boards ({unreachable:,} on a disabled ATS, unreachable) -> {args.ledger}"
    )
    for board, n in counts.most_common(10):
        _log.info(f"  {n:6,} unsettled  {board}")
    return 0


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="ledger_name", required=True)

    p_priority = sub.add_parser("priority", help="blend tech-job counts (ADR-0022)")
    p_priority.add_argument("--jobs", type=Path, default=_JOBS)
    p_priority.add_argument("--tech", type=Path, default=_TECH)
    p_priority.add_argument("--ledger", type=Path, default=_PRIORITY_LEDGER)
    p_priority.set_defaults(fn=priority)

    p_cost = sub.add_parser("cost", help="blend measured scrape seconds (ADR-0027)")
    p_cost.add_argument(
        "--fragments",
        type=Path,
        default=_FRAGMENTS,
        help="dir of scrape fragment dirs (default: data/scrape/fragments)",
    )
    p_cost.add_argument(
        "--ledger",
        type=Path,
        default=_COST_LEDGER,
        help="cost ledger to update (default: data/state/board_cost.csv)",
    )
    p_cost.set_defaults(fn=cost)

    p_failures = sub.add_parser(
        "failures", help="track consecutive gone-runs; quarantine confirmed-dead boards"
    )
    p_failures.add_argument(
        "--fragments",
        type=Path,
        default=_FRAGMENTS,
        help="dir of scrape fragment dirs (default: data/scrape/fragments)",
    )
    p_failures.add_argument(
        "--jobs",
        type=Path,
        default=_JOBS,
        help="this run's scrape output; every Board with lines here clears its streak "
        "(default: data/jobs)",
    )
    p_failures.add_argument(
        "--ledger",
        type=Path,
        default=_FAILURES_LEDGER,
        help="failures ledger to update (default: data/state/board_failures.csv)",
    )
    p_failures.set_defaults(fn=failures)

    p_gap = sub.add_parser(
        "gap",
        help="count stored Jobs whose description is unsettled, per Board (ADR-0062)",
    )
    p_gap.add_argument(
        "--meta",
        type=Path,
        default=_META,
        help="the embedding store's metadata, one row per embedded Job "
        "(default: data/embeddings/jobs/meta.jsonl)",
    )
    p_gap.add_argument(
        "--descriptions",
        type=Path,
        default=_DESCRIPTIONS,
        help="the ADR-0050 description store (default: data/descriptions)",
    )
    p_gap.add_argument(
        "--ledger",
        type=Path,
        default=_GAP_LEDGER,
        help="gap ledger to write (default: data/state/board_description_gap.csv)",
    )
    p_gap.set_defaults(fn=gap)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
