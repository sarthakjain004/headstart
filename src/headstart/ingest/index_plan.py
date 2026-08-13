"""Pure planners for the search index: what to add, evict, and prune (ADR-0014, ADR-0023).

Everything here computes row sets without touching LanceDB, so the scoping invariants stay
unit-testable on CI's base-deps-only install. :mod:`headstart.ingest.index` is the CLI that opens
the table and executes these plans; ``apply_sync`` is the one mutation helper, shared by its
``sync`` and ``prune`` steps.

Two layers, because they reach different rows:

**Freshness sync** (ADR-0014) — after a scrape, an index row is stale iff its Board was scraped this
run but its id is absent from the fresh output. New ids are added; re-seen ids are left untouched
(id-only, v1). Crucially the delete is *scoped to the Boards actually scraped*, so a partial harvest
never evicts Boards it didn't touch — and a dead Board (scraped, yields nothing) has all its rows
drop out for free. That scope is per-Board but all-or-nothing, so a Board whose scrape came back
*truncated* still looks fully covered; the **collapse guard** (ADR-0046) withholds a Board's
evictions when it would lose more than a quarter of its rows at once.

**Prune sweep** (ADR-0023) — because the sync is board-scoped it can't reach rows on Boards that left
the scrape list, nor case-variant duplicates of one job (Workday sites like ``.../External`` vs
``.../external``). These planners compute what to drop in those two cases.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headstart.config import load_active_companies
from headstart.corpus import board_of
from headstart.scrapers.registry import get_scraper


# A Board losing more than this share of its indexed rows in one run is presumed truncated, not
# delisted; below the floor a large ratio is a handful of rows, which is ordinary churn.
COLLAPSE_RATIO = 0.25
COLLAPSE_FLOOR = 20


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Ids to add to the index and ids to evict from it, plus the Boards the guard held back.

    ``held`` pairs a Board key with the number of evictions withheld from it — not its row count.
    """

    add: frozenset[str]
    delete: frozenset[str]
    held: tuple[tuple[str, int], ...] = ()


def plan_sync(
    index_ids: Iterable[str],
    fresh_ids: Iterable[str],
    scraped_boards: Iterable[str],
) -> SyncPlan:
    """Diff the current index against a scrape's fresh ids, scoped to the Boards it covered.

    ``add`` is the fresh ids not already indexed. ``delete`` is the indexed ids whose Board was
    scraped this run yet are missing from ``fresh_ids`` — a posting that has closed. An indexed id on
    a Board *not* in ``scraped_boards`` is never touched (the partial-harvest safety), and a re-seen
    id (present in both) is left as-is (id-only change detection).

    **Collapse guard (ADR-0046).** The board-scope check above is all-or-nothing at the *line*
    level: a Board that emitted one job line is fully in scope, so a scrape truncated by a
    rate-limit looks exactly like a Board that delisted everything it didn't re-emit. When a Board
    would lose more than :data:`COLLAPSE_RATIO` of its indexed rows in a single run, this refuses
    the whole Board's evictions and reports it in ``held`` for the caller to log. Measured over
    five production runs, ordinary turnover is under 10% for 754 of 817 board-runs, while the
    Boards that flapped sat at 35–82% — so the threshold separates them with room to spare.
    Boards under :data:`COLLAPSE_FLOOR` rows are exempt: there a large ratio is a handful of rows.

    The guard is a stopgap for the blast radius, not the cause, and it is deliberately blunt: a
    Board that genuinely sheds more than a quarter of its postings at once keeps those rows until
    the scrape reports per-Board outcomes and the sync can scope on them instead.
    """
    index = set(index_ids)
    fresh = set(fresh_ids)
    boards = set(scraped_boards)
    add = fresh - index

    indexed_by_board: dict[str, set[str]] = defaultdict(set)
    for job_id in index:
        board = board_of(job_id)
        if board in boards:
            indexed_by_board[board].add(job_id)

    delete: set[str] = set()
    held: list[tuple[str, int]] = []
    for board, ids in indexed_by_board.items():
        missing = {i for i in ids if i not in fresh}
        if len(ids) >= COLLAPSE_FLOOR and len(missing) > COLLAPSE_RATIO * len(ids):
            held.append((board, len(missing)))
            continue
        delete |= missing

    return SyncPlan(
        add=frozenset(add), delete=frozenset(delete), held=tuple(sorted(held))
    )


def _quote(value: str) -> str:
    return (
        "'" + value.replace("'", "''") + "'"
    )  # SQL string literal for the delete predicate


def apply_sync(
    table: Any,
    add_rows: list[dict],
    delete_ids: Iterable[str],
    *,
    chunk: int = 512,
) -> None:
    """Execute a plan on a LanceDB ``table``: delete evicted ids by predicate, then add new rows.

    Deletes are chunked so the ``id IN (...)`` predicate can't grow unbounded. ``add_rows`` must
    already carry the table's schema (vector + metadata); embedding them is the caller's job.
    """
    ids = list(delete_ids)
    for start in range(0, len(ids), chunk):
        batch = ids[start : start + chunk]
        table.delete(f"id IN ({', '.join(_quote(i) for i in batch)})")
    if add_rows:
        table.add(add_rows)


def live_keep_set(ledger_dir: str | Path) -> set[str]:
    """Board keys that should survive: every live ledger Board on an enabled ATS, in the index's
    ``board_of`` key space (via ``board_key()``). ``load_active_companies`` already drops dead
    Boards and ``DISABLED_ATS``; ``min_jobs=0`` keeps currently-empty live Boards.

    Kept in the ledger's **own casing**, not lowercased: :func:`plan_prune` matches Boards
    case-insensitively but needs the exact live casing to pick which duplicate row to keep."""
    keep: set[str] = set()
    for company in load_active_companies(ledger_dir, min_jobs=0):
        try:
            keep.add(get_scraper(company.ats, company.slug, company.name).board_key())
        except Exception:  # noqa: BLE001 - a malformed ledger row shouldn't sink the whole set
            continue
    return keep


def plan_prune(index_ids: Iterable[str], keep: set[str]) -> tuple[list[str], list[str]]:
    """Split index ids into ``(evict_off_board, evict_duplicate)``.

    ``evict_off_board``: Board not in ``keep`` (dead / dropped from the ledger / disabled ATS).
    ``evict_duplicate``: among the survivors, every id but one per ``(lowercased Board, native id)``
    group — the case-variant dupes of one job.

    The row kept is the one whose Board casing the **live ledger** produces, because that is the
    casing a future scrape emits. Keeping the lexicographically-smallest instead (the rule until
    2026-08-11) preserved whichever casing happened to sort first, which is often a *fossil* — a row
    under a casing nothing scrapes any more. Sync cannot evict a fossil (its Board is absent from
    ``scraped_boards``, so the partial-harvest guard protects it), so the fresh row was deleted as
    the fossil's duplicate on every run, forever, while the fossil itself went stale and immortal.
    Falls back to lex-min when no row carries the live casing (the group is all fossils)."""
    live: dict[str, str] = {}
    for board in sorted(keep):  # sorted so a caller's set order can't change the plan
        live.setdefault(board.lower(), board)
    off_board: list[str] = []
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for jid in index_ids:
        canon = board_of(jid).lower()
        if canon not in live:
            off_board.append(jid)
            continue
        native = jid.rsplit(":", 1)[1]
        groups[(canon, native)].append(jid)
    duplicate: list[str] = []
    for (canon, _), ids in groups.items():
        if len(ids) > 1:
            kept = next((i for i in ids if board_of(i) == live[canon]), sorted(ids)[0])
            duplicate.extend(i for i in ids if i != kept)
    return off_board, duplicate
