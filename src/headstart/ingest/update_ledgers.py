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
(ADR-0062). Recomputed from scratch every run, so it empties itself as the gap closes. Three
classes are counted *unreachable* rather than unsettled: rows on a disabled ATS, rows on a Board no
scrape slice can contain (ADR-0162), and rows whose Board this run scraped authoritatively without
re-emitting them — those postings have expired off the Board, so no future scrape can settle them
(#185).

Because it is recomputed, its total is a **level**, and a level cannot say whether the quota it
reserves is buying anything: a backlog that lost 500 rows and gained 500 prints the same number as
one nothing touched. So it also reports the movement since the ledger it read — ``N left the gap,
M joined it, net ±X``, plus each top Board's own delta — and sizes the Jobs sitting on Boards this
run could not read authoritatively (ADR-0162).

Seed the priority ledger from a full local corpus with::

    python -m headstart.ingest.update_ledgers priority --jobs data/jobs/tech
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from headstart import board_description_gap, log
from headstart.board_cost import ShardCost, ats_medians, read_shard_rows
from headstart.board_cost import load as load_cost
from headstart.board_cost import save as save_cost
from headstart.board_cost import update as update_cost
from headstart.board_identity import ats_of, board_key_of, board_of, lower_key
from headstart.board_priority import load as load_priority
from headstart.board_priority import save as save_priority
from headstart.board_priority import update as update_priority
from headstart.corpus import iter_jobs
from headstart.harvest import COST_FILENAME
from headstart.ingest import REPO_ROOT, board_failures, observability
from headstart.ingest.index_plan import read_unauthoritative_boards, resolve_board
from headstart.ingest.update_descriptions import held_ids

_log = log.get(__name__, __spec__)

_JOBS = REPO_ROOT / "data" / "jobs"
_TECH = REPO_ROOT / "data" / "jobs" / "tech"
_FRAGMENTS = REPO_ROOT / "data" / "scrape" / "fragments"
_PRIORITY_LEDGER = REPO_ROOT / "data" / "state" / "board_priority.csv"
_COST_LEDGER = REPO_ROOT / "data" / "state" / "board_cost.csv"
_FAILURES_LEDGER = REPO_ROOT / "data" / "state" / "board_failures.csv"
_GAP_LEDGER = REPO_ROOT / "data" / "state" / "board_description_gap.csv"
_UNAUTHORITATIVE = REPO_ROOT / "data" / "state" / "unauthoritative_boards.json"
_META = REPO_ROOT / "data" / "embeddings" / "jobs" / "meta.jsonl"
_DESCRIPTIONS = REPO_ROOT / "data" / "descriptions"
_LIVENESS = REPO_ROOT / "data" / "validate" / "liveness"


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
    # `is_gone` matches a literal "HTTP Error 404" in the reason text (board_failures._GONE), a
    # convention every scraper has to emit by hand — so a scraper that phrases its 404 any other
    # way silently never earns a strike, and its Board ages forever. `len(gone)` alone cannot
    # distinguish "nothing 404'd this run" from "the matcher no longer recognises how this
    # scraper says 404". Counting what was examined, and naming the shapes that did NOT match,
    # is what makes that visible: a real example is `browser_http.BrowserHTTPError`, recorded by
    # `harvest` as "BrowserHTTPError: HTTP 404: ..." — a genuine 404 that never matches the
    # pattern, because `_GONE` looks for "HTTP Error 404" and this says "HTTP 404".
    examined = 0
    unmatched: Counter[str] = Counter()
    for report in reports:
        for key, reason in report.errors.items():
            board = board_key_of(key)
            if board is None:
                continue
            examined += 1
            if board_failures.is_gone(str(reason)):
                gone[board] = str(reason)
            else:
                # The class, not the message: messages carry per-board detail (hosts, ids) and
                # would never group. `ats` alongside it because a matcher gap is usually one
                # scraper's phrasing, not a global one.
                head = str(reason).split(":", 1)[0].strip()[:60] or "unknown"
                unmatched[f"{ats_of(board)} {head}"] += 1
        # boards_ok carries the zero-job successes the corpus can't: alive-and-empty must
        # clear a streak, or a board that empties after a few 404s stays one strike from
        # quarantine forever
        for key in report.boards_ok:
            board = board_key_of(key)
            if board is not None:
                alive.add(board)
    # `board_of` yields the board_key shape the ids were built from, so both sides of the
    # update pair in the same key space (ADR-0049). The union with boards_ok is belt and
    # braces: pre-change shard reports carry no boards_ok, and the corpus still clears any
    # board that produced lines.
    produced = alive | {board_of(j["id"]) for j in iter_jobs(args.jobs)}
    now = datetime.now(UTC).isoformat(timespec="seconds")

    prev = board_failures.load(args.ledger)
    rows = board_failures.update(prev, gone, produced, now)
    board_failures.save(args.ledger, rows)

    quarantined = board_failures.quarantined(rows)
    cleared = sum(1 for b in prev if b not in rows)
    _log.info(
        f"failures: {len(gone)} of {examined} board error(s) read as gone (404/410) across "
        f"{len(reports)} shard(s) | {len(rows)} ledger rows ({cleared} cleared by a successful "
        f"scrape) | {len(quarantined)} at/over {board_failures.QUARANTINE_AT} strikes -> "
        f"{args.ledger}"
    )
    if unmatched:
        # Info, not warning: most of these are ordinary live failures (timeouts, 429s) that
        # *should* not be gone-strikes. It is the shape of the list that diagnoses a matcher gap —
        # a 404-ish class sitting here run after run is the signal, not the volume.
        _log.info(
            f"  {sum(unmatched.values())} error(s) did not read as gone; top classes: "
            + ", ".join(f"{cls} x{n}" for cls, n in unmatched.most_common(5))
        )
    for board in sorted(quarantined)[:20]:
        row = rows[board]
        _log.info(f"  quarantined  {board} ({row.strikes} strikes, {row.last_reason})")
    return 0


def _on_unauthoritative_board(job_id: str, unauthoritative: dict[str, str]) -> bool:
    """Is this id's Board one whose scrape this run was not authoritative (ADR-0053)?

    Resolved by prefix against the real ``board_key``, never through ``board_of``: ``board_of``'s
    answer for an id carrying a colon in its *native* part is a phantom Board no unauthoritative
    key matches (ADR-0049) — the gap that silently missed exactly the Workday ids it most needs to
    cover. Both callers ask this question about the same map, so they ask it the same way.
    """
    return lower_key(resolve_board(job_id, unauthoritative)) in unauthoritative


def _authoritative_scrape(
    jobs: Path, unauthoritative: dict[str, str]
) -> tuple[set[str], set[str]]:
    """The Boards whose scraped list this run can be read as their complete set of openings, and
    every id those Boards emitted.

    Boards are keyed like the gap counts themselves — ``board_of`` lowercased — so the two pair
    (ADR-0049). An id can only ever be emitted by the Board whose key prefixes it, so one flat id
    set answers "did this Board re-emit it" exactly as a per-Board set would.

    A Board that wrote no lines is simply absent, whether it went unscraped this run or was
    truncated to nothing, and absence is what leaves an id counted. The ADR-0053 half is only as
    good as its file: ``read_unauthoritative_boards`` fails **open**, so an unreadable
    ``unauthoritative_boards.json`` protects no Board here — the same bet ``index sync`` already
    makes on that file, taken for a strictly smaller action (a count, not an eviction).

    ``unauthoritative`` arrives already read rather than as a path because :func:`gap` needs the
    same map for a second test — sizing the unsettled Jobs sitting behind it (ADR-0162) — and one
    read is what keeps the two answers about the same Board from disagreeing.
    """
    boards: set[str] = set()
    emitted: set[str] = set()
    for job in iter_jobs(jobs):
        # Only the *test* resolves by prefix; the key stays `board_of`'s, so both sides of the
        # comparison in `gap` are built alike.
        if _on_unauthoritative_board(job["id"], unauthoritative):
            continue
        boards.add(lower_key(board_of(job["id"])))
        emitted.add(job["id"])
    return boards, emitted


def gap(args: argparse.Namespace) -> int:
    from headstart.config import load_active_companies
    from headstart.scrapers.registry import DISABLED_ATS

    if not args.meta.exists():
        _log.warning(f"gap: no {args.meta} yet — nothing embedded, so no gap to record")
        return 0

    held = held_ids(args.descriptions)
    if not held:
        # The join fetches the description store on a warn-only fallback, so an empty one here
        # means the download failed, not that we hold nothing. Writing the ledger now would
        # mark *every* embedded Board as gap-ful and hand the next run's scrape a slice built
        # from a missing file — worse than no boost at all.
        _log.warning(
            f"gap: {args.descriptions} holds nothing — the store is missing, not empty; "
            "leaving the ledger as it is"
        )
        return 0

    # Every Board `scrape_plan` may put in a slice, keyed exactly as it keys them for the gap
    # quota — `min_jobs=0`, the same call it makes, so nothing this counts reachable is a Board
    # the plan would refuse. An empty answer means the liveness dir is missing, not that no Board
    # is live, so it reclassifies nothing rather than emptying the ledger.
    selectable = {
        board_description_gap.key_for(c)
        for c in load_active_companies(args.liveness, min_jobs=0)
    }
    if not selectable:
        _log.warning(
            f"gap: {args.liveness} lists no selectable Board — counting every row as reachable"
        )

    unauthoritative = read_unauthoritative_boards(args.unauthoritative_boards)
    scraped, emitted = _authoritative_scrape(args.jobs, unauthoritative)

    counts: Counter[str] = Counter()
    # The unsettled Jobs whose Board this run did attempt and could not read authoritatively.
    # They stay in the count and keep their ADR-0062 quota — a truncated read is no evidence about
    # any particular id — but they are the population ADR-0162 declines to reclassify, and an
    # unmeasured population is exactly how this went five runs without being noticed.
    blocked_boards: set[str] = set()
    rows = unreachable = off_slice = expired = blocked = 0
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
            board = lower_key(board_of(row["id"]))
            # A Board no slice can contain — dead, parked, aliased away or a vendor test tenant —
            # is never scraped, so its rows can never settle and reserving gap quota against them
            # buys nothing. ADR-0062 named this class and left it in the count; measured on the
            # live ledger it is 134 Boards holding 13,592 Jobs, 30% of the backlog (ADR-0162).
            # Unlike quarantine it drains on its own: the ledger is rebuilt from scratch, so the
            # moment a liveness probe calls the Board live again its rows come straight back.
            if selectable and board not in selectable:
                off_slice += 1
                continue
            # An id its own Board's authoritative scrape did not re-emit has expired off that
            # Board, and `reconcile()` only ever acts on ids the *current* scrape returned — so
            # nothing can ever settle it, and counting it reserves gap quota no scrape can spend
            # (#185). Boards absent from `scraped` are no evidence either way, so their ids stay
            # unsettled — including one whose scrape came back unauthoritative this run.
            if board in scraped and row["id"] not in emitted:
                expired += 1
                continue
            counts[board] += 1
            if _on_unauthoritative_board(row["id"], unauthoritative):
                blocked += 1
                blocked_boards.add(board)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # Read before the write. `prior` is the generation `scrape_plan` built *this* run's slice
    # from, so the movement below prices exactly the quota this run spent. An absent *or empty*
    # ledger is not a prior of zero — subtracting one would print the whole backlog as inflow,
    # a spike that never happened — so both take the no-comparison branch.
    prior = board_description_gap.load(args.ledger)
    board_description_gap.save(args.ledger, dict(counts), today=today)
    jobs = sum(counts.values())
    _log.info(
        f"gap: {rows:,} stored rows | {len(held):,} held | {jobs:,} unsettled across "
        f"{len(counts):,} boards ({unreachable:,} on a disabled ATS, {off_slice:,} on a Board no "
        f"scrape can select, {expired:,} gone from a Board this run scraped in full — all "
        f"unreachable) -> {args.ledger}"
    )
    if not prior:
        _log.info("  gap: no prior ledger to compare against — this is the first count")
    else:
        # **Left**, not *settled*. A row also leaves this count when it is reclassified
        # unreachable (a disabled ATS, an off-slice Board, #185's expiry) or when its row leaves
        # the store — none of which fetched a description, and the expiry arm alone moved by
        # ±1,700 across the five runs that opened this. The settle rate is `update_descriptions`'
        # own `learned` count; this is movement, and its job is to tell draining from frozen.
        #
        # Per Board, not per Job: the ledger stores counts, so a Board that lost five and gained
        # five nets to zero on both sides. It is a floor on the churn, which is enough for that.
        left = sum(max(0, n - counts.get(b, 0)) for b, n in prior.items())
        joined = sum(max(0, n - prior.get(b, 0)) for b, n in counts.items())
        was = sum(prior.values())
        _log.info(
            f"  gap: drain vs the {was:,} unsettled across {len(prior):,} boards this run read: "
            f"{left:,} left the gap, {joined:,} joined it, net {jobs - was:+,}"
        )
    if blocked:
        _log.info(
            f"  gap: {blocked:,} unsettled Job(s) sit on {len(blocked_boards):,} Board(s) whose "
            "scrape this run was not authoritative (ADR-0053), so this run is no evidence about "
            "them either way"
        )
    for board, n in counts.most_common(10):
        # The per-Board delta is the sharpest half: eight of the top ten were byte-identical
        # across five runs, and seeing that took a hand diff of five logs (ADR-0162).
        moved = "" if not prior else f" ({n - prior.get(board, 0):+,})"
        _log.info(f"  {n:6,} unsettled{moved}  {board}")
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
        "--jobs",
        type=Path,
        default=_JOBS,
        help="this run's scrape output; a Job its Board scraped without re-emitting has expired "
        "off that Board, so it is unreachable rather than unsettled (default: data/jobs)",
    )
    p_gap.add_argument(
        "--unauthoritative-boards",
        type=Path,
        default=_UNAUTHORITATIVE,
        help="Boards whose scrape came back truncated or raised this run (ADR-0053); their "
        "missing Jobs stay unsettled (default: data/state/unauthoritative_boards.json)",
    )
    p_gap.add_argument(
        "--liveness",
        type=Path,
        default=_LIVENESS,
        help="liveness ledger dir; a Board absent from it is on no scrape slice, so its Jobs are "
        "unreachable rather than unsettled (default: data/validate/liveness)",
    )
    p_gap.add_argument(
        "--ledger",
        type=Path,
        default=_GAP_LEDGER,
        help="gap ledger to write (default: data/state/board_description_gap.csv)",
    )
    p_gap.set_defaults(fn=gap)

    args = ap.parse_args()
    # After parsing, so the line carries which ledger this is: all four subcommands run in the
    # same job, under the same module tag, and are otherwise indistinguishable in a merged log.
    log.context("update_ledgers", ledger=args.ledger_name)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
