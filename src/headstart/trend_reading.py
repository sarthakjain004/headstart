"""**Line reading** (ADR-0233): every figure the Trends tab shows, whole and reconciled.

:func:`read_trends` answers a question with a :class:`TrendReading`. Each line in it carries its
start and latest openings, its hiring move, and its "Not hiring" split into named causes, so that
``latest − start == hiring + Σ not_hiring`` exactly. The window carries its Marked changes, each
sized on every company line, and one day marker per day. :func:`read_company_moves` gives Hot the
same company lines. :func:`check_reading` states the invariants on the served JSON, and the
page's ``checkReading`` states the same ones in JavaScript.

The page only formats and draws (ADR-0233 step 3), so the reading also carries what it draws:
each line's counts with its steps taken out and the runs its steps land on, the first row's
counts, the lines past the page's eighth added together (its Other row), and the share
denominator netted run by run (the dashed line). :func:`trends_payload` is what ``/trends``
serves: the answer as the page draws it, with its reading.

The netting rule is ``trend_netting``'s, used here as the private implementation: a company's
line is netted exactly as ``net_answer`` nets it, so its hiring figure is today's. What this
module adds is the split of the rest, read off how ``trend_netting._net`` took each step out
(its ``_NetTrace``), pair of runs by pair of runs:

- a run a step lands on gives up its ``withheld`` openings, at their own size, never scaled: a
  duplicate removal's share of the line, ``(1 − r) ×`` the line's level before it, with ``r`` the
  company's ``(served jobs before − removed) / served jobs before``; a Found Board's openings on a
  whole company line; the rest to the Counting change (with its settling run and its week-later
  echo) or Found Board that lands there;
- growth a scaling after a run takes out of it is a cause of its own: "growth counted twice
  before the removal", or "growth rescaled by" a counting change the erase guard scaled, so each
  change keeps one size in every window that holds it;
- a pick counted from a later date joins a summed line with the openings it arrives with.

Every count is rounded once, by largest remainder, so a line's causes sum to its "Not hiring",
and a breakdown's rows (with one closing row where they fall short) sum to its first row.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
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


class CauseKind(StrEnum):
    """What a part of a line's "Not hiring" is (ADR-0233 decision 3)."""

    COUNTING = "counting"
    FOUND_BOARDS = "found_boards"
    DUPLICATES_REMOVED = "duplicates_removed"
    GROWTH_COUNTED_TWICE = "growth_counted_twice"
    # Growth before a counting change scaled away where shifting the change out would have
    # taken the line below zero (the erase guard, ADR-0185 round 13).
    GROWTH_SCALED_BY_A_CHANGE = "growth_scaled_by_a_change"
    PICK_JOINED = "pick_joined"
    # Not hiring the reading could not name: it never should be, and check_reading says so.
    UNEXPLAINED = "unexplained"


# The causes a window sizes: growth a scaling took out depends on how much growth the window
# holds before it. Every other change has one size wherever it is held.
_GROWTH = (CauseKind.GROWTH_COUNTED_TWICE, CauseKind.GROWTH_SCALED_BY_A_CHANGE)

# A line's percentage is withheld below this many openings at its start, or over a window of
# under MIN_SPAN_DAYS, and so is its weekly rate under MIN_SPAN_DAYS: the page's MOVER_FLOOR and
# MIN_SPAN_DAYS.
MOVER_FLOOR = 20
MIN_SPAN_DAYS = 3
# Nor is it given off a netted start under this many openings, where it is arithmetic, not a
# reading: 14 openings off a netted 4 read "+350%". The page's INDEX_BASE_FLOOR.
INDEX_BASE_FLOOR = 5

# The lines a page charts, one colour each; the rest fold into one Other row: the page's
# CHART_MAX.
LINES_CHARTED = 8

# The drawn lines' values (``netted``, ``reference``) are a shape, not counts: two decimals.
_DRAWN_DECIMALS = 2

# The Other row's name, as the page names it.
_OTHER = "__other__"

# The answer's pieces the reading nets and the page never reads: each pick's own line, part and
# turnover, the duplicate removals, and the Boards found later.
_READ_BY_THE_READING_ONLY = (
    "pick_series",
    "pick_parts",
    "pick_turnover",
    "evicted",
    "discovered",
)

# The closing row's one cause (decision 4), a counting change's reassignment between categories
# that the rows took out and the first row did not.
MOVED_BETWEEN_CATEGORIES = "moved_between_categories"

# Amounts under this are float noise, not openings.
_NOISE = 1e-6


# ---- the reading -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Turnover:
    """The jobs a line opened and closed over the runs its hiring move counts (ADR-0227), and
    ``net``, opened less closed. It need not equal the line's hiring: a job opened and closed
    between two reads of its Board is in neither count."""

    opened: int
    closed: int
    net: int


@dataclass(frozen=True)
class Cause:
    """One part of a line's "Not hiring": the change it is, its kind, what it was (``label``)
    and its size. A category's causes include changes no company line is sized on, so each
    cause says what it is itself."""

    change: str
    kind: CauseKind
    label: str
    size: int


@dataclass(frozen=True)
class Share:
    """A line as a share of its denominator, at the window's start and now. The start is the
    netted count over the netted denominator, each netted once (ADR-0233 decision 2).
    ``percent`` is the share's own change, latest over start, None where the line's percentage
    is withheld."""

    start: float | None
    latest: float | None
    denominator_start: int | None
    denominator_latest: int | None
    percent: float | None = None


@dataclass(frozen=True)
class LineMove:
    """What one line reports over the window. ``latest − start == hiring + not_hiring_total``,
    and ``not_hiring_total`` is ``Σ not_hiring``. ``percent`` is ``hiring`` over the netted
    start, or None with ``percent_withheld`` saying why. ``span_days`` is how long the line was
    counted in the window, and ``per_week`` its hiring at that rate, None under MIN_SPAN_DAYS.
    ``turnover`` is None where no run in the window counted it; a counted 0 stays 0."""

    start: int
    latest: int
    hiring: int
    not_hiring: tuple[Cause, ...]
    not_hiring_total: int
    percent: float | None
    percent_withheld: str | None
    span_days: float
    per_week: int | None
    turnover: Turnover | None
    share: Share | None = None


@dataclass(frozen=True)
class LineReading:
    """One line: a category, a level, a company, a tracked role, the first row (``total``), or
    the lines the page folds into Other (``other``). ``estimated`` when it takes its company's
    duplicate removal by the company's ratio: the history does not record a removed row's
    category (ADR-0233 decision 5).

    What the page draws of it: ``netted``, its counts run by run with its steps taken out,
    adjusted backwards so the latest stays the real one (the Change plot indexes it); and
    ``steps_at``, the runs a step lands on, where a line drawn in counts or shares breaks.
    ``points`` are its counts run by run where no answer series carries them: the first row's.
    ``arrived_by`` is what a line that began inside the window arrived by, a counting change
    sorting openings into it or a pick joining; None where it began with the window, or arrived
    by hiring."""

    name: str
    label: str
    move: LineMove
    estimated: bool = False
    netted: tuple[float | None, ...] = ()
    steps_at: tuple[int, ...] = ()
    arrived_by: CauseKind | None = None
    points: tuple[int | None, ...] | None = None


@dataclass(frozen=True)
class MarkedChange:
    """A change marked in the window, sized on each company line it moved (``sizes``).
    ``label`` says what it was; ``ts`` is its own run, where its day marker stands."""

    id: str
    kind: CauseKind
    ts: str
    label: str
    fields: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    company: str | None = None
    boards: int | None = None
    sizes: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DayMarker:
    """One marker per day: drawn at the run ``at`` where the lines moved most, naming every
    change that day."""

    day: str
    at: str
    changes: tuple[str, ...]


@dataclass(frozen=True)
class TrendReading:
    """Every figure the Trends tab shows for one question (ADR-0233 decision 1).

    ``total`` is the first row: every line added together, netted as a whole (None on a Company
    breakdown, which has no first row). ``lines`` are the answer's series in its order, and
    ``other`` the lines past the first LINES_CHARTED added together, the page's Other row.
    ``company_lines`` are the lines Marked changes are sized on: each picked company's own line,
    or inside a drill its part of the category. ``closing`` is the breakdown's closing row, the
    openings a counting change moved between categories; ``breakdown`` says whether ``lines``
    add up to ``total`` at all. ``reference`` is the share denominator netted run by run, the
    Change plot's dashed line. A reading that fails :func:`check_reading` is still served,
    with ``violations`` (ADR-0233 decision 6)."""

    window: tuple[str, str] | None
    picked: bool
    total: LineReading | None
    lines: tuple[LineReading, ...]
    company_lines: tuple[LineReading, ...]
    breakdown: bool
    closing: LineMove | None
    marked_changes: tuple[MarkedChange, ...]
    day_markers: tuple[DayMarker, ...]
    other: LineReading | None = None
    reference: tuple[float | None, ...] = ()
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
            out = {
                "name": r.name,
                "label": r.label,
                "estimated": r.estimated,
                "move": move(r.move),
                "netted": list(r.netted),
                "steps_at": list(r.steps_at),
                "arrived_by": r.arrived_by,
            }
            if r.points is not None:
                out["points"] = list(r.points)
            return out

        return {
            "window": {"from": self.window[0], "to": self.window[1]}
            if self.window
            else None,
            "picked": self.picked,
            "total": line(self.total),
            "lines": [line(r) for r in self.lines],
            "other": line(self.other),
            "company_lines": [line(r) for r in self.company_lines],
            "breakdown": {"closing": move(self.closing)} if self.breakdown else None,
            "marked_changes": [
                {
                    "id": c.id,
                    "kind": c.kind,
                    "ts": c.ts,
                    "label": c.label,
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
            "reference": list(self.reference),
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
    trend" link opens reads (ADR-0233 decision 1). A company with nothing counted in the window
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


def trends_payload(answer: dict, reading: TrendReading) -> dict:
    """What ``/trends`` serves (ADR-0233 decision 7): ``answer`` as the page draws it, its
    partial reads dropped, with ``reading``, which holds every figure the page shows. The pieces
    only the reading nets stay off the wire."""
    drawn, _ = _viewed(answer)
    payload = {k: v for k, v in drawn.items() if k not in _READ_BY_THE_READING_ONLY}
    payload["reading"] = reading.to_json()
    return payload


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
    return _Reader(answer, view).read()


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
    arrived_by: CauseKind | None = None


class _Reader:
    """Reads every line of one answer in one view."""

    def __init__(self, answer: dict, view: _View) -> None:
        self.answer = answer
        self.view = view
        self.stamps = view.stamps
        self.notes = view.notes
        # the tracked-roles drill, whose lines re-count their category's jobs and add up to nothing
        self.is_tracked_roles_view = (
            bool(answer.get("family")) and answer.get("split_by") == "family"
        )
        self.company_totals = answer.get("company_totals") or {}
        self.labels = {c["key"]: c["label"] for c in answer.get("companies") or []}
        self._denominators: dict[str | None, list] = {}
        # every change named on any line, and the change a growth cause belongs to
        self.changes: dict[str, MarkedChange] = {}
        self.parent_of: dict[str, str] = {}

    # -- lines --

    def read(self) -> TrendReading:
        view, answer, stamps = self.view, self.answer, self.stamps
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
        breakdown = (
            view.picked and not view.split_company and not self.is_tracked_roles_view
        )
        total_exact = self._exact(total_line, origin)
        rows_exact = [
            self._exact(line, origin if breakdown else None) for line in series
        ]
        total_hiring = self._rounded_hiring(total_exact)
        rows_hiring = [self._rounded_hiring(exact) for exact in rows_exact]
        closing_hiring = 0
        if breakdown and total_exact is not None:
            rows_hiring, closing_hiring = _breakdown_hiring(
                total_exact, total_hiring, rows_exact
            )
        # A Company breakdown has no first row: its lines are companies netted each on its own,
        # and the whole netted at once is a second figure nothing shows.
        total = (
            None
            if view.split_company
            else self._reading(total_line, "", total_exact, total_hiring)
        )
        if total is not None:
            total = replace(total, points=tuple(total_line.points))
        rows = [
            self._reading(line, s["label"], exact, hiring)
            for line, s, exact, hiring in zip(
                series, answer["series"], rows_exact, rows_hiring
            )
        ]
        closing = (
            _closing_row(closing_hiring)
            if breakdown and total is not None and closing_hiring
            else None
        )
        # Marked changes are sized on these; nothing draws them, so they carry no drawing.
        company = [
            replace(r, netted=(), steps_at=(), points=None)
            for r in self._company_lines(total, rows, origin)
        ]
        marked = self._marked_changes(company)
        lines = tuple(r for r in rows if r is not None)
        reading = TrendReading(
            window=(stamps[0], stamps[-1]),
            picked=view.picked,
            total=total,
            lines=lines,
            company_lines=tuple(company),
            breakdown=breakdown,
            closing=closing,
            marked_changes=marked,
            day_markers=self._day_markers(marked, series),
            other=self._other(lines[LINES_CHARTED:]),
            reference=_drawn(self._netted_denominator(total_line)),
        )
        return replace(reading, violations=tuple(check_reading(reading.to_json())))

    def _company_lines(
        self,
        total: LineReading | None,
        rows: list[LineReading | None],
        origin: int | None,
    ) -> list[LineReading]:
        """The lines Marked changes are sized on (ADR-0233, "company totals only"): each line of
        a Company breakdown, the tracked roles (there is no company line there), each pick's
        own line where several are summed, else the first row."""
        view = self.view
        if not view.picked:
            return []
        if view.split_company or self.is_tracked_roles_view:
            return [r for r in rows if r is not None]
        if len(view.pick_series) > 1:
            out = []
            for key, points in view.pick_series.items():
                line = _Line(key, points, True, view.pick_turnover.get(key))
                exact = self._exact(line, origin)
                reading = self._reading(
                    line, self._company_label(key), exact, self._rounded_hiring(exact)
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
                arrived_by=next((p.arrived_by for p in pieces if p.arrived_by), None),
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
        arrived_by = None
        # Arriving after the first row began: a pick joining a summed line, a category sorted
        # in by a counting change, or (neither) a category first seen, which is hiring.
        if origin < first:
            arrival = points[first]
            by = split.arrival_change(first)
            withheld = arrival if by else 0
            if by:
                split.add(by, arrival)
                arrived_by = self.changes[by].kind
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
            change = self.register_unexplained(
                "residual", line.name, len(self.stamps) - 1
            )
            causes[change] = causes.get(change, 0) + left
        return _Exact(
            start=start,
            latest=latest,
            hiring=hiring,
            causes=causes,
            origin=origin,
            first=first,
            estimated=split.estimated,
            arrived_by=arrived_by,
        )

    def _rounded_hiring(self, exact: _Exact | None) -> int | None:
        """A line's hiring in whole openings, rounded as ``net_answer``'s figure is."""
        if exact is None:
            return None
        return js_round(exact.hiring) if exact.causes else exact.latest - exact.start

    def _round_to_openings(self, exact: _Exact, hiring: int) -> dict[str, int]:
        """A line's causes as whole openings summing to latest − start − ``hiring``, largest
        remainder first. Where the line has growth a scaling took out, every other change keeps
        its own rounding and the growth, which the window sizes anyway, takes the remainders,
        so a removal reads one size in every window."""
        left = exact.latest - exact.start - hiring
        growth = [c for c in exact.causes if self.changes[c].kind in _GROWTH]
        if not growth:
            changes = list(exact.causes)
            sizes = _apportion([exact.causes[c] for c in changes], left)
            return dict(zip(changes, sizes))
        own = {c: js_round(v) for c, v in exact.causes.items() if c not in growth}
        sizes = _apportion([exact.causes[c] for c in growth], left - sum(own.values()))
        return {**own, **dict(zip(growth, sizes))}

    def _reading(
        self, line: _Line, label: str, exact: _Exact | None, hiring: int | None
    ) -> LineReading | None:
        if exact is None:
            return None
        return LineReading(
            name=line.name,
            label=label,
            move=self._move(
                line, exact, hiring, self._round_to_openings(exact, hiring)
            ),
            estimated=exact.estimated,
            netted=_drawn(_net(self.view, line.points, line, None, True)),
            steps_at=tuple(sorted(_count_jumps(self.view, line))),
            arrived_by=exact.arrived_by,
        )

    def _move(
        self, line: _Line, exact: _Exact, hiring: int, causes: dict[str, int]
    ) -> LineMove:
        span = (
            datetime.fromisoformat(self.stamps[-1])
            - datetime.fromisoformat(self.stamps[exact.first])
        ).total_seconds() / 86400
        turnover = _hiring_turnover(self.view, line)
        return _line_move(
            start=exact.start,
            latest=exact.latest,
            hiring=hiring,
            not_hiring=tuple(
                Cause(c, self.changes[c].kind, self.changes[c].label, n)
                for c, n in causes.items()
                if n
            ),
            span_days=span,
            turnover=Turnover(
                turnover["opened"],
                turnover["closed"],
                turnover["opened"] - turnover["closed"],
            )
            if turnover
            else None,
            denominators=self._denominators_of(line, exact),
        )

    def _denominators_of(
        self, line: _Line, exact: _Exact
    ) -> tuple[int | None, int | None] | None:
        """The line's share denominator at its start, netted, and now, as counted. The
        denominator is every served job in scope (a company's own, on a Company breakdown),
        netted once by the same notes, its duplicate removals by the removed count itself, which
        it includes. None where the answer has no denominator."""
        raw = line.denominators or self.view.totals
        if not raw:
            return None
        den_start = self._netted_denominator(line)[exact.origin]
        return (js_round(den_start) if den_start is not None else None, raw[-1])

    def _other(self, folded: tuple[LineReading, ...]) -> LineReading | None:
        """The lines the page folds into its Other row, added together. Their whole figures
        are summed, so the table's rows, Other among them, still add up to its first row. A
        Company breakdown's folded lines are each a share of their own company, so Other is a
        share of their companies together; elsewhere every line shares one denominator."""
        if not folded:
            return None
        moves = [r.move for r in folded]
        causes: dict[str, Cause] = {}
        for m in moves:
            for c in m.not_hiring:
                had = causes.get(c.change)
                causes[c.change] = replace(c, size=c.size + had.size) if had else c
        turnovers = [m.turnover for m in moves if m.turnover]
        shares = [m.share for m in moves]
        denominators = None
        if all(shares):
            if self.view.split_company:
                starts = [s.denominator_start for s in shares]
                denominators = (
                    None if None in starts else sum(starts),
                    sum(s.denominator_latest or 0 for s in shares),
                )
            else:
                longest = max(moves, key=lambda m: m.span_days).share
                denominators = (longest.denominator_start, longest.denominator_latest)
        return LineReading(
            name=_OTHER,
            label="",
            move=_line_move(
                start=sum(m.start for m in moves),
                latest=sum(m.latest for m in moves),
                hiring=sum(m.hiring for m in moves),
                not_hiring=tuple(c for c in causes.values() if c.size),
                span_days=max(m.span_days for m in moves),
                turnover=Turnover(
                    sum(t.opened for t in turnovers),
                    sum(t.closed for t in turnovers),
                    sum(t.net for t in turnovers),
                )
                if turnovers
                else None,
                denominators=denominators,
            ),
            estimated=any(r.estimated for r in folded),
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

    # -- changes: each registered once, with what the page needs to say it --

    def _register(
        self, change_id: str, kind: CauseKind, ts: str, label: str, **facts
    ) -> None:
        if change_id not in self.changes:
            self.changes[change_id] = MarkedChange(
                id=change_id, kind=kind, ts=ts, label=label, **facts
            )

    def register_note_change(self, k: int) -> str:
        """The change note ``k`` belongs to, registered: a settling run and a week-later echo
        belong to their Counting change."""
        n = self.notes[k]
        stamps = self.stamps
        if n["evicted"]:
            return self.register_removal(n["company"], n["i"])
        if n["join"]:
            return self.register_joining(n["company"], n["i"])
        if n["found"]:
            change = f"found@{stamps[n['i']]}/{n['company']}"
            boards = n["boards"]
            self._register(
                change,
                CauseKind.FOUND_BOARDS,
                stamps[n["i"]],
                f"{boards} more board{'' if boards == 1 else 's'} found",
                company=n["company"],
                boards=boards,
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
        self._register(
            change,
            CauseKind.COUNTING,
            source,
            ", ".join(n["changed"] or n["fields"]),
            fields=tuple(n["fields"]),
            changed=tuple(n["changed"]),
        )
        return change

    def register_joining(self, company: str, j: int) -> str:
        change = f"joined@{company}"
        self._register(
            change,
            CauseKind.PICK_JOINED,
            self.stamps[j],
            "counting starts",
            company=company,
        )
        return change

    def register_removal(self, company: str, j: int) -> str:
        change = f"removed@{self.stamps[j]}/{company}"
        self._register(
            change,
            CauseKind.DUPLICATES_REMOVED,
            self.stamps[j],
            "duplicate postings removed",
            company=company,
        )
        return change

    def register_growth_counted_twice(self, company: str, j: int) -> str:
        change = f"growth_counted_twice@{self.stamps[j]}/{company}"
        self._register(
            change,
            CauseKind.GROWTH_COUNTED_TWICE,
            self.stamps[j],
            "growth counted twice before the removal",
            company=company,
        )
        self.parent_of[change] = self.register_removal(company, j)
        return change

    def register_growth_rescaled_by(self, change: str) -> str:
        parent = self.changes[change]
        rescaled = f"growth_scaled_by_{change}"
        self._register(
            rescaled,
            CauseKind.GROWTH_SCALED_BY_A_CHANGE,
            parent.ts,
            f"growth rescaled by {parent.label}",
        )
        self.parent_of[rescaled] = change
        return rescaled

    def register_unexplained(self, reason: str, line: str, j: int) -> str:
        change = f"{CauseKind.UNEXPLAINED}@{reason}/{line}"
        self._register(
            change,
            CauseKind.UNEXPLAINED,
            self.stamps[j],
            "not hiring with no named cause",
        )
        return change

    def _marked_changes(self, company: list[LineReading]) -> tuple[MarkedChange, ...]:
        """Under a pick, every change with a size on a company line, sized on each; with no
        pick every Counting change in the window, marked and sized on nothing (decision 6)."""
        if not self.view.picked:
            for k, n in enumerate(self.notes):
                if n["epoch"]:
                    self.register_note_change(k)
            listed = [c for c in self.changes.values() if c.kind == CauseKind.COUNTING]
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
        """One marker per day, naming each Marked change on exactly one day: its own run's, or
        where its own run is before the window, its first run inside it (a week-later echo). A
        growth cause stands with the change it belongs to. The marker is drawn at the day's run
        where the lines moved most, over every line and not just those drawn; with no pick
        nothing is taken out, so the run's own change."""
        view, stamps = self.view, self.stamps
        landing: dict[str, int] = {}
        for k, n in enumerate(self.notes):
            if not n["settle"]:
                change = self.register_note_change(k)
                landing[change] = min(landing.get(change, n["i"]), n["i"])

        def run_of(c: MarkedChange) -> int | None:
            if c.ts in stamps:
                return stamps.index(c.ts)
            return landing.get(c.id, landing.get(self.parent_of.get(c.id, "")))

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
            i = run_of(c)
            if i is None:
                continue
            day = days.setdefault(stamps[i][:10], {"at": i, "changes": []})
            if moved_at(i) > moved_at(day["at"]):
                day["at"] = i
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
        # a whole company's line, which takes out the steps whose size is known per company
        self.is_company_line = _is_whole(self.view, line)
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
        scaling = self.trace.ratios.get(b)
        by_removal = bool(scaling and scaling.by_removal)
        if by_removal:
            before = jump.before if jump else self.points[a]
            share = (scaling.ratio - 1) * before
            self.add(self.reader.register_removal(self.company, b), share)
            rest -= share
            if not self.is_company_line:
                self.estimated = True
        if self.is_company_line:
            for k in landing:
                n = notes[k]
                if n["size"] is None or (n["evicted"] and by_removal):
                    continue
                self.add(self.reader.register_note_change(k), n["size"])
                rest -= n["size"]
        if abs(rest) <= _NOISE:
            return
        owner = self._owner(landing)
        if owner is None:
            self.add(
                self.reader.register_unexplained("unowned run", self.line.name, b), rest
            )
            return
        self.add(self.reader.register_note_change(owner), rest)

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
        found = [k for k in landing if notes[k]["found"] and not self.is_company_line]
        return found[0] if found else None

    def scaled(self, amount: float, after: int, landing: int | None) -> None:
        """``amount`` of the growth after run ``after`` that a later scaling took out: split
        among those scalings, each by its own share of it."""
        if abs(amount) <= _NOISE:
            return
        ops = sorted(
            (
                (j, scaling)
                for j, scaling in self.trace.ratios.items()
                if j > after and j != landing
            ),
            key=lambda op: op[0],
            reverse=True,
        )
        kept = 1.0
        weights = []
        for j, scaling in ops:
            weights.append((j, scaling, kept * (1 - scaling.ratio)))
            kept *= scaling.ratio
        if abs(1 - kept) <= _NOISE:
            self.add(
                self.reader.register_unexplained(
                    "unscaled growth", self.line.name, after
                ),
                amount,
            )
            return
        for j, scaling, weight in weights:
            share = amount * weight / (1 - kept)
            if scaling.by_removal:
                self.add(
                    self.reader.register_growth_counted_twice(self.company, j), share
                )
            else:
                self.add(self._growth_rescaled_by(j), share)

    def _growth_rescaled_by(self, j: int) -> str:
        """The growth a counting change scaled away where shifting it out would have taken the
        line below zero: its own cause, beside the change's own size at its runs."""
        jump = self.trace.jumps.get(j)
        owner = self._owner(list(jump.notes) if jump else [])
        if owner is None:
            return self.reader.register_unexplained(
                "unowned scaling", self.line.name, j
            )
        return self.reader.register_growth_rescaled_by(
            self.reader.register_note_change(owner)
        )

    def arrival_change(self, first: int) -> str | None:
        """What a line arriving after the first row began arrived by: a pick joining a summed
        line, or a counting change sorting openings into a category; None for hiring."""
        joiner = self.line.name if self.line.pick else self.line.company
        if joiner:
            # A pick's part of a summed line is held at its first value before it (`_net`),
            # so its arrival is its joining, marked or not.
            joins = [
                n["i"]
                for n in self.reader.notes
                if n["join"] and n["company"] == joiner
            ]
            return self.reader.register_joining(joiner, joins[0] if joins else first)
        if self.view.metric == "stock":
            k = _birth_note(self.view, self.line)
            if k is not None:
                return self.reader.register_note_change(k)
        return None

    def pending_after(self, last: int) -> str | None:
        """The change of unknown size landing after the line's last count: a stock line a
        counting change emptied reads 0 by that change, not by closures."""
        notes = self.reader.notes
        landing = [k for k in self.steps if notes[k]["i"] > last]
        owner = self._owner(landing)
        return self.reader.register_note_change(owner) if owner is not None else None

    def cut_change(self, c: int) -> str:
        """Where a step was bigger than the history before it could hold and could not scale,
        the line starts after it (run ``c``, ``_net``'s cut): the growth before is scaled to
        nothing by the change that made the step, the nearest one landing at or after ``c``."""
        at = min((j for j in self.trace.withheld if j >= c), default=None)
        if at is None:
            return self.reader.register_unexplained("cut", self.line.name, c)
        scaling = self.trace.ratios.get(at)
        if scaling and scaling.by_removal:
            return self.reader.register_growth_counted_twice(self.company, at)
        return self._growth_rescaled_by(at)


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


def _breakdown_hiring(
    total: _Exact, total_hiring: int, rows: list[_Exact | None]
) -> tuple[list[int | None], int]:
    """A breakdown's hiring in whole openings, rounded once: the closing row's (decision 4),
    the openings the first row holds that the rows do not, rounded; then the rows together, by
    largest remainder, to the rest of the first row's. Rounded one by one, rows reaching the
    first row exactly would still miss it by a remainder, and a closing row of ±1 would stand
    for nothing but rounding. Each row stays its netted figure rounded down or up."""
    present = [k for k, row in enumerate(rows) if row is not None]
    closing = js_round(total.hiring - sum(rows[k].hiring for k in present))
    rounded = _apportion([rows[k].hiring for k in present], total_hiring - closing)
    out: list[int | None] = [None] * len(rows)
    for k, hiring in zip(present, rounded):
        out[k] = hiring
    return out, closing


def _closing_row(hiring: int) -> LineMove:
    """The breakdown's closing row: one figure, "moved between categories by a counting change",
    hiring the rows took out as a counting change's and the first row counts."""
    return LineMove(
        start=0,
        latest=0,
        hiring=hiring,
        not_hiring=(
            Cause(
                MOVED_BETWEEN_CATEGORIES,
                CauseKind.COUNTING,
                "moved between categories by a counting change",
                -hiring,
            ),
        ),
        not_hiring_total=-hiring,
        percent=None,
        percent_withheld="a closing row has no start",
        span_days=0.0,
        per_week=None,
        turnover=None,
    )


def _line_move(
    *,
    start: int,
    latest: int,
    hiring: int,
    not_hiring: tuple[Cause, ...],
    span_days: float,
    turnover: Turnover | None,
    denominators: tuple[int | None, int | None] | None,
) -> LineMove:
    """A line's move from its whole figures: its percentage, weekly rate and share, each read
    off them once. ``denominators`` is the share's, netted at the start and as counted now."""
    span_days = round(span_days, 4)
    netted_start = latest - hiring
    percent, withheld = None, None
    if span_days < MIN_SPAN_DAYS:
        withheld = f"a window under {MIN_SPAN_DAYS} days"
    elif start < MOVER_FLOOR:
        withheld = f"under {MOVER_FLOOR} openings at the start"
    elif netted_start < INDEX_BASE_FLOOR:
        withheld = f"under {INDEX_BASE_FLOOR} openings at the start once the steps are taken out"
    else:
        percent = hiring / netted_start * 100
    share = None
    if denominators is not None:
        den_start, den_latest = denominators
        at_start = netted_start / den_start * 100 if den_start else None
        now = latest / den_latest * 100 if den_latest else None
        share = Share(
            start=at_start,
            latest=now,
            denominator_start=den_start,
            denominator_latest=den_latest,
            percent=(now - at_start) / at_start * 100
            if percent is not None and at_start and now is not None
            else None,
        )
    return LineMove(
        start=start,
        latest=latest,
        hiring=hiring,
        not_hiring=not_hiring,
        not_hiring_total=sum(c.size for c in not_hiring),
        percent=percent,
        percent_withheld=withheld,
        span_days=span_days,
        per_week=js_round(hiring / span_days * 7)
        if span_days >= MIN_SPAN_DAYS
        else None,
        turnover=turnover,
        share=share,
    )


def _drawn(values) -> tuple[float | None, ...]:
    """Values a line is drawn from, to _DRAWN_DECIMALS: a shape, not counts."""
    return tuple(None if v is None else round(v, _DRAWN_DECIMALS) for v in values)


# ---- the invariants --------------------------------------------------------------------------


def check_reading(reading: dict) -> list[str]:
    """Every way ``reading`` (``TrendReading.to_json()``) breaks ADR-0233's invariants, as
    sentences; empty when it reconciles. The page's ``checkReading`` states the same ones.

    1. For every line: latest − start == hiring + Σ not_hiring.
    2. A company line's "Not hiring" is its Marked changes: each cause is listed at that size,
       and every size listed for it is one of its causes.
    3. A breakdown's rows, with its closing row, add up to its first row in start, latest,
       hiring and Not hiring. The closing row starts and ends at 0 and is one figure: hiring N
       and one counting-change cause −N, N ≠ 0. Cause by cause the rows need not add up (a
       category takes a removal by an estimated ratio, and the erase guard rescales a
       category's growth that the company line keeps), nor do opened and closed: a category
       leaves out a Found Board's run that the company line counts.
    4. A change's size is the same in every window that holds it. One reading holds one
       window, so the tests state it, re-reading over narrower windows. Growth a scaling took
       out is sized by the window: it holds only while the window keeps the growth before it.
    5. Share is the netted count over the netted denominator, and the percentage is hiring over
       the netted start, given only off INDEX_BASE_FLOOR openings or more: neither is netted a
       second time. The share's own change is its latest
       over its start, and is withheld with the percentage.
    6. With no pick nothing is taken out.
    Plus: every count is a whole number; a line's "Not hiring" total is its causes' sum; its
    weekly rate is its hiring over the days it was counted, withheld under MIN_SPAN_DAYS; its
    turnover's net is opened less closed; the Other row is the lines past LINES_CHARTED added
    together; no change is one the reading could not name; and every Marked change is named by
    exactly one day marker."""
    out: list[str] = []
    changes = {c["id"]: c for c in reading.get("marked_changes") or []}
    lines = [
        ("first row", reading.get("total")),
        *((f"line {r['name']}", r) for r in reading.get("lines") or []),
        ("other row", reading.get("other")),
        *((f"company line {r['name']}", r) for r in reading.get("company_lines") or []),
    ]
    moves = [(where, r["move"]) for where, r in lines if r]
    breakdown = reading.get("breakdown")
    closing = breakdown.get("closing") if breakdown else None
    if closing:
        moves.append(("closing row", closing))
    for where, m in moves:
        counts = [
            m["start"],
            m["latest"],
            m["hiring"],
            m["not_hiring_total"],
            *(c["size"] for c in m["not_hiring"]),
        ]
        if m["per_week"] is not None:
            counts.append(m["per_week"])
        if m["turnover"]:
            counts += [
                m["turnover"]["opened"],
                m["turnover"]["closed"],
                m["turnover"]["net"],
            ]
        if any(not isinstance(n, int) or isinstance(n, bool) for n in counts):
            out.append(f"{where}: a count is not a whole number")
            continue
        named = sum(c["size"] for c in m["not_hiring"])
        netted_start = m["latest"] - m["hiring"]
        if m["latest"] - m["start"] != m["hiring"] + named:
            out.append(
                f"{where}: latest − start is {m['latest'] - m['start']}, "
                f"hiring + not hiring is {m['hiring'] + named}"
            )
        if m["not_hiring_total"] != named:
            out.append(
                f"{where}: its Not hiring reads {m['not_hiring_total']}, its causes sum to "
                f"{named}"
            )
        span = m["span_days"]
        weekly = js_round(m["hiring"] / span * 7) if span >= MIN_SPAN_DAYS else None
        if m["per_week"] != weekly:
            out.append(f"{where}: its weekly rate is not its hiring over its days")
        turnover = m["turnover"]
        if turnover and turnover["net"] != turnover["opened"] - turnover["closed"]:
            out.append(f"{where}: its turnover's net is not opened less closed")
        for c in m["not_hiring"]:
            if c["kind"] == CauseKind.UNEXPLAINED:
                out.append(
                    f"{where}: {c['size']} openings of not hiring have no named cause"
                )
        share = m.get("share")
        if share:
            den = share["denominator_start"]
            if not _same(share["start"], netted_start / den * 100 if den else None):
                out.append(
                    f"{where}: its share at the start is not its netted count over the "
                    "netted denominator"
                )
            den = share["denominator_latest"]
            if not _same(share["latest"], m["latest"] / den * 100 if den else None):
                out.append(
                    f"{where}: its latest share is not its count over the denominator"
                )
            start, now = share["start"], share["latest"]
            change = (
                (now - start) / start * 100
                if m["percent"] is not None and start and now is not None
                else None
            )
            if not _same(share["percent"], change):
                out.append(
                    f"{where}: its share's change is not its latest share over its start"
                )
        if m["percent"] is not None and (
            netted_start < INDEX_BASE_FLOOR
            or not _same(m["percent"], m["hiring"] / netted_start * 100)
        ):
            out.append(f"{where}: its percentage is not hiring over the netted start")
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
        rows = [r["move"] for r in reading.get("lines") or []]
        if closing:
            causes = closing["not_hiring"]
            if (
                closing["start"]
                or closing["latest"]
                or not closing["hiring"]
                or len(causes) != 1
                or causes[0]["kind"] != CauseKind.COUNTING
                or causes[0]["size"] != -closing["hiring"]
            ):
                out.append(
                    "closing row: it is not one figure moved between categories by a "
                    "counting change"
                )
            rows.append(closing)

        def fields(m: dict) -> dict[str, int]:
            return {
                "start": m["start"],
                "latest": m["latest"],
                "hiring": m["hiring"],
                "not hiring": sum(c["size"] for c in m["not_hiring"]),
            }

        want = fields(reading["total"]["move"])
        for k, total in want.items():
            summed = sum(fields(m)[k] for m in rows)
            if summed != total:
                out.append(
                    f"breakdown: its rows' {k} add up to {summed}, its first row's is {total}"
                )
    folded = [r["move"] for r in (reading.get("lines") or [])[LINES_CHARTED:]]
    other = reading.get("other")
    if bool(folded) != bool(other):
        out.append(
            f"other row: {'missing' if folded else 'present'} with "
            f"{len(folded)} lines past the first {LINES_CHARTED}"
        )
    elif other:
        m = other["move"]
        for k in ("start", "latest", "hiring", "not_hiring_total"):
            if m[k] != sum(f[k] for f in folded):
                out.append(
                    f"other row: its {k} is not the folded lines' added together"
                )
        merged: Counter = Counter()
        for f in folded:
            merged.update({c["change"]: c["size"] for c in f["not_hiring"]})
        if {c["change"]: c["size"] for c in m["not_hiring"]} != {
            c: n for c, n in merged.items() if n
        }:
            out.append("other row: its causes are not the folded lines' added together")
    named = Counter(
        change for d in reading.get("day_markers") or [] for change in d["changes"]
    )
    for cid in changes:
        if named[cid] != 1:
            out.append(
                f"marked change {cid}: named by {named[cid]} day markers, not one"
            )
    for cid in named.keys() - changes.keys():
        out.append(f"day marker: it names {cid}, which is no Marked change")
    return out


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
