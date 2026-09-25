#!/usr/bin/env python3
"""Record this run's tick of the Trends history (ADR-0040, ADR-0230) — merge stage.

Runs after ``index sync`` and ``index prune``, so it counts the **served stock**: every row
still in the ``jobs`` table gets a role family from its title and its served description
``vector``, through the classifier head in ``config/role_family_classifier/`` (ADR-0220,
ADR-0224, :mod:`headstart.ingest.role_family_classifier`), and titles already encoded under that
head come from a cache kept in ``data/state``. The row is banded by the experience columns the
table already carries, and counted per Board into ``(metric, family, band)`` groups, with
non-tech as one ``(stock, non-tech, all)`` group a Board.
:func:`headstart.trends.trend_history.record_tick` writes the tick as one file of the Board-delta
ledger: each group's change since the last tick, and the tick's **Methodology** (the family list,
the classifier head, the tech filter, the derivations and the dedup rules). A new classifier head is
a delta like any other tick; the tick it lands on is a counting change because its Methodology moved
(ADR-0230).

It also records which family each row landed in and reports the rows that **changed** family
since the last tick (ADR-0057, :mod:`headstart.ingest.role_assignments`). Counting stock alone
cannot tell a closure apart from a reassignment, and a retitled or re-described posting moves
between families — so the transitions ride their own ledger rather than distorting this one.

The same snapshot gives each tick's **turnover** (ADR-0227, :mod:`headstart.ingest.job_turnover`).
The ids that arrived since the last tick, and the ids that left, are booked as Opened, Closed or
Recounted per Board, family and band. They go into the tick's file as rows of their own metrics,
beside the level changes, so a net change can be read with what made it.

Degrades rather than dies: without the classifier head or the family list on disk it logs a
warning and exits 0, and while a new head's title cache is still warming up it fills the cache
and counts nothing — trends must never sink a run that already scraped and embedded
successfully.

Run: python -m headstart.ingest.role_trends
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np

from headstart import log
from headstart.ingest import (
    EVICTION_QUEUE_PATH,
    REPO_ROOT,
    UNAUTHORITATIVE_BOARDS_PATH,
    job_turnover,
    role_assignments,
    role_family_classifier,
    run_ts,
)
from headstart.ingest.doc_prep import DERIVATIONS_VERSION
from headstart.ingest.index_plan import (
    DEDUP_VERSION,
    boards_by_canon,
    live_keep_set,
    read_unauthoritative_boards,
    resolve_board,
)
from headstart.ingest.role_assignments import Placement
from headstart.jobs import tech_filter
from headstart.trends import role_taxonomy, trend_history

_log = log.get(__name__, __spec__)

_DB = REPO_ROOT / "data" / "lancedb"
_FAMILIES = REPO_ROOT / "config" / "role_families.json"  # curated, in git (ADR-0220)
# the trained head and its manifest, in git; a new head is a new version (ADR-0220)
_CLASSIFIER = REPO_ROOT / "config" / "role_family_classifier"
# normalised title -> family under the current head, carried between runs in the state artifact
_TITLE_CACHE = REPO_ROOT / "data" / "state" / "role_title_families.parquet"
# Encoding time one run may spend filling the cache: a few thousand new titles take well under a
# minute, and a new head's ~270k-title backlog fills over a few runs instead of timing one out.
_CLASSIFY_BUDGET_SECONDS = 720.0
# Trends counts nothing until this share of served rows has a decided title (a new head's warm-up).
_MIN_TITLE_COVERAGE = 0.99
_WATCHLIST = REPO_ROOT / "config" / "role_watchlist.json"  # curated, in git (ADR-0051)
_STATE = REPO_ROOT / "data" / "state"  # the tick files live under it (trend_history)
_BOARD_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
# id -> family snapshot + the transitions between snapshots (see role_assignments)
_ASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_assignments.parquet"
_REASSIGNMENTS = REPO_ROOT / "data" / "state" / "role_reassignments.csv"

# The flow window (ADR-0051): a row is "new" when its `first_seen` is within this many days of
# the measurement. A rolling week, not a per-run diff — the back-to-back cadence makes per-run deltas
# pipeline noise, and "how many roles appeared this week" is the question a job hunter has.
NEW_WINDOW_DAYS = 7


def _columns(rows) -> tuple[list, ...]:
    """The served columns the counts read, row-aligned. ``first_seen`` is absent when the table
    predates ADR-0031; those rows are stock, never new."""
    titles = rows["title"].to_pylist()
    seen = (
        rows["first_seen"].to_pylist()
        if "first_seen" in rows.schema.names
        else [None] * len(titles)
    )
    return (
        rows["id"].to_pylist(),
        rows["min_years"].to_pylist(),
        titles,
        rows["employment_type"].to_pylist(),
        rows["ats"].to_pylist(),
        seen,
    )


def count_board_groups(
    rows,
    families: list[str | None],
    watchlist: list[role_taxonomy.WatchRole],
    new_after: str,
    boards: list[str],
) -> tuple[
    dict[tuple[str, str, str, str], int],
    int,
    dict[str, Placement],
    dict[tuple[str, str, str, str], int],
]:
    """Count served rows into ``(metric, family, band, ats)`` groups, and each Board's
    contribution to them, in one pass; non-tech counted apart.

    ``families`` is each row's family, aligned with ``rows``; ``None`` marks a non-tech row.

    Returns ``(counts, non_tech, placed, board_counts)``. ``placed`` is where each tech row was
    counted, its family with the rest of its Board-delta key (ADR-0227):
    :mod:`headstart.ingest.role_assignments` diffs it against the previous tick, so a job that
    *changed* family is not miscounted as one that closed.

    Two metrics per group (ADR-0051): ``stock`` — every live row — and ``new`` — the subset
    whose ``first_seen`` is at or after ``new_after``. Stock answers "how big is this field";
    new answers "is it hiring this week", and the two disagree exactly where it matters (a
    large family can be barely posting). ``first_seen`` survives an ADR-0050 re-embed, so a
    re-embedded row does not read as a fresh opening; rows predating ADR-0031 carry no stamp
    and are never "new", which under-counts the first week after that ADR and nothing after.

    ``ats`` (ADR-0075) lets a Trends request narrow its scope to a chosen set of ATSes — every
    served row carries one, unlike ``first_seen`` there is no pre-existing-table case to guard.

    Watch roles (ADR-0051) are counted by title into the same structure under
    ``watch:{name}``, whatever family the row landed in — the pattern is the definition — but
    only on tech rows (ADR-0215): the chart excludes non-tech, and a watch line counting a
    grocery "Front End" clerk contradicted it. They are never a row's placement: a row "moving"
    between them is a title edit, not a reassignment.

    Non-tech rows are the tech filter's known creep (ADR-0017 is recall-biased on purpose) — kept
    out of the role groups, but returned as one number so the log carries a filter-health figure
    (ADR-0040), and counted per Board under ``(stock, non-tech, all)`` in ``board_counts``.

    ``board_counts`` is keyed ``(board, metric, family, band)``, the Board-delta ledger's key: a
    Board's ATS is its board_key's prefix, so it would only repeat the key (ADR-0230)."""
    ids, min_years, titles, employment, atses, seen = _columns(rows)
    if len(boards) != len(ids):
        raise ValueError("Board identities must align with served rows")
    counts: dict[tuple[str, str, str, str], int] = {}
    board_counts: dict[tuple[str, str, str, str], int] = {}
    placed: dict[str, Placement] = {}
    non_tech = 0

    def bump(board: str, family: str, band: str, ats: str, is_new: bool) -> None:
        key = ("stock", family, band, ats)
        counts[key] = counts.get(key, 0) + 1
        board_key = (board, "stock", family, band)
        board_counts[board_key] = board_counts.get(board_key, 0) + 1
        if is_new:
            key = ("new", family, band, ats)
            counts[key] = counts.get(key, 0) + 1
            board_key = (board, "new", family, band)
            board_counts[board_key] = board_counts.get(board_key, 0) + 1

    for job_id, years, title, etype, first, ats, board, family in zip(
        ids, min_years, titles, employment, seen, atses, boards, families, strict=True
    ):
        # ISO-8601 UTC on both sides, so string order is time order.
        is_new = bool(first) and first >= new_after
        if family is None:
            non_tech += 1
            key = (board, "stock", role_taxonomy.NON_TECH, "all")
            board_counts[key] = board_counts.get(key, 0) + 1
            continue
        band = role_taxonomy.band(years, title, etype)
        for role in watchlist:
            if role.matches(title):
                bump(board, role_taxonomy.WATCH_PREFIX + role.name, band, ats, is_new)
        placed[job_id] = Placement(board, family, band, ats)
        bump(board, family, band, ats, is_new)
    return counts, non_tech, placed, board_counts


def _turnover_this_tick(
    placed: dict[str, Placement],
    first_seen: dict[str, str | None],
    live: dict[str, str],
    *,
    snapshot: Path,
    counted_boards: set[str],
    eviction_queue: Path,
    unauthoritative_boards: Path,
) -> tuple[dict[job_turnover.Key, int], str | None]:
    """This tick's turnover and its Unauthoritative-Board markers, keyed like the delta ledger
    (ADR-0227), and the stamp of the snapshot it diffed. ``first_seen`` covers every served row;
    ``counted_boards`` is every Board the last tick counted any row of, so a Board missing from it
    was found this tick and its backlog is Recounted, not Opened.
    There is no turnover without a comparable snapshot: on the first tick after ADR-0227, and
    after an unreadable one."""
    turnover: dict[job_turnover.Key, int] = {}
    # One marker per Board whose scrape could not show an absence (ADR-0053): none of its
    # closures counts this tick, and the Space says so rather than let it read as all opening.
    for lowered in read_unauthoritative_boards(unauthoritative_boards):
        turnover[job_turnover.unscoped_marker(live.get(lowered, lowered))] = 1
    loaded = role_assignments.load_placements(snapshot)
    if loaded is None:
        # `load_placements` has already said which reason, and that turnover waits a tick.
        return turnover, None
    previous, previous_as_of = loaded
    booked = job_turnover.turnover(
        previous,
        placed,
        previous_as_of=previous_as_of,
        first_seen=first_seen,
        counted_boards=counted_boards,
        evicted=job_turnover.queued_evictions(eviction_queue).keys(),
    )
    totals = {
        metric: sum(n for key, n in booked.items() if key[1] == metric)
        for metric in job_turnover.METRICS
    }
    _log.info(
        f"turnover since {previous_as_of}: opened {totals[job_turnover.OPENED]}, closed "
        f"{totals[job_turnover.CLOSED]}, recounted +{totals[job_turnover.RECOUNTED_IN]} "
        f"−{totals[job_turnover.RECOUNTED_OUT]}; closures not counted on {len(turnover)} "
        "Unauthoritative Board(s) (ADR-0227)"
    )
    turnover.update(booked)
    return turnover, previous_as_of


def _served_row_logits(table, ids: list[str], head) -> np.ndarray:
    """Each served row's row part of the head's logits (ADR-0224), aligned with ``ids``, one batch
    at a time so each batch's vectors shrink to one logit per family. Raises ``ValueError`` when
    ids repeat or the vectors do not cover the rows exactly: a row left unfilled would otherwise
    be decided from uninitialised memory."""
    position = {job_id: i for i, job_id in enumerate(ids)}
    if len(position) != len(ids):
        raise ValueError(f"{len(ids) - len(position)} served ids repeat")
    out = np.empty((len(ids), len(head.families)), dtype=np.float32)
    filled = np.zeros(len(ids), dtype=bool)
    for batch_ids, vectors in role_family_classifier.served_vector_batches(table):
        try:
            rows = [position[job_id] for job_id in batch_ids]
        except KeyError as exc:
            raise ValueError(f"served id {exc} has a vector but no row") from None
        out[rows] = head.row_logits(vectors)
        filled[rows] = True
    if not filled.all():
        raise ValueError(f"{int((~filled).sum())} served rows have no vector")
    return out


def main() -> int:
    log.setup()
    log.context("role_trends")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--classifier", type=Path, default=_CLASSIFIER)
    ap.add_argument("--title-cache", type=Path, default=_TITLE_CACHE)
    ap.add_argument("--families", type=Path, default=_FAMILIES)
    ap.add_argument("--watchlist", type=Path, default=_WATCHLIST)
    ap.add_argument("--state", type=Path, default=_STATE)
    ap.add_argument("--board-ledger", type=Path, default=_BOARD_LEDGER)
    ap.add_argument("--assignments", type=Path, default=_ASSIGNMENTS)
    ap.add_argument("--reassignments", type=Path, default=_REASSIGNMENTS)
    # sync's evictions not yet booked, and this run's Unauthoritative Boards (ADR-0227)
    ap.add_argument("--eviction-queue", type=Path, default=EVICTION_QUEUE_PATH)
    ap.add_argument(
        "--unauthoritative-boards", type=Path, default=UNAUTHORITATIVE_BOARDS_PATH
    )
    args = ap.parse_args()

    # The step is `continue-on-error`, which would turn an unguarded FileNotFoundError into a
    # green run that silently never accrues a row, so every input is checked up front.
    missing = [
        str(p)
        for p in (
            args.classifier / "manifest.json",
            args.classifier / "head.npz",
            args.families,
        )
        if not p.exists()
    ]
    if missing:
        _log.warning(
            f"skipping trends this run — missing {', '.join(missing)} (the classifier head and "
            "the family list ship in git, ADR-0220)"
        )
        return 0

    import lancedb

    from headstart.embedding_conventions import MODEL as EMBED_MODEL
    from headstart.embedding_conventions import PROD_TABLE

    try:
        family_names = role_taxonomy.load_families(args.families)
        head = role_family_classifier.Head(args.classifier)
        head.check_families(family_names)
        watchlist = role_taxonomy.load_watchlist(args.watchlist, set(family_names))
    except ValueError as exc:
        # An unusable taxonomy is a real defect, not a missing prerequisite. The workflow step is
        # `continue-on-error`, so without this it would crash into a green run with no annotation
        # at all; ERROR + exit 1 makes it visible and still non-fatal.
        _log.error(f"role taxonomy unusable, no trends this run: {exc}")
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    if not n:
        _log.warning(f"served table '{PROD_TABLE}' is empty — no trend rows this run")
        return 0
    # The head read the served `vector` as it was trained on: another embedder or width would feed
    # its row part numbers it never learned, and still yield a confident family.
    row_width = table.schema.field("vector").type.list_size
    if (head.row_vector_model, head.row_vector_dim) != (EMBED_MODEL, row_width):
        _log.error(
            f"role taxonomy unusable, no trends this run: the head was trained on "
            f"{head.row_vector_dim}-wide {head.row_vector_model} vectors, the served table holds "
            f"{row_width}-wide {EMBED_MODEL} ones — retrain the head (ADR-0224)"
        )
        return 1
    # The "assigning N served rows to K families" prefix is parsed by
    # scripts/runlog/fanout_merge.py (tests/test_log_contract.py pins it); what follows is free.
    _log.info(
        f"assigning {n} served rows to {len(family_names)} families by title and description "
        f"(classifier head {head.version})"
    )
    # first_seen may be absent on a pre-ADR-0031 table; select() would raise on the missing
    # column, so ask only for what exists and let count_board_groups treat absence as
    # "never new".
    # ats carries no such case — every served row has had one since before this table existed.
    columns = ["id", "min_years", "title", "employment_type", "ats"]
    if "first_seen" in table.schema.names:
        columns.append("first_seen")
    rows = table.search().select(columns).limit(n).to_arrow()

    titles = rows["title"].to_pylist()
    cache = role_family_classifier.load_cache(args.title_cache, head.version)
    try:
        added = role_family_classifier.fill(
            cache,
            head,
            titles,
            _CLASSIFY_BUDGET_SECONDS,
            lambda filled: role_family_classifier.save_cache(args.title_cache, filled),
        )
    except Exception as exc:  # noqa: BLE001 - a failed fill keeps what the cache already holds
        _log.warning(
            f"title classifier failed: {type(exc).__name__}: {exc}", exc_info=True
        )
        added = 0
    covered = role_family_classifier.coverage(cache, titles)
    _log.info(
        f"title cache: {added} titles classified this run; {covered:.1%} of served rows decided"
    )
    if covered < _MIN_TITLE_COVERAGE:
        # A new head starts with an empty cache. Counting now would chart every undecided title
        # as unclassified-tech and then read its decision next run as hiring, so Trends waits.
        _log.warning(
            f"classifier warming up: {covered:.1%} of served rows have a decided title, "
            f"{_MIN_TITLE_COVERAGE:.0%} needed — no trend rows this run"
        )
        return 0

    try:
        row_logits = _served_row_logits(table, rows["id"].to_pylist(), head)
    except ValueError as exc:
        _log.error(f"role families undecidable, no trends this run: {exc}")
        return 1
    decided = role_family_classifier.decide_rows(cache, head, titles, row_logits)
    families = [
        None if family == role_taxonomy.NON_TECH else family for family in decided
    ]

    # The run's one stamp, which `index prune` also wrote its dedup evictions under (ADR-0210).
    now = run_ts()
    # How this tick counts (ADR-0164, ADR-0230), carried by the tick's own file: a tick whose
    # Methodology differs from the one before it is a counting change.
    methodology = trend_history.Methodology(
        family_list_fingerprint=role_taxonomy.family_list_fingerprint(args.families),
        family_classifier_version=head.version,
        tech_filter_version=tech_filter.TECH_FILTER_VERSION,
        derivations_version=DERIVATIONS_VERSION,
        dedup_version=DEDUP_VERSION,
    )
    ts = now.isoformat(timespec="seconds")
    new_after = (now - timedelta(days=NEW_WINDOW_DAYS)).isoformat(timespec="seconds")
    try:
        live = boards_by_canon(live_keep_set(args.board_ledger))
        ids, *_, first_seen = _columns(rows)
        # The Board identity index sync and prune resolve through.
        boards = [resolve_board(job_id, live) for job_id in ids]
        counts, non_tech, placed, board_counts = count_board_groups(
            rows, families, watchlist, new_after, boards
        )
        assigned = {job_id: p.family for job_id, p in placed.items()}
        # Read before the snapshot is overwritten below: the transitions diff it (ADR-0057), and
        # so does this tick's turnover (ADR-0227). A new head re-decides every family, so its
        # transitions are not comparable across it.
        previous_families = role_assignments.load_previous(
            args.assignments, head.version
        )
        had_snapshot = args.assignments.exists()
        _, last_levels = trend_history.board_levels(args.state)
        turnover, booked_through = _turnover_this_tick(
            placed,
            dict(zip(ids, first_seen, strict=True)),
            live,
            snapshot=args.assignments,
            counted_boards={board for board, *_ in last_levels},
            eviction_queue=args.eviction_queue,
            unauthoritative_boards=args.unauthoritative_boards,
        )
        # The tick file's key: a Board's ATS is its board_key's prefix (ADR-0230).
        tick_turnover: dict[tuple[str, str, str, str], int] = {}
        for key, count in turnover.items():
            short = (key.board, key.metric, key.family, key.band)
            tick_turnover[short] = tick_turnover.get(short, 0) + count
        written = trend_history.record_tick(
            args.state, ts, board_counts, tick_turnover, methodology
        )
        # The snapshot turnover diffs, and the level changes, must move together (ADR-0227):
        # the tick's file without its snapshot would book this tick's turnover again next tick,
        # and the snapshot without the file would leave next tick's stock change covering two
        # ticks while its turnover covered one. So a failed snapshot takes the file back out.
        try:
            role_assignments.save(args.assignments, placed, head.version, ts)
        except BaseException:
            trend_history.tick_path(args.state / trend_history.DELTAS, ts).unlink(
                missing_ok=True
            )
            raise
        # Entries the published snapshot already covers are dropped, never this run's: see
        # `job_turnover.drop_evictions_through`. After the snapshot, so a failed save keeps them.
        if booked_through is not None:
            dropped, kept = job_turnover.drop_evictions_through(
                args.eviction_queue, booked_through
            )
            _log.info(
                f"eviction queue: dropped {dropped} booked through {booked_through}, "
                f"{kept} carried forward"
            )
    except (OSError, ValueError) as exc:
        _log.error(
            f"comparable Trends state unusable, no trends this run: {exc}",
            exc_info=True,
        )
        return 1
    stock_top = sorted(
        ((k, c) for k, c in counts.items() if k[0] == "stock"), key=lambda kv: -kv[1]
    )[:5]
    fresh_total = sum(c for k, c in counts.items() if k[0] == "new")
    _log.info(
        f"appended {written} rows @ {ts} -> "
        f"{trend_history.tick_path(args.state / trend_history.DELTAS, ts)} | top: "
        # `ats` is in the key (ADR-0075), so it belongs in the label: without it two ATSes' rows
        # for one family and band render identically and read as a double-count.
        + ", ".join(
            f"{family}/{band}/{ats} {c}" for (_, family, band, ats), c in stock_top
        )
        + f" | new in {NEW_WINDOW_DAYS}d: {fresh_total}"
    )
    _log.info(
        f"non-tech: {non_tech} of {n} served rows ({100 * non_tech / n:.1f}% — the "
        "ADR-0017 filter's creep) excluded from the chart"
    )

    # Which rows CHANGED family since the last tick. Without this, a re-embedded job that moves
    # from one family to another is indistinguishable in the stock series from a closure plus an
    # unrelated new posting — which is how a 622-row "software-engineering decline" turned out to
    # be largely redistribution. Diagnostic only: never fails the run.
    try:
        moved = role_assignments.transitions(previous_families, assigned)
        # The snapshot was written above, BEFORE this ledger, deliberately. The ledger is
        # append-only, so if the snapshot write failed after appending, the next tick would diff
        # against the stale snapshot and append the same transitions again — silently inflating
        # the series with duplicates that are indistinguishable from real repeated moves. This
        # order can instead lose one tick's transitions, which under-reports once and stays
        # truthful.
        rows_written = role_assignments.append_ledger(
            args.reassignments, moved, head.version, ts
        )
        if previous_families is None:
            why = (
                "discarded the previous snapshot (unreadable, or a re-base: a new classifier "
                "head)"
                if had_snapshot
                else "first snapshot"
            )
            _log.info(
                f"assignments: {why} — wrote {len(assigned)} rows to {args.assignments}; "
                "transitions start next run"
            )
        else:
            total = sum(moved.values())
            top = sorted(moved.items(), key=lambda kv: -kv[1])[:3]
            _log.info(
                f"assignments: {total} of {len(assigned)} rows changed family "
                f"({100 * total / max(len(assigned), 1):.2f}%), {rows_written} transition rows"
                + (
                    " | top: " + ", ".join(f"{a}->{b} {c}" for (a, b), c in top)
                    if top
                    else ""
                )
            )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never sink a good run
        _log.warning(
            f"assignment diff skipped: {type(exc).__name__}: {exc}", exc_info=True
        )
    return 0


if __name__ == "__main__":
    log.run_logging_crash(
        _log,
        main,
        "role_trends failed — no trend rows this run, and the Board ledgers "
        "company_directory and the Space's Hot ranking read may be a tick stale",
    )
