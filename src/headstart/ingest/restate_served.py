"""Decide when each Job version counted, under today's rules: the second step of a Restatement
(ADR-0330).

A Restatement answers what Trends would have shown had today's rules always applied, so a rule
applies to the whole past alike:

* **the keep-set**: a version on a Board outside today's keep-set (parked, excluded, aliased,
  disabled, dead) never counts, so removing a Board removes it from the past too;
* **the tech filter**: a version whose raw fields today's filter rejects never counts;
* **the grace period** (ADR-0083): a Job the scrape stopped returning still counts until its
  Board's next authoritative read, or until it is listed again if that comes first, as ``index
  sync`` serves it. A version that ended ``changed`` or ``off_board`` stops counting when it ended;
* **Dormant Boards** (ADR-0250): while every Job listed on a Board is dated and the newest is more
  than two years older than the run, none of its Jobs counts. Judged over every listed Job, tech
  or not, as ``scrape_join`` judges it, and at every run, since a Board turns Dormant as time
  passes with nothing scraped. An interval Dormancy cuts ends ``dormant``; one that resumes when
  the Board posts again starts ``revived``.

Each counted version becomes a **served interval**, ``[served_from, served_to)`` in runs, with
``served_to`` None while it still counts. Duplicate groups apply to these intervals in the step
after this one.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, date, datetime, timedelta

from headstart.boards.board_identity import lower_key
from headstart.ingest import board_dormancy
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


def _dormant_from(newest: str, runs: list[str], since: str) -> str | None:
    """The first run at or after ``since`` that judges a Board whose newest posting is ``newest``
    Dormant, as ``PostedDates.dormant`` judges it on the run's day."""
    first_day = (
        date.fromisoformat(newest) + board_dormancy.DORMANT_AFTER + timedelta(days=1)
    )
    threshold = max(
        since,
        datetime(
            first_day.year, first_day.month, first_day.day, tzinfo=UTC
        ).isoformat(),
    )
    at = bisect_left(runs, threshold)
    return runs[at] if at < len(runs) else None


def dormant_periods(
    versions, runs: list[str], live: Mapping[str, str]
) -> dict[str, list[tuple[str, str | None]]]:
    """``{case-folded Board: [(from run, to run or None)]}`` for every stretch today's Dormant rule
    held, judged over every version listed on the Board at each run."""
    by_board: dict[str, list[tuple[str, str | None, str, object]]] = {}
    for job_id, vfrom, vto, posted in zip(
        versions["id"].to_pylist(),
        versions["valid_from"].to_pylist(),
        versions["valid_to"].to_pylist(),
        versions["posted_at"].to_pylist(),
        strict=True,
    ):
        board = lower_key(resolve_board(job_id, live))
        by_board.setdefault(board, []).append((vfrom, vto, job_id, posted))

    periods: dict[str, list[tuple[str, str | None]]] = {}
    for board, listed in by_board.items():
        bounds = sorted({v[0] for v in listed} | {v[1] for v in listed if v[1]})
        found: list[tuple[str, str | None]] = []
        for k, start in enumerate(bounds):
            end = bounds[k + 1] if k + 1 < len(bounds) else None
            dates = board_dormancy.PostedDates()
            for vfrom, vto, _, posted in listed:
                if vfrom <= start and (vto is None or vto > start):
                    dates.see(board, posted)
            newest = dates.newest(board)
            if newest is None:
                continue
            onset = _dormant_from(newest, runs, start)
            if onset is not None and (end is None or onset < end):
                if found and found[-1][1] == onset:
                    found[-1] = (found[-1][0], end)
                else:
                    found.append((onset, end))
        if found:
            periods[board] = found
    return periods


def clip_dormant(served, periods: Mapping[str, list[tuple[str, str | None]]]):
    """``served`` with each interval's Dormant stretches cut out: a cut interval ends
    ``dormant``, and the part after a Dormant stretch starts ``revived``."""
    import pyarrow as pa

    rows = served.to_pylist()
    out = []
    for row in rows:
        row.setdefault("starts_as", None)
        pieces = [row]
        for gone_from, gone_to in periods.get(lower_key(row["board"]), []):
            kept = []
            for piece in pieces:
                start, end = piece["served_from"], piece["served_to"]
                if (end is not None and end <= gone_from) or (
                    gone_to is not None and start >= gone_to
                ):
                    kept.append(piece)
                    continue
                if start < gone_from:
                    kept.append(piece | {"served_to": gone_from, "ended_as": "dormant"})
                if gone_to is not None and (end is None or end > gone_to):
                    kept.append(
                        piece | {"served_from": gone_to, "starts_as": "revived"}
                    )
            pieces = kept
        out.extend(pieces)
    if not out:
        return served.slice(0, 0).append_column("starts_as", pa.nulls(0, pa.string()))
    return pa.Table.from_pylist(
        out, schema=served.schema.append(pa.field("starts_as", pa.string()))
    )
