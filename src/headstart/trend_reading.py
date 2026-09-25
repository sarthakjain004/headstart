"""**Line reading** (ADR-0232): every figure the Trends tab shows, whole and reconciled.

:func:`read_trends` answers a question with a :class:`TrendReading`. Each line in it carries its
start and latest openings, its hiring move, and its "Not hiring" split into named causes, so that
``latest − start == hiring + Σ not_hiring`` exactly. The window carries its Marked changes, each
sized on every company line, and one day marker per day. :func:`read_company_moves` gives Hot the
same company lines. :func:`check_reading` states the invariants on the served JSON, and the
page's ``checkReading`` states the same ones in JavaScript.

The netting rule is ``trend_netting``'s, used here as the private implementation: a company's
line is netted exactly as ``net_answer`` nets it, so its hiring figure is today's. What this
module adds is the split of the rest, read off how ``trend_netting._net`` took each step out
(its ``_NetTrace``), pair of runs by pair of runs:

- a run a step lands on gives up its ``withheld`` openings, at their own size, never scaled: a
  duplicate removal's share of the line, ``(1 − r) ×`` the line's level before it, with ``r`` the
  company's ``(served jobs before − removed) / served jobs before``; a Found Board's openings on a
  whole company line; the rest to the Counting change (with its settling run and its week-later
  echo) or Found Board that lands there;
- growth the scaling after a run takes out of it is its own cause, "growth counted twice before
  the removal", so a removal has one size in every window that holds it;
- a pick counted from a later date joins a summed line with the openings it arrives with.

Every count is rounded once, by largest remainder, so a line's causes sum to its "Not hiring",
and a breakdown's rows (with one closing row where they fall short) sum to its first row.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from itertools import pairwise

from headstart import trend_netting
from headstart.trend_netting import (
    _TOTAL,
    _birth_note,
    _count_jumps,
    _hiring_turnover,
    _is_whole,
    _Line,
    _line_company,
    _line_notes,
    _net,
    _NetTrace,
    _summed_picks,
    _View,
    _viewed,
    js_round,
)

# The kinds of change a line's "Not hiring" is made of (ADR-0232 decision 3).
COUNTING = "counting"
FOUND_BOARDS = "found_boards"
DUPLICATES_REMOVED = "duplicates_removed"
GROWTH_COUNTED_TWICE = "growth_counted_twice"
PICK_JOINED = "pick_joined"
# Growth before a counting change scaled away where shifting the change out would have taken
# the line below zero (the erase guard, ADR-0185 round 13): its own cause, as growth counted
# twice is, so the change keeps one size in every window.
GROWTH_SCALED_BY_A_CHANGE = "growth_scaled_by_a_change"
CHANGE_KINDS = (
    COUNTING,
    FOUND_BOARDS,
    DUPLICATES_REMOVED,
    GROWTH_COUNTED_TWICE,
    GROWTH_SCALED_BY_A_CHANGE,
    PICK_JOINED,
)
# Not hiring the reading could not name: it never should be, and check_reading says so.
UNEXPLAINED = "unexplained"

# A line's percentage is withheld below this many openings at its start, or over a window of
# under MIN_SPAN_DAYS: the page's MOVER_FLOOR and MIN_SPAN_DAYS.
MOVER_FLOOR = 20
MIN_SPAN_DAYS = 3

# Amounts under this are float noise, not openings.
_NOISE = 1e-6

# The causes a window sizes: growth a scaling took out depends on how much growth the window
# holds before it. Every other change has one size wherever it is held.
_SCALED_GROWTH = ("growth_counted_twice@", "growth_scaled_by_")


# ---- the reading -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Turnover:
    """The jobs a line opened and closed over the runs its hiring move counts (ADR-0227)."""

    opened: int
    closed: int


@dataclass(frozen=True)
class Cause:
    """One part of a line's "Not hiring": the Marked change it is, and how many openings."""

    change: str
    size: int


@dataclass(frozen=True)
class Share:
    """A line as a share of its denominator, at the window's start and now. The start is the
    netted count over the netted denominator, each netted once (ADR-0232 decision 2)."""

    start: float | None
    latest: float | None
    denominator_start: int | None
    denominator_latest: int | None


@dataclass(frozen=True)
class LineMove:
    """What one line reports over the window. ``latest − start == hiring + Σ not_hiring``.
    ``percent`` is ``hiring`` over the netted start, or None with ``percent_withheld`` saying
    why. ``turnover`` is None where no run in the window counted it; a counted 0 stays 0."""

    start: int
    latest: int
    hiring: int
    not_hiring: tuple[Cause, ...]
    percent: float | None
    percent_withheld: str | None
    turnover: Turnover | None
    share: Share | None = None


@dataclass(frozen=True)
class LineReading:
    """One line: a category, a level, a company, a tracked role, or the first row (``total``).
    ``estimated`` when it takes its company's duplicate removal by the company's ratio: the
    history does not record a removed row's category (ADR-0232 decision 5)."""

    name: str
    label: str
    move: LineMove
    estimated: bool = False


@dataclass(frozen=True)
class MarkedChange:
    """A change marked in the window, sized on each company line it moved (``sizes``)."""

    id: str
    kind: str
    ts: str
    fields: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    company: str | None = None
    boards: int | None = None
    sizes: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DayMarker:
    """One marker per day: drawn at the run ``at`` where the lines moved most, listing every
    change that landed that day."""

    day: str
    at: str
    changes: tuple[str, ...]


@dataclass(frozen=True)
class TrendReading:
    """Every figure the Trends tab shows for one question (ADR-0232 decision 1).

    ``total`` is the first row: every line added together, netted as a whole. ``lines`` are the
    answer's series in its order. ``company_lines`` are the lines Marked changes are sized on:
    each picked company's own line, or inside a drill its part of the category. ``closing`` is
    the breakdown's closing row, the openings a counting change moved between categories that
    the rows took out and the first row did not; ``breakdown`` says whether ``lines`` add up to
    ``total`` at all. A reading that fails :func:`check_reading` is still served, with
    ``violations`` (ADR-0232 decision 6)."""

    window: tuple[str, str] | None
    picked: bool
    total: LineReading | None
    lines: tuple[LineReading, ...]
    company_lines: tuple[LineReading, ...]
    breakdown: bool
    closing: LineMove | None
    marked_changes: tuple[MarkedChange, ...]
    day_markers: tuple[DayMarker, ...]
    violations: tuple[str, ...] = ()

    @property
    def reconciles(self) -> bool:
        return not self.violations

    def to_json(self) -> dict:
        def move(m: LineMove | None) -> dict | None:
            if m is None:
                return None
            out = asdict(m)
            out["not_hiring"] = [asdict(c) for c in m.not_hiring]
            return out

        def line(r: LineReading | None) -> dict | None:
            if r is None:
                return None
            return {
                "name": r.name,
                "label": r.label,
                "estimated": r.estimated,
                "move": move(r.move),
            }

        return {
            "window": {"from": self.window[0], "to": self.window[1]}
            if self.window
            else None,
            "picked": self.picked,
            "total": line(self.total),
            "lines": [line(r) for r in self.lines],
            "company_lines": [line(r) for r in self.company_lines],
            "breakdown": {"closing": move(self.closing)} if self.breakdown else None,
            "marked_changes": [
                {
                    "id": c.id,
                    "kind": c.kind,
                    "ts": c.ts,
                    "fields": list(c.fields),
                    "changed": list(c.changed),
                    "company": c.company,
                    "boards": c.boards,
                    "sizes": dict(c.sizes),
                }
                for c in self.marked_changes
            ],
            "day_markers": [
                {"day": d.day, "at": d.at, "changes": list(d.changes)}
                for d in self.day_markers
            ],
            "reconciles": self.reconciles,
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class TrendWindow:
    """The runs a reading covers: ``since`` and ``until`` as ``TrendQuestion`` takes them."""

    since: str | None = None
    until: str | None = None


# ---- entry points ----------------------------------------------------------------------------


def read_trends(history, question) -> TrendReading:
    """The reading of ``question`` (a ``trend_history.TrendQuestion``) over ``history`` (a
    ``trend_history.TrendHistory``)."""
    return read_answer(history.unnetted_answer(question))


def read_company_moves(
    history, window: TrendWindow, keys: Iterable[str]
) -> dict[str, LineMove]:
    """Hot's figures: each company's own line over ``window``, the move the trend its "See
    trend" link opens reads (ADR-0232 decision 1). A company with nothing counted in the window
    is left out."""
    from headstart.trend_history import TrendQuestion

    moves = {}
    for key in keys:
        reading = read_trends(
            history,
            TrendQuestion(companies=(key,), since=window.since, until=window.until),
        )
        if reading.company_lines:
            moves[key] = reading.company_lines[0].move
    return moves


def read_answer(answer: dict) -> TrendReading:
    """The reading of one answer as ``TrendHistory.unnetted_answer`` builds it. Pure."""
    answer, view = _viewed(answer)
    stamps = view.stamps
    if not stamps or not answer["series"]:
        reading = TrendReading(
            window=(stamps[0], stamps[-1]) if stamps else None,
            picked=view.picked,
            total=None,
            lines=(),
            company_lines=(),
            breakdown=False,
            closing=None,
            marked_changes=(),
            day_markers=(),
        )
        return replace(reading, violations=tuple(check_reading(reading.to_json())))
    reader = _Reader(answer, view)
    return reader.read()


# ---- reading one answer ----------------------------------------------------------------------


@dataclass
class _Exact:
    """A line's figures before rounding: ``start + hiring + Σ causes == latest``."""

    start: int
    latest: int
    hiring: float
    causes: dict[str, float]
    origin: int  # the run its start is read at
    first: int  # the run the line's own counting starts at
    estimated: bool = False


class _Reader:
    """Reads every line of one answer in one view."""

    def __init__(self, answer: dict, view: _View) -> None:
        self.answer = answer
        self.view = view
        self.stamps = view.stamps
        self.notes = view.notes
        self.split_by = answer.get("split_by")
        self.roles = bool(answer.get("family")) and self.split_by == "family"
        self.company_totals = answer.get("company_totals") or {}
        self.labels = {c["key"]: c["label"] for c in answer.get("companies") or []}
        self._denominators: dict[str | None, list] = {}
        # every change named on any line, with what the page needs to say it
        self.changes: dict[str, MarkedChange] = {}

    # -- lines --

    def read(self) -> TrendReading:
        view, answer = self.view, self.answer
        series = [
            _Line(
                line["name"],
                line["points"],
                turnover=line.get("turnover"),
                denominators=self.company_totals.get(line["name"])
                if view.split_company
                else None,
            )
            for line in answer["series"]
        ]
        parts = [
            line.get("turnover") for line in answer["series"] if line.get("turnover")
        ]
        stamps = self.stamps
        total_line = _Line(
            _TOTAL,
            trend_netting._sum_points([line.points for line in series], len(stamps)),
            turnover={
                metric: trend_netting._sum_points(
                    [part[metric] for part in parts], len(stamps)
                )
                for metric in ("opened", "closed", "recounted")
            }
            if parts
            else None,
        )
        origin = next(
            (j for j, v in enumerate(total_line.points) if v is not None), None
        )
        # Rows of a breakdown start where the first row does, 0 where they were not counted
        # yet, so their starts and latests add up to the first row's.
        breakdown = view.picked and not view.split_company and not self.roles
        total_exact = self._exact(total_line, origin)
        rows_exact = [
            self._exact(line, origin if breakdown else None) for line in series
        ]
        total_ints = _whole(total_exact)
        rows_ints = [_whole(exact) for exact in rows_exact]
        closing_ints = None
        if breakdown and total_exact is not None:
            closing_ints = self._reconcile_rows(
                total_exact, total_ints, rows_exact, rows_ints
            )
        # A Company breakdown has no first row: its lines are companies netted each on its own,
        # and the whole netted at once is a second figure nothing shows.
        total = (
            None
            if view.split_company
            else self._reading(total_line, "", total_exact, total_ints)
        )
        rows = [
            self._reading(line, s["label"], exact, ints)
            for line, s, exact, ints in zip(
                series, answer["series"], rows_exact, rows_ints
            )
        ]
        closing = (
            self._closing(total, rows, closing_ints)
            if breakdown and total is not None
            else None
        )
        company = self._company_lines(total, rows, origin)
        marked = self._marked_changes(company)
        reading = TrendReading(
            window=(stamps[0], stamps[-1]),
            picked=view.picked,
            total=total,
            lines=tuple(r for r in rows if r is not None),
            company_lines=tuple(company),
            breakdown=breakdown,
            closing=closing,
            marked_changes=marked,
            day_markers=self._day_markers(marked, series),
        )
        return replace(reading, violations=tuple(check_reading(reading.to_json())))

    def _company_lines(
        self,
        total: LineReading | None,
        rows: list[LineReading | None],
        origin: int | None,
    ) -> list[LineReading]:
        """The lines Marked changes are sized on (ADR-0232, "company totals only"): each line of
        a Company breakdown, the tracked roles (there is no company line there), each pick's
        own line where several are summed, else the first row."""
        view = self.view
        if not view.picked:
            return []
        if view.split_company or self.roles:
            return [r for r in rows if r is not None]
        if len(view.pick_series) > 1:
            out = []
            for key, points in view.pick_series.items():
                line = _Line(key, points, True, view.pick_turnover.get(key))
                exact = self._exact(line, origin)
                reading = self._reading(
                    line, self._company_label(key), exact, _whole(exact)
                )
                if reading is not None:
                    out.append(reading)
            return out
        if total is None:
            return []
        key = view.company_keys[0]
        label = self._company_label(key)
        if view.drilled:
            label = (
                f"{label}, {self.answer.get('family_label') or self.answer['family']}"
            )
        return [replace(total, name=key, label=label)]

    def _company_label(self, key: str) -> str:
        return self.labels.get(key, key)

    def _exact(self, line: _Line, origin: int | None) -> _Exact | None:
        """``line``'s figures before rounding, from ``origin`` (the first row's first run, for a
        row of a breakdown; else the line's own first run)."""
        view = self.view
        own = next((j for j, v in enumerate(line.points) if v is not None), None)
        if own is None:
            return None
        if origin is None or origin > own:
            origin = own
        parts = _summed_picks(view, line)
        if parts:
            pieces = [p for part in parts if (p := self._exact(part, origin))]
            if not pieces:
                return None
            causes: dict[str, float] = defaultdict(float)
            for piece in pieces:
                for change, size in piece.causes.items():
                    causes[change] += size
            return _Exact(
                start=sum(p.start for p in pieces),
                latest=sum(p.latest for p in pieces),
                hiring=sum(p.hiring for p in pieces),
                causes=dict(causes),
                origin=origin,
                first=min(p.first for p in pieces),
                estimated=any(p.estimated for p in pieces),
            )
        points = line.points
        measured = [j for j, v in enumerate(points) if v is not None]
        first, last = measured[0], measured[-1]
        trace = _NetTrace()
        net = _net(view, points, line, None, True, trace=trace)
        kept = [j for j in measured if net[j] is not None]
        stock = view.metric == "stock"
        # A stock line with no count at the latest run reads 0 there (the page's latestOf).
        emptied = stock and last < len(points) - 1
        split = _Split(self, line, trace, points)
        start = points[origin] if origin == first else 0
        latest = 0 if emptied else points[last]
        hiring = 0.0
        # Arriving after the first row began: a pick joining a summed line, a category sorted
        # in by a counting change, or (neither) a category first seen, which is hiring.
        if origin < first:
            arrival = points[first]
            by = split.arrival_change(first)
            withheld = arrival if by else 0
            if by:
                split.add(by, arrival)
            if first == kept[0]:
                scale = trace.scale.get(first, 1)
                hiring += scale * (arrival - withheld)
                split.scaled(
                    arrival - withheld - scale * (arrival - withheld), first, None
                )
            else:
                split.add(split.cut_change(kept[0]), arrival - withheld)
        # A step bigger than the history before it starts the netted line after it.
        if kept[0] > first:
            c = kept[0]
            before = max(j for j in measured if j < c)
            withheld = trace.withheld.get(c, 0.0)
            split.run(c, before, withheld)
            split.add(split.cut_change(c), points[c] - points[first] - withheld)
        hiring += net[kept[-1]] - net[kept[0]]
        for a, b in pairwise(kept):
            gave_up = (points[b] - points[a]) - (net[b] - net[a])
            withheld = trace.withheld.get(b, 0.0)
            split.run(b, a, withheld)
            split.scaled(gave_up - withheld, a, b)
        if emptied:
            change = split.pending_after(last)
            if change:
                split.add(change, -points[last])
            else:
                hiring -= points[last]
        causes = {c: v for c, v in split.causes.items() if abs(v) > _NOISE}
        left = latest - start - hiring - sum(causes.values())
        if abs(left) > _NOISE:
            change = self.unexplained("residual", line.name, len(self.stamps) - 1)
            causes[change] = causes.get(change, 0) + left
        return _Exact(
            start=start,
            latest=latest,
            hiring=hiring,
            causes=causes,
            origin=origin,
            first=first,
            estimated=split.estimated,
        )

    def _reading(
        self,
        line: _Line,
        label: str,
        exact: _Exact | None,
        ints: dict[str, int] | None,
    ) -> LineReading | None:
        if exact is None:
            return None
        causes = {c: n for c, n in ints.items() if c != "hiring"}
        return LineReading(
            name=line.name,
            label=label,
            move=self._move(line, exact, ints["hiring"], causes),
            estimated=exact.estimated,
        )

    def _move(
        self, line: _Line, exact: _Exact, hiring: int, causes: dict[str, int]
    ) -> LineMove:
        view = self.view
        netted_start = exact.latest - hiring
        percent, withheld = None, None
        span = (
            datetime.fromisoformat(self.stamps[-1])
            - datetime.fromisoformat(self.stamps[exact.first])
        ).total_seconds() / 86400
        if span < MIN_SPAN_DAYS:
            withheld = f"a window under {MIN_SPAN_DAYS} days"
        elif exact.start < MOVER_FLOOR:
            withheld = f"under {MOVER_FLOOR} openings at the start"
        elif netted_start <= 0:
            withheld = "no openings at the start once the steps are taken out"
        else:
            percent = hiring / netted_start * 100
        turnover = _hiring_turnover(view, line)
        return LineMove(
            start=exact.start,
            latest=exact.latest,
            hiring=hiring,
            not_hiring=tuple(Cause(c, n) for c, n in causes.items() if n),
            percent=percent,
            percent_withheld=withheld,
            turnover=Turnover(turnover["opened"], turnover["closed"])
            if turnover
            else None,
            share=self._share(line, exact, netted_start),
        )

    def _share(self, line: _Line, exact: _Exact, netted_start: int) -> Share | None:
        """The line over its denominator: the netted count over the netted denominator at the
        start, the counts as counted now. The denominator is every served job in scope (a
        company's own, on a Company breakdown), netted once by the same notes, its duplicate
        removals by the removed count itself, which it includes."""
        raw = line.denominators or self.view.totals
        if not raw:
            return None
        netted = self._netted_denominator(line)
        den_start = netted[exact.origin]
        den_start = js_round(den_start) if den_start is not None else None
        den_latest = raw[-1]
        return Share(
            start=netted_start / den_start * 100 if den_start else None,
            latest=exact.latest / den_latest * 100 if den_latest else None,
            denominator_start=den_start,
            denominator_latest=den_latest,
        )

    def _netted_denominator(self, line: _Line) -> list:
        key = line.name if line.denominators else None
        if key in self._denominators:
            return self._denominators[key]
        view = self.view
        raw = line.denominators or view.totals
        if not view.picked:
            netted = list(raw)
        else:
            # The denominator is every served job of the picks, so it is a whole company's
            # line whatever the drill: its removals lift and scale by the count they came from.
            several = not line.denominators and len(view.company_totals) > 1
            whole = replace(
                view,
                drilled=False,
                pick_series=view.company_totals if several else {},
                pick_parts={},
                _jump_cache={},
            )
            whose = (
                _Line(line.name, raw, pick=True)
                if line.denominators
                else _Line(_TOTAL, raw)
            )
            netted = _net(whole, raw, whose, None, True)
        self._denominators[key] = netted
        return netted

    # -- the breakdown's closing row --

    def _reconcile_rows(
        self,
        total_exact: _Exact,
        total_ints: dict[str, int],
        rows_exact: list[_Exact | None],
        rows_ints: list[dict[str, int] | None],
    ) -> dict[str, int]:
        """The rows' whole numbers moved, in place, so each column (hiring, and each cause)
        adds up to the first row's; returns the closing row's (decision 4), what the first row
        holds that the rows do not."""
        present = [k for k, exact in enumerate(rows_exact) if exact is not None]
        columns = [
            "hiring",
            *sorted(
                {c for k in present for c in rows_exact[k].causes}
                | set(total_exact.causes)
            ),
        ]
        exact_rows = [
            {"hiring": rows_exact[k].hiring, **rows_exact[k].causes} for k in present
        ]
        int_rows = [rows_ints[k] for k in present]
        closing_exact = {
            c: ({"hiring": total_exact.hiring, **total_exact.causes}).get(c, 0)
            - sum(x.get(c, 0) for x in exact_rows)
            for c in columns
        }
        # The closing row takes openings only where the rows fall a whole one short.
        closing_ints = {c: 0 for c in columns}
        if any(abs(v) >= 0.5 for v in closing_exact.values()):
            closing_ints = _whole_of(closing_exact, 0)
            exact_rows.append(closing_exact)
            int_rows.append(closing_ints)
        residual = {
            c: total_ints.get(c, 0) - sum(r.get(c, 0) for r in int_rows)
            for c in columns
        }
        # Move a whole opening from one column to another inside one row, where that row's
        # rounding was furthest from exact, until every column adds up: each row keeps its own
        # sum, so each still satisfies latest − start = hiring + not hiring.
        while any(v > 0 for v in residual.values()) and any(
            v < 0 for v in residual.values()
        ):
            up = next(c for c in columns if residual[c] > 0)
            down = next(c for c in columns if residual[c] < 0)
            k = max(
                range(len(int_rows)),
                key=lambda k: (
                    (exact_rows[k].get(up, 0) - int_rows[k].get(up, 0))
                    - (exact_rows[k].get(down, 0) - int_rows[k].get(down, 0))
                ),
            )
            int_rows[k][up] = int_rows[k].get(up, 0) + 1
            int_rows[k][down] = int_rows[k].get(down, 0) - 1
            residual[up] -= 1
            residual[down] += 1
        return closing_ints

    def _closing(
        self,
        total: LineReading,
        rows: list[LineReading | None],
        ints: dict[str, int] | None,
    ) -> LineMove | None:
        """The closing row, where the rows do not reach the first row: its openings, and the
        jobs opened and closed on runs the first row counts and the rows leave out."""
        ints = ints or {"hiring": 0}
        turnover = None
        if total.move.turnover:
            counted = [r.move.turnover for r in rows if r and r.move.turnover]
            turnover = Turnover(
                total.move.turnover.opened - sum(t.opened for t in counted),
                total.move.turnover.closed - sum(t.closed for t in counted),
            )
        if not any(ints.values()) and not (
            turnover and (turnover.opened or turnover.closed)
        ):
            return None
        return LineMove(
            start=0,
            latest=0,
            hiring=ints["hiring"],
            not_hiring=tuple(
                Cause(c, n) for c, n in ints.items() if c != "hiring" and n
            ),
            percent=None,
            percent_withheld="a closing row has no start",
            turnover=turnover,
        )

    # -- changes --

    def _change(self, change_id: str, kind: str, ts: str, **facts) -> None:
        if change_id not in self.changes:
            self.changes[change_id] = MarkedChange(
                id=change_id, kind=kind, ts=ts, **facts
            )

    def note_change(self, k: int) -> str:
        """The change note ``k`` belongs to: a settling run and a week-later echo belong to
        their Counting change."""
        n = self.notes[k]
        stamps = self.stamps
        if n["evicted"]:
            return self.removal(n["company"], n["i"])
        if n["join"]:
            change = f"joined@{n['company']}"
            self._change(change, PICK_JOINED, stamps[n["i"]], company=n["company"])
            return change
        if n["found"]:
            change = f"found@{stamps[n['i']]}/{n['company']}"
            self._change(
                change,
                FOUND_BOARDS,
                stamps[n["i"]],
                company=n["company"],
                boards=n["boards"],
            )
            return change
        if n["settle"]:
            k = next(
                m
                for m in range(k - 1, -1, -1)
                if self.notes[m]["epoch"]
                and not self.notes[m]["echo"]
                and self.notes[m]["i"] == n["i"] - 1
            )
            n = self.notes[k]
        source = n["source"] or stamps[n["i"]]
        change = f"counting@{source}"
        self._change(
            change,
            COUNTING,
            source,
            fields=tuple(n["fields"]),
            changed=tuple(n["changed"]),
        )
        return change

    def unexplained(self, reason: str, line: str, j: int) -> str:
        change = f"{UNEXPLAINED}@{reason}/{line}"
        self._change(change, UNEXPLAINED, self.stamps[j])
        return change

    def growth_scaled_by(self, change: str) -> str:
        scaled = f"growth_scaled_by_{change}"
        self._change(scaled, GROWTH_SCALED_BY_A_CHANGE, self.changes[change].ts)
        return scaled

    def removal(self, company: str, j: int) -> str:
        change = f"removed@{self.stamps[j]}/{company}"
        self._change(change, DUPLICATES_REMOVED, self.stamps[j], company=company)
        return change

    def growth_counted_twice(self, company: str, j: int) -> str:
        change = f"growth_counted_twice@{self.stamps[j]}/{company}"
        self._change(change, GROWTH_COUNTED_TWICE, self.stamps[j], company=company)
        return change

    def _marked_changes(self, company: list[LineReading]) -> tuple[MarkedChange, ...]:
        """Under a pick, every change with a size on a company line, sized on each; with no
        pick every Counting change in the window, marked and sized on nothing (decision 6)."""
        if not self.view.picked:
            for k, n in enumerate(self.notes):
                if n["epoch"]:
                    self.note_change(k)
            listed = [c for c in self.changes.values() if c.kind == COUNTING]
        else:
            sizes: dict[str, list[tuple[str, int]]] = defaultdict(list)
            for line in company:
                for cause in line.move.not_hiring:
                    sizes[cause.change].append((line.name, cause.size))
            listed = [
                replace(self.changes[c], sizes=tuple(s)) for c, s in sizes.items()
            ]
        return tuple(sorted(listed, key=lambda c: (c.ts, c.id)))

    def _day_markers(
        self, marked: tuple[MarkedChange, ...], series: list[_Line]
    ) -> tuple[DayMarker, ...]:
        """One marker per day, at the day's run where the lines moved most, over every line and
        not just those drawn; with no pick nothing is taken out, so the run's own change."""
        view, stamps = self.view, self.stamps
        listed = {c.id for c in marked}
        runs_of: dict[str, set[int]] = defaultdict(set)
        for k, n in enumerate(self.notes):
            if n["settle"]:
                continue
            change = self.note_change(k)
            if change in listed:
                runs_of[change].add(n["i"])
        for c in marked:  # a growth counted twice stands at its removal's run
            if c.kind == GROWTH_COUNTED_TWICE and c.ts in stamps:
                runs_of[c.id].add(stamps.index(c.ts))
        jumps = [_count_jumps(view, line) for line in series] if view.picked else None

        def moved_at(i: int) -> float:
            if i < 1:
                return 0
            if jumps is not None:
                return sum(abs(j[i].after - j[i].before) for j in jumps if i in j)
            return sum(
                abs(line.points[i] - line.points[i - 1])
                for line in series
                if line.points[i] is not None and line.points[i - 1] is not None
            )

        days: dict[str, dict] = {}
        for c in marked:
            for i in sorted(runs_of.get(c.id, ())):
                day = days.setdefault(stamps[i][:10], {"at": i, "changes": []})
                if moved_at(i) > moved_at(day["at"]):
                    day["at"] = i
                if c.id not in day["changes"]:
                    day["changes"].append(c.id)
        return tuple(
            DayMarker(day, stamps[d["at"]], tuple(d["changes"]))
            for day, d in sorted(days.items())
        )


class _Split:
    """Splits one line's "Not hiring" into its causes, run by run, as ``_net`` took it out."""

    def __init__(self, reader: _Reader, line: _Line, trace: _NetTrace, points) -> None:
        self.reader = reader
        self.view = reader.view
        self.line = line
        self.trace = trace
        self.points = points
        self.whole = _is_whole(self.view, line)
        self.company = _line_company(self.view, line)
        self.steps = _line_notes(self.view, line)
        self.causes: dict[str, float] = defaultdict(float)
        self.estimated = False

    def add(self, change: str, size: float) -> None:
        if abs(size) > _NOISE:
            self.causes[change] += size

    def run(self, b: int, a: int, withheld: float) -> None:
        """What run ``b`` gave up (``withheld``), measured from run ``a``: a removal's share of
        the line, then the known sizes of Found Boards on a whole company line, then the rest to
        the change that owns the run."""
        notes = self.reader.notes
        jump = self.trace.jumps.get(b)
        landing = list(jump.notes) if jump else []
        rest = withheld
        ratio = self.trace.ratios.get(b)
        if ratio and ratio[1] == "removal":
            before = jump.before if jump else self.points[a]
            share = (ratio[0] - 1) * before
            self.add(self.reader.removal(self.company, b), share)
            rest -= share
            if not self.whole:
                self.estimated = True
        if self.whole:
            for k in landing:
                n = notes[k]
                if n["size"] is None or (n["evicted"] and ratio):
                    continue
                self.add(self.reader.note_change(k), n["size"])
                rest -= n["size"]
        if abs(rest) <= _NOISE:
            return
        owner = self._owner(landing)
        if owner is None:
            self.add(self.reader.unexplained("unowned run", self.line.name, b), rest)
            return
        self.add(self.reader.note_change(owner), rest)

    def _owner(self, landing: list[int]) -> int | None:
        """The note a run's unsized jump belongs to, as ``_change_size`` gives it: a change of
        unknown size landing there (a new change over a week-later echo, else the first
        listed), else a settling run, else on a category a Found Board."""
        notes = self.reader.notes
        claims = [
            k for k in landing if not notes[k]["settle"] and notes[k]["size"] is None
        ]
        fresh = [k for k in claims if not notes[k]["echo"]]
        if fresh or claims:
            return (fresh or claims)[0]
        settles = [k for k in landing if notes[k]["settle"]]
        if settles:
            return settles[0]
        found = [k for k in landing if notes[k]["found"] and not self.whole]
        return found[0] if found else None

    def scaled(self, amount: float, after: int, landing: int | None) -> None:
        """``amount`` of the growth after run ``after`` that a later scaling took out: split
        among those scalings, each by its own share of it."""
        if abs(amount) <= _NOISE:
            return
        ops = sorted(
            (
                (j, r, kind)
                for j, (r, kind) in self.trace.ratios.items()
                if j > after and j != landing
            ),
            reverse=True,
        )
        kept = 1.0
        weights = []
        for j, r, kind in ops:
            weights.append((j, kind, kept * (1 - r)))
            kept *= r
        if abs(1 - kept) <= _NOISE:
            self.add(
                self.reader.unexplained("unscaled growth", self.line.name, after),
                amount,
            )
            return
        for j, kind, weight in weights:
            share = amount * weight / (1 - kept)
            if kind == "removal":
                self.add(self.reader.growth_counted_twice(self.company, j), share)
            else:
                self.add(self._growth_scaled_by(j), share)

    def _growth_scaled_by(self, j: int) -> str:
        """The growth a counting change scaled away where shifting it out would have taken the
        line below zero: its own cause, beside the change's own size at its runs."""
        jump = self.trace.jumps.get(j)
        owner = self._owner(list(jump.notes) if jump else [])
        if owner is None:
            return self.reader.unexplained("unowned scaling", self.line.name, j)
        return self.reader.growth_scaled_by(self.reader.note_change(owner))

    def arrival_change(self, first: int) -> str | None:
        """What a line arriving after the first row began arrived by: a pick joining a summed
        line, or a counting change sorting openings into a category; None for hiring."""
        notes = self.reader.notes
        joiner = self.line.name if self.line.pick else self.line.company
        if joiner:
            # A pick's part of a summed line is held at its first value before it (`_net`),
            # so its arrival is its joining, marked or not.
            for k, n in enumerate(notes):
                if n["join"] and n["company"] == joiner:
                    return self.reader.note_change(k)
            change = f"joined@{joiner}"
            self.reader._change(
                change, PICK_JOINED, self.reader.stamps[first], company=joiner
            )
            return change
        if self.view.metric == "stock":
            k = _birth_note(self.view, self.line)
            if k is not None:
                return self.reader.note_change(k)
        return None

    def pending_after(self, last: int) -> str | None:
        """The change of unknown size landing after the line's last count: a stock line a
        counting change emptied reads 0 by that change, not by closures."""
        notes = self.reader.notes
        landing = [k for k in self.steps if notes[k]["i"] > last]
        owner = self._owner(landing)
        return self.reader.note_change(owner) if owner is not None else None

    def cut_change(self, c: int) -> str:
        """Where a step was bigger than the history before it could hold and could not scale,
        the line starts after it (run ``c``, ``_net``'s cut): the growth before is scaled to
        nothing by the change that made the step, the nearest one landing at or after ``c``."""
        at = min((j for j in self.trace.withheld if j >= c), default=None)
        if at is None:
            return self.reader.unexplained("cut", self.line.name, c)
        ratio = self.trace.ratios.get(at)
        if ratio and ratio[1] == "removal":
            return self.reader.growth_counted_twice(self.company, at)
        return self._growth_scaled_by(at)


# ---- rounding --------------------------------------------------------------------------------


def _apportion(values: list[float], target: int) -> list[int]:
    """``values`` as whole numbers summing to ``target``, the largest remainders rounded up
    (Hamilton's method): each its floor or ceiling when ``target`` is their rounded sum, and
    moved by whole openings, the nearest first, when it is not."""
    out = [math.floor(v) for v in values]
    left = target - sum(out)
    while left and values:
        step = 1 if left > 0 else -1
        order = sorted(
            range(len(values)), key=lambda k: (-step * (values[k] - out[k]), k)
        )
        for k in order[: min(abs(left), len(values))]:
            out[k] += step
            left -= step
    return out


def _whole(exact: _Exact | None) -> dict[str, int] | None:
    """A line's hiring and causes as whole numbers summing to latest − start: hiring rounded as
    ``net_answer``'s figure is, then the causes apportioned to what is left. A change keeps its
    own rounding where the line has growth a scaling took out, which is sized by the window
    anyway and takes the remainders, so a removal reads one size in every window."""
    if exact is None:
        return None
    if not exact.causes:
        return {"hiring": exact.latest - exact.start}
    hiring = js_round(exact.hiring)
    left = exact.latest - exact.start - hiring
    scaled = [c for c in exact.causes if c.startswith(_SCALED_GROWTH)]
    if not scaled:
        return _whole_of({"hiring": exact.hiring, **exact.causes}, left + hiring)
    own = {
        c: js_round(v)
        for c, v in exact.causes.items()
        if not c.startswith(_SCALED_GROWTH)
    }
    sizes = _apportion([exact.causes[c] for c in scaled], left - sum(own.values()))
    return {"hiring": hiring, **own, **dict(zip(scaled, sizes))}


def _whole_of(exact: dict[str, float], total: int) -> dict[str, int]:
    """``exact`` (hiring and causes) as whole numbers summing to ``total``."""
    hiring = js_round(exact.get("hiring", 0))
    changes = [c for c in exact if c != "hiring"]
    sizes = _apportion([exact[c] for c in changes], total - hiring)
    return {"hiring": hiring, **dict(zip(changes, sizes))}


# ---- the invariants --------------------------------------------------------------------------


def check_reading(reading: dict) -> list[str]:
    """Every way ``reading`` (``TrendReading.to_json()``) breaks ADR-0232's invariants, as
    sentences; empty when it reconciles. The page's ``checkReading`` states the same ones.

    1. For every line: latest − start == hiring + Σ not_hiring.
    2. A company line's "Not hiring" is its Marked changes: each cause is listed at that size,
       and every size listed for it is one of its causes.
    3. A breakdown's rows (with its closing row, which starts and ends at 0) add up to its first
       row, field by field.
    4. (A change's size is the same in every window: stated by the tests, over narrower
       windows, since one reading holds one window.)
    5. Share is the netted count over the netted denominator, and the percentage is hiring over
       the netted start: neither is netted a second time.
    6. With no pick nothing is taken out.
    Plus: every count is a whole number, and no change is one the reading could not name."""
    out: list[str] = []
    changes = {c["id"]: c for c in reading.get("marked_changes") or []}
    lines = [
        ("first row", reading.get("total")),
        *((f"line {r['name']}", r) for r in reading.get("lines") or []),
        *((f"company line {r['name']}", r) for r in reading.get("company_lines") or []),
    ]
    moves = [(where, r["move"]) for where, r in lines if r]
    breakdown = reading.get("breakdown")
    if breakdown and breakdown.get("closing"):
        moves.append(("closing row", breakdown["closing"]))
    for where, m in moves:
        counts = [
            m["start"],
            m["latest"],
            m["hiring"],
            *(c["size"] for c in m["not_hiring"]),
        ]
        if m["turnover"]:
            counts += [m["turnover"]["opened"], m["turnover"]["closed"]]
        if any(not isinstance(n, int) or isinstance(n, bool) for n in counts):
            out.append(f"{where}: a count is not a whole number")
            continue
        named = sum(c["size"] for c in m["not_hiring"])
        if m["latest"] - m["start"] != m["hiring"] + named:
            out.append(
                f"{where}: latest − start is {m['latest'] - m['start']}, "
                f"hiring + not hiring is {m['hiring'] + named}"
            )
        for c in m["not_hiring"]:
            if c["change"].startswith(f"{UNEXPLAINED}@"):
                out.append(
                    f"{where}: {c['size']} openings of not hiring have no named cause"
                )
        share = m.get("share")
        if share:
            netted_start = m["latest"] - m["hiring"]
            den = share["denominator_start"]
            want = netted_start / den * 100 if den else None
            if not _same(share["start"], want):
                out.append(
                    f"{where}: its share at the start is not its netted count over the netted denominator"
                )
            den = share["denominator_latest"]
            want = m["latest"] / den * 100 if den else None
            if not _same(share["latest"], want):
                out.append(
                    f"{where}: its latest share is not its count over the denominator"
                )
        if m["percent"] is not None:
            netted_start = m["latest"] - m["hiring"]
            if netted_start <= 0 or not _same(
                m["percent"], m["hiring"] / netted_start * 100
            ):
                out.append(
                    f"{where}: its percentage is not hiring over the netted start"
                )
        if not reading.get("picked") and (
            m["not_hiring"] or m["hiring"] != m["latest"] - m["start"]
        ):
            out.append(f"{where}: with no pick, something was taken out")
    if reading.get("picked"):
        for line in reading.get("company_lines") or []:
            name = line["name"]
            causes = {c["change"]: c["size"] for c in line["move"]["not_hiring"]}
            listed = {
                cid: c["sizes"][name]
                for cid, c in changes.items()
                if name in c["sizes"]
            }
            if causes != listed:
                out.append(
                    f"company line {name}: its Not hiring is not its Marked changes"
                )
    if breakdown and reading.get("total"):
        total = reading["total"]["move"]
        closing = breakdown.get("closing")
        rows = [r["move"] for r in reading.get("lines") or []]
        if closing:
            if closing["start"] or closing["latest"]:
                out.append("closing row: it does not start and end at 0")
            rows.append(closing)

        def fields(m: dict) -> dict:
            f = {"start": m["start"], "latest": m["latest"], "hiring": m["hiring"]}
            for c in m["not_hiring"]:
                f[c["change"]] = f.get(c["change"], 0) + c["size"]
            if m["turnover"]:
                f["opened"] = m["turnover"]["opened"]
                f["closed"] = m["turnover"]["closed"]
            return f

        want = fields(total)
        summed: dict[str, int] = defaultdict(int)
        for m in rows:
            for k, v in fields(m).items():
                summed[k] += v
        for k in sorted(set(want) | set(summed)):
            if k in ("opened", "closed") and not total["turnover"]:
                continue
            if want.get(k, 0) != summed.get(k, 0):
                out.append(
                    f"breakdown: its rows' {k} add up to {summed.get(k, 0)}, "
                    f"its first row's is {want.get(k, 0)}"
                )
    return out


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
