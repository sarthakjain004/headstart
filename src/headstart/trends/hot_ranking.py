"""Rank the companies hiring hardest right now, for the Hot tab ("Hiring now", ADR-0171).

A row is a **Company directory** entry, never a single Board (ADR-0230): Bosch Group ranked
third at +442 from one of its two Boards while the trend its row opened summed both. Ranked at
Space boot from the same history the Trends tab answers from, so it can never be stale against
the ticks the Space loaded, and its figures are :func:`line_reading.read_company_moves`, each
company's own line read by the same code as the trend a row opens (ADR-0233). A row's net
change is therefore the hiring its "See trend" reads from the window's base, by construction
rather than by a mirrored rule.

## Three lenses, because "actively hiring" is three questions

``expansion``: the net change in tech openings over the trailing week, with the steps that are
not hiring (counting changes, found Boards, duplicate removals) netted out. *Who is actually
growing.* Over the 7 days to 2026-09-21 Amazon opened **1,396** roles at a net change of
**+20**: churn at a near-constant size, which only this lens says.

``volume``: the jobs **Opened** over the same runs (ADR-0227). *Where the most opportunity is
right now.* Always led by the largest employers.

``rate``: the jobs Opened as a share of the company's openings now. *Who is moving fast for
their size*, the only lens that surfaces a small company a user would never otherwise find.

## What is left out, and counted rather than silently applied

- **A company below ``MIN_STOCK`` openings.** One posting on a three-posting company is a 33%
  rate and pure noise, and the Rate lens would become a list of tiny companies that posted once.
- **A company counted for under ``MIN_COUNTED_DAYS``.** Its trend reads "too new to show a
  direction yet", so a row for it would state a direction its own link will not. A company with
  nothing counted in the window has no line to read, and is counted with these.
- **A Board no directory entry holds.** The directory names only companies someone can name
  (ADR-0212), so such a Board cannot be a row.

A found Board inside the window needs no rule here: its backlog is a step the history nets out
of the company's change, as the trend does.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from headstart.boards.board_identity import ats_of
from headstart.trends import line_reading

if TYPE_CHECKING:
    from headstart.trends.trend_history import TrendHistory

#: Rows kept per lens. Enough to scroll, small enough that the companies on it can be adjudicated
#: by hand, the stated way to extend `boards.board_operator` beyond its curated head.
TOP_N = 100

#: A company below this many tech openings is not ranked (see the module docstring).
MIN_STOCK = 25

#: A company counted for fewer days than this is too new to rank. It mirrors app.js
#: MIN_SPAN_DAYS, under which the trend a row opens reads "too new to show a direction yet".
MIN_COUNTED_DAYS = 3


def rank(
    history: TrendHistory, directory: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The ``/hot`` payload: ``{window, lenses, counts}``, or ``{}`` when there is no measured
    window yet, which keeps the tab dark rather than ranking nothing.

    ``history`` is a :class:`headstart.trends.trend_history.TrendHistory`, read through
    :meth:`~headstart.trends.trend_history.TrendHistory.openings`,
    :meth:`~headstart.trends.trend_history.TrendHistory.trailing_week` and
    :func:`headstart.trends.line_reading.read_company_moves`. ``directory`` is the Company
    directory, ``{company key: {name, boards, operator}}``.

    Every candidate company is scored once and the three lenses sort the same rows, so a company
    cannot appear as an employer on one lens and a services firm on another.
    """
    openings = history.openings()
    stock = {
        key: sum(openings.get(board, 0) for board in entry["boards"])
        for key, entry in directory.items()
    }
    ranked = {key: n for key, n in stock.items() if n >= MIN_STOCK}
    window = history.trailing_week()
    newest = window["to"]
    if not newest:
        return {}
    moves = line_reading.read_company_moves(
        history, line_reading.TrendWindow(since=window["base"]), list(ranked)
    )
    too_new_since = (
        datetime.fromisoformat(newest) - timedelta(days=MIN_COUNTED_DAYS)
    ).isoformat(timespec="seconds")
    candidates, too_new = [], 0
    for key, open_now in ranked.items():
        company = moves.get(key)
        # Left out by the reading where nothing of it was counted in the window: like a company
        # whose counting only just began, it has no week to rank. Looked up as if present, one
        # such company would darken the whole tab.
        if company is None or company.counted_since > too_new_since:
            too_new += 1
            continue
        move, entry = company.move, directory[key]
        # None where the company's turnover was not counted: a 0 there stated a week nobody
        # measured.
        opened = move.turnover.opened if move.turnover else None
        closed = move.turnover.closed if move.turnover else None
        candidates.append(
            {
                "key": key,
                "company": entry["name"],
                "boards": list(entry["boards"]),
                "atses": sorted({ats_of(board) for board in entry["boards"]}),
                "operator": entry["operator"],
                "stock": open_now,
                "net": move.hiring,
                "opened": opened,
                "closed": closed,
                # Where some of its Boards' closures went uncounted, how many of how many: its
                # closed count is then theirs only, and the row says so.
                "closures_uncounted_boards": company.closures_uncounted_boards,
                "boards_in_scope": company.boards_in_scope,
                # Percent rather than a fraction: it is a display value, and rounding it here
                # keeps every consumer from inventing its own precision. None, as opened is,
                # where the company's turnover was not counted.
                "rate": None if opened is None else round(100 * opened / open_now),
            }
        )
    # Ties break on the key, so the same history always ranks the same list.
    lenses = {
        "expansion": _top(candidates, "net"),
        "volume": _top(candidates, "opened"),
        "rate": _top(candidates, "rate"),
    }
    shown = {row["key"]: row for lens in lenses.values() for row in lens}
    operators = Counter(row["operator"] for row in shown.values())
    in_directory = {board for entry in directory.values() for board in entry["boards"]}
    counts = {
        "ranked": len(candidates),
        "too_new": too_new,
        "below_min_stock": sum(1 for n in stock.values() if 0 < n < MIN_STOCK),
        # The threshold travels with the counts it explains. The UI prints "fewer than N open
        # roles", and with N hardcoded there, changing MIN_STOCK would leave that sentence
        # quietly stating a number the ranking no longer uses.
        "min_stock": MIN_STOCK,
        "unnamed": sum(
            1
            for board, n in openings.items()
            if n >= MIN_STOCK and board not in in_directory
        ),
        "services": operators["services"],
        "staffing": operators["staffing"],
        "aggregator": operators["aggregator"],
    }
    return {"window": window, "lenses": lenses, "counts": counts}


def _top(candidates: list[dict[str, Any]], figure: str) -> list[dict[str, Any]]:
    """The ``TOP_N`` candidates with a positive ``figure``, largest first; a figure that was
    not counted (None) ranks nowhere."""
    positive = [row for row in candidates if (row[figure] or 0) > 0]
    return sorted(positive, key=lambda row: (-row[figure], row["key"]))[:TOP_N]
