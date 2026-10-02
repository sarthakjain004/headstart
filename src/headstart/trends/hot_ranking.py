"""Rank the companies hiring hardest right now, for the Hot tab ("Hiring now", ADR-0171).

A row is a **Company directory** entry, never a single Board (ADR-0230): Bosch Group ranked
third at +442 from one of its two Boards while the trend its row opened summed both. Ranked at
Space boot from the same history the Trends tab answers from, so it can never be stale against
the ticks the Space loaded, and its figures are :func:`line_reading.read_company_moves`, each
company's own line read by the same code as the trend a row opens (ADR-0233). A row's net
change is therefore the hiring its "See trend" reads from the window's base, by construction
rather than by a mirrored rule.

## Four lenses, because "actively hiring" is more than one question

``expansion``: the net change in tech openings over the trailing week, with the steps that are
not hiring (counting changes, found Boards, duplicate removals) netted out. *Who is actually
growing.* Over the 7 days to 2026-09-21 Amazon opened **1,396** roles at a net change of
**+20**: churn at a near-constant size, which only this lens says.

``opened_less_closed``: the jobs **Opened** less the jobs **Closed** over the same runs, only for
a company whose closures were counted on every Board of it (ADR-0321). *Who opened more postings
than it closed*, with no re-counting in the figure at all. Expansion's net still holds re-counting
the netting could not size: Bosch Group led it at +442 on 23 opened and 33 closed.

``volume``: the jobs **Opened** over the same runs (ADR-0227). *Where the most opportunity is
right now.* Always led by the largest employers.

``rate``: the jobs Opened as a share of the company's openings now, among companies that grew.
*Who is moving fast for their size*, the only lens that surfaces a small company a user would
never otherwise find.

## What is left out, and counted rather than silently applied

- **A company below ``MIN_STOCK`` openings.** One posting on a three-posting company is a 33%
  rate and pure noise, and the Rate lens would become a list of tiny companies that posted once.
- **A company counted for under ``MIN_COUNTED_DAYS``.** Its trend reads "too new to show a
  direction yet", so a row for it would state a direction its own link will not. A company with
  nothing counted in the window has no line to read, and is counted with these.
- **A Board no directory entry holds.** The directory names only companies someone can name
  (ADR-0212), so such a Board cannot be a row.
- **From Rate, a company whose closures were not counted** (#835). Its jobs Opened cannot be
  told from the same jobs listed again, so its rate measures churn rather than hiring. New York
  Life led Rate on 2026-09-28 at 2,016%: 504 opened against 25 open now, at a net change of −47.
  The other Lenses still rank it, and its row there says its closures were not counted.
- **From Rate, a company whose net change was 0 or less** (ADR-0309, option 1 of #835). What it
  opened only replaced what closed, so its rate measures churn rather than growth. On 2026-09-29
  30 of Rate's 100 rows were such churn, CSB second at 60% on a net change of 0 and Bluelight
  Consulting ninth at 41% on −101. Expansion already ranks only a net change above 0.
- **From Opened less closed, a company whose closures went uncounted on any of its Boards.** Its
  closed count is then low, and its opened less closed high by as much.

A found Board inside the window needs no rule here: its backlog is a step the history nets out
of the company's change, as the trend does.

## Opened that was posted long before HeadStart saw it (ADR-0351)

A company's jobs **Opened** are ids new on a Board already counted, and some of them were posted
weeks earlier: a Board read again after a gap, postings listed again under new ids, a scraper or
filter change that let old postings in. Starbucks stood third on Opened less closed on 2026-09-29
on 50 opened; 28 of its postings first seen that week were posted more than 14 days before
HeadStart first saw them. So each row also says, from the served postings first seen in the
week's ticks with turnover, how many were posted within ``found_late.FOUND_LATE_DAYS`` of first sight
(``opened_fresh``, undated ones included) and how many longer before (``opened_found_late``),
counted by :mod:`headstart.trends.found_late`, which ``/trends`` counts its company lines by too
(ADR-0369).
Both are None where the Space could not read the postings. They count postings still served, so
a posting opened and closed inside the window is in neither; the reader judges them against
``opened``. Opened itself is unchanged: redefining it by posted date belongs to the restated
Trends (ADR-0330, step 5).

Staffing firms and job boards are ranked like any company, and the payload's
``hidden_by_default`` (``HIDDEN_BY_DEFAULT``) names them as the Operators the tab hides unless
asked (ADR-0238), never IT services, which employ the people they post for. The page, and any
other reader of ``/hot``, hides by that one list. A row's ``operator_unverified`` says it is an
employer only because no list names it, while its name reads like an agency's
(``board_operator.unverified``, ADR-0335).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from headstart.boards import board_operator
from headstart.boards.board_identity import ats_of
from headstart.trends import found_late, line_reading

if TYPE_CHECKING:
    from headstart.boards.board_operator import Operator
    from headstart.trends.trend_history import TrendHistory

#: Rows kept per lens. Enough to scroll, small enough that the companies on it can be adjudicated
#: by hand, the stated way to extend `boards.board_operator` beyond its curated head.
TOP_N = 100

#: A company below this many tech openings is not ranked (see the module docstring).
MIN_STOCK = 25

#: A company counted for fewer days than this is too new to rank. It mirrors app.js
#: MIN_SPAN_DAYS, under which the trend a row opens reads "too new to show a direction yet".
MIN_COUNTED_DAYS = 3

#: The Operators the tab hides unless asked, in the order its "hidden" note names them (ADR-0238).
HIDDEN_BY_DEFAULT: tuple[Operator, ...] = ("staffing", "aggregator")


def rank(
    history: TrendHistory,
    directory: Mapping[str, Mapping[str, Any]],
    postings: found_late.FirstSeenPostings | None = None,
) -> dict[str, Any]:
    """The ``/hot`` payload: ``{window, lenses, counts, hidden_by_default}``, or ``{}`` when
    there is no measured window yet, which keeps the tab dark rather than ranking nothing.

    ``history`` is a :class:`headstart.trends.trend_history.TrendHistory`, read through
    :meth:`~headstart.trends.trend_history.TrendHistory.openings`,
    :meth:`~headstart.trends.trend_history.TrendHistory.trailing_week` and
    :func:`headstart.trends.line_reading.read_company_moves`. ``directory`` is the Company
    directory, ``{company key: {name, boards, operator}}``. ``postings`` is the served
    postings first seen since turnover began, or None where they could not be read.

    Every candidate company is scored once and every lens sorts the same rows, so a company
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
        # None where either the turnover or the postings went unread (ADR-0351).
        fresh, late = (
            (None, None)
            if opened is None or postings is None or not window["turnover_from"]
            else postings.split(entry["boards"], window["turnover_from"], newest)
        )
        candidates.append(
            {
                "key": key,
                "company": entry["name"],
                "boards": list(entry["boards"]),
                "atses": sorted({ats_of(board) for board in entry["boards"]}),
                "operator": entry["operator"],
                # An employer only by default, named like an agency (ADR-0335).
                "operator_unverified": board_operator.unverified(
                    entry["boards"], entry["name"]
                ),
                "stock": open_now,
                "net": move.hiring,
                "opened": opened,
                "closed": closed,
                # Where some of its Boards' closures went uncounted, how many of how many: its
                # closed count is then theirs only. The page no longer prints the fraction; a
                # note under the list says closed counts can be low (ADR-0255).
                "closures_uncounted_boards": company.closures_uncounted_boards,
                "boards_in_scope": company.boards_in_scope,
                # Percent rather than a fraction: it is a display value, and rounding it here
                # keeps every consumer from inventing its own precision. None, as closed is,
                # where the company's turnover or its closures were not counted.
                "rate": None if closed is None else round(100 * opened / open_now),
                # None unless every Board's closures were counted: a partial closed count
                # makes this figure high by what it missed.
                "opened_less_closed": None
                if closed is None or company.closures_uncounted_boards
                else opened - closed,
                # Of its served postings first seen after the week's first tick with turnover
                # and by its last, those posted within found_late.FOUND_LATE_DAYS of first sight
                # (or undated), and those posted longer before.
                "opened_fresh": fresh,
                "opened_found_late": late,
            }
        )
    # Ties break on the key, so the same history always ranks the same list.
    lenses = {
        "expansion": _top(candidates, "net"),
        "opened_less_closed": _top(candidates, "opened_less_closed"),
        "volume": _top(candidates, "opened"),
        "rate": _top([row for row in candidates if row["net"] > 0], "rate"),
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
        # Likewise the days `opened_found_late` counts past (ADR-0351).
        "found_late_days": found_late.FOUND_LATE_DAYS,
        "unnamed": sum(
            1
            for board, n in openings.items()
            if n >= MIN_STOCK and board not in in_directory
        ),
        # Left out of Rate and Opened less closed: they opened jobs, but their closures were
        # not counted.
        "closures_uncounted": sum(
            1 for row in candidates if row["opened"] and row["closed"] is None
        ),
        # Left out of Opened less closed only: their closures went uncounted on some Board.
        "closures_partly_uncounted": sum(
            1
            for row in candidates
            if row["closed"] is not None and row["closures_uncounted_boards"]
        ),
        # Left out of Rate too, apart from those: they have a rate, but their net change was 0
        # or less.
        "not_growing": sum(1 for row in candidates if row["rate"] and row["net"] <= 0),
        "services": operators["services"],
        "staffing": operators["staffing"],
        "aggregator": operators["aggregator"],
    }
    return {
        "window": window,
        "lenses": lenses,
        "counts": counts,
        "hidden_by_default": list(HIDDEN_BY_DEFAULT),
    }


def _top(candidates: list[dict[str, Any]], figure: str) -> list[dict[str, Any]]:
    """The ``TOP_N`` candidates with a positive ``figure``, largest first; a figure that was
    not counted (None) ranks nowhere."""
    positive = [row for row in candidates if (row[figure] or 0) > 0]
    return sorted(positive, key=lambda row: (-row[figure], row["key"]))[:TOP_N]
