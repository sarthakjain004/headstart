"""Incremental, board-scoped freshness sync for the search index (ADR-0014).

The freshness rule after a scrape: an index row is stale iff its Board was scraped this run but its
id is absent from the fresh output. New ids are added; re-seen ids are left untouched (id-only, v1).
Crucially the delete is *scoped to the Boards actually scraped*, so a partial harvest never evicts
Boards it didn't touch — and a dead Board (scraped, yields nothing) has all its rows drop out for free.

:func:`plan_sync` computes the add/delete sets purely — no LanceDB, so the scoping invariant is unit
-testable. :func:`apply_sync` executes a plan against a LanceDB table (delete-by-id, then add).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from headstart.corpus import board_of


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Ids to add to the index and ids to evict from it."""

    add: frozenset[str]
    delete: frozenset[str]


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
    """
    index = set(index_ids)
    fresh = set(fresh_ids)
    boards = set(scraped_boards)
    add = fresh - index
    delete = {i for i in index if i not in fresh and board_of(i) in boards}
    return SyncPlan(add=frozenset(add), delete=frozenset(delete))


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
