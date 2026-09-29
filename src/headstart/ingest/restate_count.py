"""Count served intervals into each tick's Board levels and turnover: the third step of a
Restatement (ADR-0330).

A tick is a run that recorded facts. At each tick, every served interval covering it counts once
under ``(board, stock, family, band)``, and under ``new`` too while its Job was first listed within
the last seven days, the groups ``role_trends`` writes (ADR-0040, ADR-0051). A Job the classifier
places outside tech counts as one ``(stock, non-tech, all)`` row of its Board, as there.

Turnover (ADR-0227) follows from how each interval began and ended, which the facts record, so
nothing is inferred from a diff:

* **Opened**: a Job's interval began, it was not counted the tick before, and its Board had been
  read before this tick;
* **Closed**: an interval ended because its Job was unlisted, with no interval of it following;
* **Recounted**: every other arrival or departure — a found Board's backlog, an off-Board or
  non-tech departure, and a Job whose family or band moved (out of the old key, into the new).

What family and band a version gets is ``place``'s to say: the classifier and the derivations in
production, a stub in tests.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timedelta

from headstart.boards.board_identity import lower_key
from headstart.ingest.job_turnover import (
    CLOSED,
    NOT_SPLIT,
    OPENED,
    RECOUNTED_IN,
    RECOUNTED_OUT,
)
from headstart.trends.role_taxonomy import NON_TECH

#: A version's ``(family, band)``, or None when the classifier places it outside tech.
Place = Callable[[Mapping], tuple[str, str] | None]
Key = tuple[str, str, str, str]  # (board, metric, family, band)

#: How long a Job counts as ``new`` after it was first listed (ADR-0051).
NEW_WINDOW = timedelta(days=7)


def _plus(stamp: str, delta: timedelta) -> str:
    return (datetime.fromisoformat(stamp) + delta).isoformat()


def tick_counts(
    served, runs: list[str], first_reads: Mapping[str, str], place: Place
) -> Iterator[tuple[str, dict[Key, int], Counter[Key]]]:
    """``(run, levels, turnover)`` for each run in ``runs``, oldest first. ``levels`` holds every
    non-zero ``(board, metric, family, band)`` count at the run; ``turnover`` its Opened, Closed
    and Recounted. ``first_reads`` maps a case-folded Board to the first run that read it."""
    rows = sorted(served.to_pylist(), key=lambda r: (r["id"], r["served_from"]))
    groups = [place(row) for row in rows]
    first_listed: dict[str, str] = {}
    for row in rows:
        first_listed.setdefault(row["id"], row["served_from"])

    # Level changes as (when, key, delta), applied at the first run at or after `when`: a Job
    # stops being `new` at a moment that is rarely a run's own stamp.
    changes: list[tuple[str, Key, int]] = []
    events: dict[str, Counter[Key]] = {}

    def event(run: str, board: str, metric: str, group: tuple[str, str]) -> None:
        events.setdefault(run, Counter())[(board, metric, *group)] += 1

    for i, row in enumerate(rows):
        group = groups[i]
        board, start, end = row["board"], row["served_from"], row["served_to"]
        stock = (board, "stock", *(group or (NON_TECH, NOT_SPLIT)))
        changes.append((start, stock, +1))
        if end is not None:
            changes.append((end, stock, -1))
        if group is not None:
            new_until = _plus(first_listed[row["id"]], NEW_WINDOW)
            if start < new_until:
                changes.append((start, (board, "new", *group), +1))
                changes.append(
                    (
                        min(end, new_until) if end else new_until,
                        (board, "new", *group),
                        -1,
                    )
                )

        before = rows[i - 1] if i and rows[i - 1]["id"] == row["id"] else None
        after = (
            rows[i + 1]
            if i + 1 < len(rows) and rows[i + 1]["id"] == row["id"]
            else None
        )
        if before is not None and before["served_to"] == start:
            old = groups[i - 1]
            if old != group:
                if old is not None:
                    event(start, before["board"], RECOUNTED_OUT, old)
                if group is not None:
                    event(start, board, RECOUNTED_IN, group)
        elif group is not None:
            found = first_reads.get(lower_key(board), start) >= start
            event(start, board, RECOUNTED_IN if found else OPENED, group)
        ends_alone = end is not None and not (after and after["served_from"] == end)
        if ends_alone and group is not None:
            metric = CLOSED if row["ended_as"] == "unlisted" else RECOUNTED_OUT
            event(end, board, metric, group)

    changes.sort(key=lambda change: change[0])
    levels: Counter[Key] = Counter()
    applied = 0
    for run in runs:
        while applied < len(changes) and changes[applied][0] <= run:
            _, key, delta = changes[applied]
            levels[key] += delta
            if not levels[key]:
                del levels[key]
            applied += 1
        yield run, dict(levels), events.get(run, Counter())
