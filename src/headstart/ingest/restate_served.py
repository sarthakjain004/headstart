"""Decide when each Job version counted, under today's rules: the second step of a Restatement
(ADR-0330).

A Restatement answers what Trends would have shown had today's rules always applied, so a rule
applies to the whole past alike:

* **the keep-set**: a version on a Board outside today's keep-set (parked, excluded, aliased,
  disabled, dead) never counts, so removing a Board removes it from the past too;
* **the tech filter**: a version whose raw fields today's filter rejects never counts;
* **the grace period** (ADR-0083): a Job the scrape stopped returning still counts until its
  Board's next authoritative read, or until it is listed again if that comes first, as ``index
  sync`` serves it. A version that ended ``changed`` or ``off_board`` stops counting when it ended.

Each counted version becomes a **served interval**, ``[served_from, served_to)`` in runs, with
``served_to`` None while it still counts. Dormant Boards and duplicate groups apply to these
intervals in the steps after this one.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping

from headstart.boards.board_identity import lower_key
from headstart.ingest.index_plan import resolve_board

#: How a version's raw fields read as tech: ``tech_filter.is_tech`` in production.
TechTest = Callable[[str | None, str | None], bool]


def _in_scope_reads(reads) -> dict[str, list[str]]:
    """``{case-folded Board: the runs, in order, whose read of it was authoritative}``."""
    by_board: dict[str, list[str]] = {}
    if reads is None:
        return by_board
    for board, run, in_scope in zip(
        reads["board"].to_pylist(),
        reads["run"].to_pylist(),
        reads["in_scope"].to_pylist(),
        strict=True,
    ):
        if board is not None and in_scope:
            by_board.setdefault(lower_key(board), []).append(run)
    for runs in by_board.values():
        runs.sort()
    return by_board


def _next_after(runs: list[str], run: str) -> str | None:
    at = bisect_right(runs, run)
    return runs[at] if at < len(runs) else None


def served_intervals(
    versions,
    reads,
    *,
    is_tech: TechTest,
    live: Mapping[str, str],
    keep_set: Collection[str] | None,
):
    """The versions that count under today's rules, each with ``served_from`` and ``served_to``,
    and its Board re-resolved through ``live`` (``index_plan.boards_by_canon`` of today's
    keep-set). ``keep_set`` holds today's case-folded kept Boards; None keeps every Board, for a
    Restatement without a ledger to trust."""
    import pyarrow as pa

    ids = versions["id"].to_pylist()
    valid_from = versions["valid_from"].to_pylist()
    valid_to = versions["valid_to"].to_pylist()
    ended_as = versions["ended_as"].to_pylist()
    titles = versions["title"].to_pylist()
    departments = versions["department"].to_pylist()

    boards = [resolve_board(job_id, live) for job_id in ids]
    reads_of = _in_scope_reads(reads)
    # The same Job's next version, if any: listed again ends the grace period at once.
    next_from: list[str | None] = [None] * len(ids)
    for i in range(len(ids) - 1):
        if ids[i + 1] == ids[i]:
            next_from[i] = valid_from[i + 1]

    keep = []
    served_to: list[str | None] = []
    for i, board in enumerate(boards):
        if keep_set is not None and lower_key(board) not in keep_set:
            continue
        if not is_tech(titles[i], departments[i]):
            continue
        end = valid_to[i]
        if ended_as[i] == "unlisted":
            second_absence = _next_after(reads_of.get(lower_key(board), []), end)
            candidates = [r for r in (second_absence, next_from[i]) if r is not None]
            end = min(candidates) if candidates else None
        keep.append(i)
        served_to.append(end)

    served = versions.take(pa.array(keep, pa.int64()))
    served = served.set_column(
        served.schema.get_field_index("board"),
        "board",
        pa.array([boards[i] for i in keep], pa.string()),
    )
    return served.append_column("served_from", served["valid_from"]).append_column(
        "served_to", pa.array(served_to, pa.string())
    )
