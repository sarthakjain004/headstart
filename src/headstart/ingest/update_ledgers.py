#!/usr/bin/env python3
"""Blend this run's measurements into the ``data/state/`` board ledgers (ADR-0028).

All run in the join stage, all read what this run produced, and all leave Boards the run didn't
touch untouched — the partial-harvest rule (ADR-0022). They stay separate subcommands because the
workflow treats their failures differently: a cost- or failures-ledger failure is
``continue-on-error`` (it costs one run of memory), a priority failure is not::

    python -m headstart.ingest.update_ledgers priority   # ADR-0022
    python -m headstart.ingest.update_ledgers cost       # ADR-0027
    python -m headstart.ingest.update_ledgers failures   # consecutive-gone quarantine

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

Seed the priority ledger from a full local corpus with::

    python -m headstart.ingest.update_ledgers priority --jobs data/jobs/tech
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from headstart import log
from headstart.board_cost import ats_medians, read_shard_rows
from headstart.board_cost import load as load_cost
from headstart.board_cost import save as save_cost
from headstart.board_cost import update as update_cost
from headstart.board_priority import load as load_priority
from headstart.board_priority import save as save_priority
from headstart.board_priority import update as update_priority
from headstart.corpus import board_of, iter_jobs
from headstart.harvest import COST_FILENAME
from headstart.ingest import REPO_ROOT, board_failures, observability

_log = log.get(__name__, __spec__)

_JOBS = REPO_ROOT / "data" / "jobs"
_TECH = REPO_ROOT / "data" / "jobs" / "tech"
_FRAGMENTS = REPO_ROOT / "data" / "scrape" / "fragments"
_PRIORITY_LEDGER = REPO_ROOT / "data" / "state" / "board_priority.csv"
_COST_LEDGER = REPO_ROOT / "data" / "state" / "board_cost.csv"
_FAILURES_LEDGER = REPO_ROOT / "data" / "state" / "board_failures.csv"


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
    measured: dict[str, tuple[float, int]] = {}
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
    for report in reports:
        for key, reason in (report.get("errors") or {}).items():
            if not board_failures.is_gone(str(reason)):
                continue
            ats, _sep, slug = str(key).partition(":")  # a Workday slug holds colons
            board = board_failures.board_key_of(ats, slug)
            if board is not None:
                gone[board] = str(reason)
    # `board_of` yields the board_key shape the ids were built from, so both sides of the
    # update pair in the same key space (ADR-0049).
    produced = {board_of(j["id"]) for j in iter_jobs(args.jobs)}
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
    p_failures.add_argument("--jobs", type=Path, default=_JOBS)
    p_failures.add_argument(
        "--ledger",
        type=Path,
        default=_FAILURES_LEDGER,
        help="failures ledger to update (default: data/state/board_failures.csv)",
    )
    p_failures.set_defaults(fn=failures)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
