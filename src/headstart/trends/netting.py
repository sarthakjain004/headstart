"""**Netting** (ADR-0185, ADR-0230): how much of a Trends line's change is hiring.

A line's change holds steps that are not hiring: a **Counting change** and the run after it, a
**Found Board**'s backlog, a pick counted from a later date joining a sum, duplicate postings
removed, and a Board read only partly for one run. This module takes them out, once, for every
line an answer serves (ADR-0230 decision 3). Until ADR-0230 step 4 the rule lived in the page's
JavaScript (`stepNotes`, `stepJumps`, `netOfSteps`); this is a port of that rule, checked against
it by the golden answers under ``tests/fixtures/trend_answers/``. Since ADR-0233 step 3 the page
reads ``line_reading``, which uses this module as its private implementation; Hot still reads
:func:`net_answer` until step 4.

:func:`net_answer` is the one entry point. It reads an answer as ``TrendHistory.answer`` builds it
and adds, without touching any count:

- ``notes``: every point in the window where lines move for a reason that is not hiring, each
  with what the page needs to say it (``kind``, ``fields``, ``changed``, ``company``, ``size``);
- on each series, on ``series_sum`` (every series added together, the whole company's line
  under a pick) and on ``totals_net`` (the share denominator, the chart's dashed reference line):
  - ``net``: the line with its steps taken out, in openings (``count``) and as a share
    (``share``). Adjusted backwards, the way a price history is: the latest value stays the real
    one and the history before a step is brought to it;
  - ``steps``: each note that moves the line, with where it lands (``at``), how many openings it
    moved the line by (``size``) and whether its runs moved the line at all (``moved``);
  - ``jumps``: each run a step lands on, with the jump there, where a drawn line breaks;
  - ``causes``: how much of the change was duplicates removed and Boards found;
  - ``hiring_turnover``: the jobs opened and closed over the runs the net change counts;
  - ``born_by_change``, for the "sorted in by a counting change" reading.

With no pick nothing is taken out: the index chart keeps a counting change's jump, marked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from headstart.boards.board_identity import ats_of, tenant

# The counting changes that move every line they reach (ADR-0164): a taxonomy refit, a
# family-list or family-assignment change (ADR-0215, ADR-0220) and a tech-filter change. An
# extraction change (derivations) moves only a Level breakdown, which it re-sorts.
LINE_MOVING_FIELDS = (
    "centroid_version",
    "family_map_fingerprint",
    "family_classifier_version",
    "tech_filter_version",
)
# The tick `new` stops being a level and becomes the Opened inflow (ADR-0230 decision 5): a
# counting change of its own, marked on every `new` line and taken out of it like a refit.
NEW_BECAME_INFLOW = "new_became_inflow"
# What each Methodology field is called where it moved (ADR-0164), as a change ("tech filter
# changed") and as a noun ("the week-later echo of the Sep 17 tech filter change"): the one home
# of the words every Trends label is built from, so no label is a raw field id.
METHODOLOGY_WORDS = {
    "centroid_version": ("role taxonomy refit", "role taxonomy refit"),
    "family_map_fingerprint": ("role family map edited", "role family map edit"),
    "tech_filter_version": ("tech filter changed", "tech filter change"),
    "derivations_version": (
        "experience/salary extraction changed",
        "experience/salary extraction change",
    ),
    "dedup_version": ("duplicate removal changed", "duplicate removal change"),
    "family_classifier_version": (
        "role family assignment changed",
        "role family assignment change",
    ),
    NEW_BECAME_INFLOW: (
        "new openings became the jobs opened in the week",
        "change of new openings to the jobs opened in the week",
    ),
}
_DERIVATIONS = "derivations_version"
_DEDUP = "dedup_version"
_TECH_FILTER = "tech_filter_version"

# Duplicate removal parks copies among one Tenant's Boards on these ATSes (ADR-0186/0187), and
# aliases or drops Eightfold Boards against the Board they mirror (#632, #649), so only a company
# holding such Boards can step at a duplicate-removal change.
DEDUP_SIBLING_ATSES = ("taleo_enterprise", "workday")
DEDUP_MIRROR_ATS = "eightfold"

# Below this many openings a found Board or a duplicate removal is left in the line, unmarked:
# a marker for one opening was furniture.
SMALLEST_STEP_TAKEN_OUT = 5

# A shift that would push a line's history below zero scales instead, where both sides of the
# step hold this many openings (ADR-0185, round 13).
RATIO_FLOOR = 20

# A partial read (a leap one run puts straight back) is off both neighbours by at least this many
# openings and half, with the neighbours within a tenth of each other.
_PARTIAL_READ_MIN_LEAP = 20

_TOTAL = "__total__"


def js_round(x: float) -> int:
    """``Math.round``: the nearest integer, halves toward +infinity. Python's ``round`` goes to
    the even neighbour, which made −2.5 read −2 in one and −3 in the other."""
    floor = math.floor(x)
    return int(floor + 1 if x - floor >= 0.5 else floor)


def dedup_touched(boards) -> bool:
    """Whether duplicate removal can move a company holding ``boards``: any Eightfold Board, or
    two or more Boards of one Tenant on an ATS it dedupes within. Tenants compare case-blind."""
    boards = list(boards)
    if any(ats_of(board) == DEDUP_MIRROR_ATS for board in boards):
        return True
    for ats in DEDUP_SIBLING_ATSES:
        tenants = [tenant(board).lower() for board in boards if ats_of(board) == ats]
        if len(set(tenants)) < len(tenants):
            return True
    return False


def moves_lines(fields, bands: bool) -> bool:
    """Whether a counting change of ``fields`` moves every line it reaches: a line-moving change,
    or on a Level breakdown an extraction change, which re-sorts the levels."""
    return any(f in LINE_MOVING_FIELDS or f == NEW_BECAME_INFLOW for f in fields) or (
        bands and _DERIVATIONS in fields
    )


def left_out_runs(
    epochs: list[dict], stamps: list[str], bands: bool
) -> tuple[set[int], set[int]]:
    """The charted runs a company's whole line leaves out of its hiring: ``(for every Board, for
    a Board duplicate removal can move)``. A counting change that moves lines leaves out its run
    and the run after it; a duplicate-removal change alone does so only where it can move a
    Board. A change on the window's first run is already in every line's start. The index's
    turnover leaves these out Board by Board, so it is the sum of what every company's view
    shows (ADR-0227)."""
    every: set[int] = set()
    touched: set[int] = set()
    for epoch in epochs:
        if epoch["ts"] not in stamps[1:]:
            continue
        i = stamps.index(epoch["ts"])
        fields = epoch.get("fields") or []
        runs = {j for j in (i, i + 1) if j < len(stamps)}
        if moves_lines(fields, bands):
            every |= runs
        elif _DEDUP in fields:
            touched |= runs
    return every, touched


def drop_partial_reads(series: list[dict]) -> int:
    """Null out every partial read in ``series``' points, in place, and return how many runs had
    one. A run where one line leaps and the next run puts it straight back is a partial read of a
    Board, not hiring: Newyorklife went 26 → 104 → 26, headlined "+292.3%" at the leap. The
    newest run has no next one to tell by, so a leap there stands until the next run."""
    runs: set[int] = set()
    for line in series:
        points = line["points"]
        at = [j for j, v in enumerate(points) if v is not None]
        # Judged against the last point kept, not the one just dropped: [26, 104, 26, 0] dropped
        # the second 26 as well, reading the null it had just made as the level before it.
        prev = points[at[0]] if at else None
        for k in range(1, len(at) - 1):
            v, following = points[at[k]], points[at[k + 1]]
            if abs(v - prev) >= max(_PARTIAL_READ_MIN_LEAP, prev * 0.5) and abs(
                following - prev
            ) <= max(1, prev * 0.1):
                points[at[k]] = None
                runs.add(at[k])
            else:
                prev = v
    return len(runs)


@dataclass
class _Line:
    """One line the answer serves. ``name`` None is the dashed reference line (``totals``);
    ``company`` names the one company a part of a summed category counts (``pick_parts``)."""

    name: str | None
    points: list
    pick: bool = False
    turnover: dict | None = None
    denominators: list | None = None
    company: str | None = None


@dataclass
class _Jump:
    before: float
    after: float
    kinds: set[str]
    lift: float | None
    # the notes whose steps land on this run, for trend_reading to say whose jump it is
    notes: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Scaling:
    """A run that scales the history before it by ``ratio``: a company's duplicate removal
    (``by_removal``), or a shift the erase guard scaled because it would go below zero."""

    ratio: float
    by_removal: bool


@dataclass
class _NetTrace:
    """How :func:`_net` took the steps out of one line, recorded for ``line_reading`` to size
    each cause without a second copy of the rule. Runs are indexes into the line's levels.

    - ``scale``: each kept run -> the scale its netted value was read at (the removals and
      scaled shifts after it);
    - ``withheld``: each run a step lands on -> the openings the run gives up, before scaling;
    - ``ratios``: each run that scales the history before it -> its :class:`_Scaling`;
    - ``jumps``: each run a step lands on -> its jump.

    A line summing several picks is traced pick by pick, by ``line_reading``."""

    scale: dict[int, float] = field(default_factory=dict)
    withheld: dict[int, float] = field(default_factory=dict)
    ratios: dict[int, _Scaling] = field(default_factory=dict)
    jumps: dict[int, _Jump] = field(default_factory=dict)


@dataclass
class _View:
    """What of the question decides how a line is netted."""

    metric: str
    drilled: bool
    split_company: bool
    stamps: list[str]
    totals: list
    notes: list[dict]
    pick_series: dict[str, list]
    pick_parts: dict[str, dict[str, list]]
    pick_turnover: dict[str, dict]
    company_totals: dict[str, list]
    evicted: list[dict]
    company_keys: list[str]
    picked: bool
    _jump_cache: dict = field(default_factory=dict)


def _notes(answer: dict) -> list[dict]:
    """Every point in the window where lines move for a reason that is not hiring.

    - A counting change (ADR-0164) is listed on every chart with no pick. Under a pick it is
      listed only where it moves the lines, and taken out of them: a taxonomy refit, a
      family-list or family-assignment change, a tech-filter change (Wipro's "+74.7%" held about
      +25% from the Sep 17 filter step alone), an extraction change on a Level breakdown, or
      duplicate removal (ADR-0188) at a pick it can touch. The run after each change is left out
      too, since a change can land over two runs (Amazon's Sep 17: +308, then −439).
    - Under New a tech-filter change is also taken out a week later, when its openings age out.
    - A Board found later lands its backlog at once (``discovered``).
    - Duplicate rows removed at a pick's Boards (``evicted``, #649), sized exactly, per Board.
    - In a view that sums several picks, one counted from a later date joins the sum at once.
    """
    stamps = answer["stamps"]
    metric = answer["metric"]
    companies = answer.get("companies") or []
    picked = bool(companies)
    bands = bool(answer.get("family")) and answer.get("split_by") == "band"
    touched = [c["key"] for c in companies if dedup_touched(c.get("board_keys") or [])]
    notes: list[dict] = []

    def note(**given) -> dict:
        out = {
            "i": given["i"],
            "kind": given.get("kind", "counting"),
            "epoch": False,
            "echo": False,
            "settle": False,
            "found": False,
            "evicted": False,
            "join": False,
            "withhold": False,
            "company": None,
            "companies": None,
            "size": None,
            "boards": None,
            "fields": [],
            "changed": [],
            "source": None,
            "touched": [],
            "bands_only": False,
            "dedup_only": False,
            "whole_only": False,
        }
        out.update(given)
        return out

    for epoch in answer.get("epochs") or []:
        fields = epoch.get("fields") or []
        if metric == "new" and _TECH_FILTER in fields:
            # Taken out a second time when the openings it let in age out of `new`, after the
            # answer's `new_window_days` (``trend_history.NEW_WINDOW_DAYS``).
            echo = datetime.fromisoformat(epoch["ts"]) + timedelta(
                days=answer["new_window_days"]
            )
            k = next(
                (
                    k
                    for k, ts in enumerate(stamps)
                    if datetime.fromisoformat(ts) >= echo
                ),
                -1,
            )
            if k > 0:
                notes.append(
                    note(
                        i=k,
                        epoch=True,
                        echo=True,
                        withhold=picked,
                        fields=[_TECH_FILTER],
                        source=epoch["ts"],
                        touched=touched,
                    )
                )
        if epoch["ts"] not in stamps:
            continue
        i = stamps.index(epoch["ts"])
        # Under picks, a duplicate-removal change is named only where a pick can be touched:
        # Google's marker read "duplicate removal changed", which moved nothing at Google.
        said = [
            label
            for k, label in enumerate(epoch.get("changed") or [])
            if not picked
            or (fields[k] if k < len(fields) else None) != _DEDUP
            or touched
        ]
        if not picked:
            notes.append(note(i=i, epoch=True, changed=said))
            continue
        lines_move = moves_lines(fields, bands)
        dedup = not lines_move and bool(touched) and _DEDUP in fields
        if not (lines_move or dedup):
            continue
        dedup_only = fields == [_DEDUP]
        shared = {
            "companies": touched if dedup else None,
            # An extraction change re-sorts a category's levels, never its total.
            "bands_only": not any(
                f in LINE_MOVING_FIELDS or f in (_DEDUP, NEW_BECAME_INFLOW)
                for f in fields
            ),
            "dedup_only": dedup_only,
            "kind": "duplicates" if dedup_only and metric == "new" else "counting",
        }
        # A change on the window's first run is already in every line's start; its settling
        # run is not.
        if i > 0:
            notes.append(
                note(
                    i=i,
                    epoch=True,
                    withhold=True,
                    fields=fields,
                    changed=said,
                    source=epoch["ts"],
                    touched=touched,
                    **shared,
                )
            )
        # Only for a change inside the window: at its first run the change's own jump is not on
        # the chart, so its settling run cannot be told from an ordinary one.
        if i > 0 and i + 1 < len(stamps):
            notes.append(note(i=i + 1, settle=True, withhold=True, **shared))
    for found in answer.get("discovered") or []:
        if found["ts"] not in stamps:
            continue
        # Found openings are added back, not scaled: they were open all along. A handful is left
        # in the line, unmarked.
        notes.append(
            note(
                i=stamps.index(found["ts"]),
                kind="found",
                found=True,
                withhold=found["openings"] >= SMALLEST_STEP_TAKEN_OUT,
                company=found["company"],
                size=found["openings"],
                boards=found["boards"],
            )
        )
    # Duplicates removed are sized per Board, not per category, so only a whole company's line
    # can take them out (`whole_only`).
    if metric == "stock":
        for removed in answer.get("evicted") or []:
            i = stamps.index(removed["ts"]) if removed["ts"] in stamps else -1
            if i <= 0 or removed["count"] < SMALLEST_STEP_TAKEN_OUT:
                continue
            notes.append(
                note(
                    i=i,
                    kind="duplicates",
                    evicted=True,
                    withhold=True,
                    company=removed["company"],
                    size=-removed["count"],
                    whole_only=True,
                )
            )
    # Not under comparable coverage: a pick counted after the cohort's base has no Boards in it.
    # Under New a pick joins when its first week ends (`new_counted_from`), not when counted.
    if (
        len(companies) > 1
        and answer.get("split_by") != "company"
        and answer.get("coverage") != "comparable"
    ):
        since = (
            answer.get("new_counted_from")
            if metric == "new"
            else answer.get("counted_since")
        ) or {}
        for company in companies:
            began = since.get(company["key"])
            i = (
                next((k for k, ts in enumerate(stamps) if ts >= began), -1)
                if began
                else -1
            )
            if i <= 0:
                continue
            notes.append(
                note(
                    i=i,
                    kind="found",
                    found=True,
                    join=True,
                    withhold=True,
                    company=company["key"],
                )
            )
    return notes


def _is_whole(view: _View, line: _Line | None) -> bool:
    """A line that is a whole company's tech openings under All openings: the sum of every
    series, a pick's own line, or a company's line at the top level. Only these can take out a
    step whose size is known per company. Never inside a category: NVIDIA's 2,045 removals
    would overshoot its AI/ML 300."""
    return (
        view.metric == "stock"
        and line is not None
        and not view.drilled
        and (line.name == _TOTAL or line.pick or view.split_company)
    )


def _line_notes(view: _View, line: _Line | None, omit: int | None = None) -> list[int]:
    """The notes taken out of ``line`` (``omit`` left out, to size a change against the rest).
    Every line of a view leaves out the same runs, so a company's categories add up to it. A
    company's own step moves every line of a summed view but only its own line under a Company
    breakdown, and only its own part of a category summed over several picks. ``line`` None is
    the dashed reference line."""
    per_company = line is not None and (line.pick or view.split_company)
    whole = _is_whole(view, line)
    out = []
    for k, n in enumerate(view.notes):
        if k == omit or not n["withhold"] or (n["whole_only"] and not whole):
            continue
        # a pick's line is its total, which an extraction change never moves
        if n["bands_only"] and (line is None or line.name == _TOTAL or line.pick):
            continue
        if per_company and (
            (n["company"] and line.name != n["company"])
            or (n["companies"] is not None and line.name not in n["companies"])
        ):
            continue
        if (
            line is not None
            and line.company
            and (
                (n["company"] and n["company"] != line.company)
                or (n["companies"] is not None and line.company not in n["companies"])
            )
        ):
            continue
        out.append(k)
    return out


def _summed_picks(view: _View, line: _Line | None) -> list[_Line] | None:
    """Each pick's own part of ``line`` when it sums several: ``pick_series`` for the sum of
    every series, ``pick_parts`` for a category or level. A category took the removals of no
    company: NVIDIA and Micron's categories summed +73 against a Total of +40 (#690)."""
    if line is None or line.pick or line.company:
        return None
    if line.name == _TOTAL:
        if len(view.pick_series) < 2:
            return None
        return [
            _Line(name, points, pick=True) for name, points in view.pick_series.items()
        ]
    parts = view.pick_parts.get(line.name)
    if not parts or len(parts) < 2:
        return None
    return [
        _Line(line.name, points, company=company) for company, points in parts.items()
    ]


def _level_before(values: list, j: int):
    """The last measured value before run ``j``, or None."""
    k = j - 1
    while k >= 0 and values[k] is None:
        k -= 1
    return values[k] if k >= 0 else None


def _line_company(view: _View, line: _Line | None) -> str | None:
    """The one company ``line`` counts: its own for a company's line, a company's part of a
    summed line, or any line inside one company's view; None for a line summing several."""
    if line is None:
        return None
    if line.company:
        return line.company
    if line.pick or (view.split_company and line.name in view.company_keys):
        return line.name
    if len(view.company_keys) == 1 and not _summed_picks(view, line):
        return view.company_keys[0]
    return None


def _dup_ratios(view: _View, key: str) -> dict[int, float]:
    """A company's duplicate removals as ratios: landing run -> (its served jobs before −
    removed) / its served jobs before. Every posting of a doubled Board was counted twice all
    along, and so was every hire, so a removal scales the history before it rather than lifting
    it (#690; the owner's choice, 2026-09-25). From ``company_totals``, every served job, the
    base the removal count is taken from. Under All openings only, where removals are sized."""
    totals = view.company_totals.get(key)
    if not totals or view.metric != "stock":
        return {}
    # one run's removals are one removal: summed, then one ratio
    removed: dict[int, int] = {}
    for e in view.evicted:
        if e["company"] != key or e["count"] < SMALLEST_STEP_TAKEN_OUT:
            continue
        j = view.stamps.index(e["ts"]) if e["ts"] in view.stamps else -1
        if j > 0:
            removed[j] = removed.get(j, 0) + e["count"]
    out: dict[int, float] = {}
    for j, count in removed.items():
        before = _level_before(totals, j)
        if before is not None and before > 0 and before - count > 0:
            out[j] = (before - count) / before
    return out


def _dup_ratios_for(
    view: _View, line: _Line | None, in_openings: bool
) -> dict[int, float]:
    """Whose removals scale ``line``: its own company's, in openings only."""
    key = _line_company(view, line) if in_openings else None
    return dict(_dup_ratios(view, key)) if key else {}


def _jumps(
    view: _View,
    levels: list,
    line: _Line | None,
    only: frozenset[str] | None,
    omit: int | None = None,
) -> dict[int, _Jump]:
    """Where each step taken out of ``line`` lands on ``levels`` (a step on a gap lands on the
    next measured point), with the level on either side of it. ``lift`` is a step's size when it
    is known exactly and the line is a whole company's: found openings, duplicate removals.
    Taking out that size rather than the run's whole jump keeps the run's ordinary hiring in.
    ``only`` limits the steps to those kinds."""
    whole = _is_whole(view, line)
    steps: dict[int, dict] = {}
    for k in _line_notes(view, line, omit):
        n = view.notes[k]
        if only is not None and n["kind"] not in only:
            continue
        at = steps.setdefault(
            n["i"], {"size": 0, "sized": True, "kinds": set(), "notes": ()}
        )
        if n["size"] is None:
            at["sized"] = False
        else:
            at["size"] += n["size"]
        at["kinds"].add(n["kind"])
        at["notes"] += (k,)
    jumps: dict[int, _Jump] = {}
    if not steps:
        return jumps
    last = None
    pending = None
    for j, v in enumerate(levels):
        if j in steps:
            at = steps[j]
            pending = (
                {
                    "size": pending["size"] + at["size"],
                    "sized": pending["sized"] and at["sized"],
                    "kinds": pending["kinds"] | at["kinds"],
                    "notes": pending["notes"] + at["notes"],
                }
                if pending
                else {**at, "kinds": set(at["kinds"])}
            )
        if v is None:
            continue
        lift = pending["size"] if pending and whole and pending["sized"] else None
        # Removing duplicates cannot add openings. A rise over a duplicates-only step with no
        # known size is that run's ordinary hiring, and stays in the line.
        hiring = (
            pending is not None
            and lift is None
            and v > (last if last is not None else 0)
            and pending["kinds"] == {"duplicates"}
        )
        if pending and last is not None and not hiring:
            jumps[j] = _Jump(last, v, pending["kinds"], lift, pending["notes"])
        pending = None
        last = v
    return jumps


def _count_jumps(
    view: _View, line: _Line | None, only=None, omit: int | None = None
) -> dict[int, _Jump]:
    """``_jumps`` on ``line``'s own openings, worked out once per line: a line is one series, a
    pick's or a company's part of a summed line, the sum of every series, or (None) the
    reference line."""
    key = (
        (line.name, line.pick, line.company, only, omit)
        if line is not None
        else (None, False, None, only, omit)
    )
    if key not in view._jump_cache:
        view._jump_cache[key] = _jumps(
            view, line.points if line else view.totals, line, only, omit
        )
    return view._jump_cache[key]


def _net(
    view: _View,
    levels: list,
    line: _Line | None,
    only: frozenset[str] | None,
    in_openings: bool,
    omit: int | None = None,
    trace: _NetTrace | None = None,
) -> list:
    """``levels`` with the steps taken out of ``line``, adjusted backwards: the latest value
    stays the real one, and the history before a step is shifted by the step's size, never
    scaled, so a company's categories add up to it (the owner's choice, 2026-09-25).

    The exceptions: a company's duplicate removal scales the history before it (`_dup_ratios`),
    and a shift that would push the history below zero scales instead where both sides hold
    RATIO_FLOOR openings; where it cannot scale either, the line starts after that step instead
    of inventing a zero base. A line summing several picks is the sum of each pick's own netted
    part, so a company's step comes out of its own part only, in openings; under Share every
    level is divided by the whole. ``trace``, when given, records how (:class:`_NetTrace`)."""
    picks = _summed_picks(view, line)
    if picks and in_openings:
        nets = []
        for pick in picks:
            net = _net(view, pick.points, pick, only, True, omit)
            first = next((v for v in net if v is not None), None)
            seen = False
            filled = []
            # Before a pick's first run its part of the sum holds at its first value.
            for v in net:
                seen = seen or v is not None
                filled.append(v if seen else first)
            nets.append(filled)
        summed = _sum_points(nets, len(levels))
        return [None if v is None else summed[j] for j, v in enumerate(levels)]
    jumps = _jumps(view, levels, line, only, omit)
    # A line with no step of its own can still sit inside a company whose removals scale it.
    dups = (
        _dup_ratios_for(view, line, in_openings)
        if only is None or "duplicates" in only
        else {}
    )
    if omit is not None and view.notes[omit]["evicted"]:
        dups.pop(view.notes[omit]["i"], None)
    if not jumps and not dups:
        return list(levels)
    # The same steps read off the openings themselves, for the floor.
    counts = _count_jumps(view, line, only, omit) if line is not None else jumps
    out = list(levels)
    lowest = math.inf
    lowest_before = []
    for v in levels:
        lowest_before.append(lowest)
        if v is not None:
            lowest = min(lowest, v)
    scale, lift, cut = 1, 0, False
    for j in range(len(levels) - 1, -1, -1):
        if levels[j] is None:
            # A removal on a run this line has no point at still scales what came before it.
            if j in dups:
                scale *= dups[j]
                if trace is not None:
                    trace.ratios[j] = _Scaling(dups[j], by_removal=True)
            continue
        v = levels[j] * scale + lift
        if cut or v < 0:
            cut = True
            out[j] = None
            continue
        out[j] = v
        jump = jumps.get(j)
        if trace is not None:
            trace.scale[j] = scale
            if jump:
                trace.jumps[j] = jump
        if j in dups:
            # The history before the run is scaled by the ratio, and the run itself gives up its
            # own step whole where it has one, else its share of the removal.
            r = dups[j]
            before = jump.before if jump else _level_before(levels, j)
            if before is not None:
                if jump and jump.lift is not None:
                    # A removal gives up its share of the line's tech openings, never the rows
                    # it removed, which count non-tech ones too (ADR-0233 decision 3).
                    removed = sum(
                        view.notes[k]["size"]
                        for k in jump.notes
                        if view.notes[k]["evicted"]
                    )
                    withheld = jump.lift - removed + (r - 1) * before
                elif jump:
                    withheld = jump.after - jump.before
                else:
                    withheld = (r - 1) * before
                lift += scale * (withheld - (r - 1) * before)
                scale *= r
                if trace is not None:
                    trace.withheld[j] = withheld
                    trace.ratios[j] = _Scaling(r, by_removal=True)
                continue
        if not jump:
            continue
        size = counts.get(j) or jump
        known = "found" in jump.kinds or "duplicates" in jump.kinds
        shift = scale * (
            jump.lift if jump.lift is not None else jump.after - jump.before
        )
        erases = lowest_before[j] * scale + lift + shift < 0
        if (
            erases
            and jump.lift is None
            and not known
            and size.before >= RATIO_FLOOR
            and size.after >= RATIO_FLOOR
        ):
            scale *= jump.after / jump.before
            if trace is not None:
                trace.withheld[j] = jump.after - jump.before
                trace.ratios[j] = _Scaling(jump.after / jump.before, by_removal=False)
        else:
            lift += shift
            if trace is not None:
                trace.withheld[j] = shift / scale
    return out


def _runs(note: dict, line: _Line) -> list[int]:
    """The runs a note moves ``line`` at: where it lands (a step on a gap lands on the next
    measured point), and for a counting change where its settling run lands."""

    def landing(i: int) -> int:
        j = i
        while j < len(line.points) and line.points[j] is None:
            j += 1
        return j

    if note["epoch"] and not note["echo"]:
        return [landing(note["i"]), landing(note["i"] + 1)]
    return [landing(note["i"])]


def _moved(view: _View, k: int, line: _Line) -> bool:
    """Whether note ``k``'s runs moved ``line`` — its own run or its settling run."""
    jumps = _count_jumps(view, line)
    return any(
        (jump := jumps.get(j)) is not None and js_round(jump.after - jump.before) != 0
        for j in _runs(view.notes[k], line)
    )


def _birth_note(view: _View, line: _Line) -> int | None:
    """The counting change a category line was born at: one whose own run (or settling run) is
    the line's first point, never one that reaches it by skipping the empty runs before it.
    Skipping, Sep 17's filter change claimed Stripe's "Web & .NET" born at the Sep 24 refit. A
    company's own first point is when counting began, never a change's."""
    first = next((j for j, v in enumerate(line.points) if v is not None), -1)
    if first < 1 or _is_whole(view, line) or line.pick:
        return None
    for k in _line_notes(view, line):
        n = view.notes[k]
        if (
            n["epoch"]
            and not n["echo"]
            and n["kind"] == "counting"
            and (n["i"] == first or n["i"] + 1 == first)
        ):
            return k
    return None


def _head_tail_change(values: list) -> float:
    seen = [v for v in values if v is not None]
    return seen[-1] - seen[0] if len(seen) >= 2 else 0


def _change_size(view: _View, k: int, line: _Line) -> float:
    """How many openings note ``k`` moved ``line`` by, exactly, sized as the sentence peels its
    causes: removals first, then everything else in the frame the removals leave. A removal the
    line scales by is what it does with only removals taken out; any other change is its jump at
    the scale the removals after it leave, and for a line the change sorted into existence, the
    openings it arrived with. A line summing several picks is sized pick by pick. Each left-out
    run belongs to one change: only a change of unknown size claims runs, and two changes on one
    run go to the new change over a week-later echo, else to the first listed."""
    picks = _summed_picks(view, line)
    if picks:
        total = 0
        for pick in picks:
            total = total + _change_size(view, k, pick)
        return total
    n = view.notes[k]
    dups = _dup_ratios_for(view, line, True)
    if n["evicted"] and n["i"] in dups and n["company"] == _line_company(view, line):
        only = frozenset({"duplicates"})

        def move_of(omit: int | None) -> float:
            return _head_tail_change(_net(view, line.points, line, only, True, omit))

        return move_of(k) - move_of(None)

    def factor_after(j: int) -> float:
        f = 1
        for at, r in dups.items():
            if at > j:
                f = f * r
        return f

    steps = _line_notes(view, line)
    if k not in steps:
        return 0
    whole = _is_whole(view, line)
    if whole and n["size"] is not None:  # a found Board
        return n["size"] * factor_after(_runs(n, line)[0])

    def claims(m: dict) -> bool:
        return not m["settle"] and m["size"] is None

    mine = steps.index(k)

    def beats(position: int) -> bool:
        m = view.notes[steps[position]]
        return position < mine if m["echo"] == n["echo"] else not m["echo"]

    owned = set()
    ahead = set()
    for position, other in enumerate(steps):
        m = view.notes[other]
        if other == k or not claims(m):
            continue
        owned.add(_runs(m, line)[0])
        if beats(position):
            ahead.add(_runs(m, line)[0])
    runs = [
        j
        for position, j in enumerate(_runs(n, line))
        if (j not in ahead if position == 0 else j not in owned)
    ]
    first = next((j for j, v in enumerate(line.points) if v is not None), -1)
    arrived = (
        line.points[first] * factor_after(first)
        if view.metric == "stock" and _birth_note(view, line) == k
        else 0
    )
    # Each run at its own scale, as `_net` takes it out: a run that is also a removal's gives up
    # the removal's share there, and the removal's known size is not taken twice.
    jumps = _count_jumps(view, line)
    moved = 0
    for j in runs:
        jump = jumps.get(j)
        if not jump:
            continue
        known = 0
        if whole:
            for other in steps:
                m = view.notes[other]
                if (
                    other != k
                    and m["size"] is not None
                    and not (m["evicted"] and j in dups)
                    and _runs(m, line)[0] == j
                ):
                    known = known + m["size"]
        share = (dups[j] - 1) * jump.before if j in dups else 0
        moved = moved + (jump.after - jump.before - known - share) * factor_after(j)
    return arrived + moved


def _causes(view: _View, line: _Line) -> dict[str, int]:
    """The duplicates and found-Board parts of ``line``'s move, peeled a kind at a time so that
    with ratio steps the parts still add up to the total exactly."""

    def change(only: frozenset[str]) -> float:
        return _head_tail_change(_net(view, line.points, line, only, True))

    raw = change(frozenset())
    without_duplicates = change(frozenset({"duplicates"}))
    without_found = change(frozenset({"duplicates", "found"}))
    return {
        "duplicates": js_round(raw - without_duplicates),
        "found": js_round(without_duplicates - without_found),
    }


def _hiring_turnover(view: _View, line: _Line) -> dict[str, int] | None:
    """The jobs ``line`` opened and closed across the window (ADR-0227), over exactly the runs
    its hiring move counts: a step whose size is not known takes the turnover of every run inside
    its jump out too. The window's first run is none of it. The sum of every series under several
    picks sums each pick's own turnover. None when no run in the window measured turnover."""
    picks = _summed_picks(view, line)
    if picks and picks[0].pick:
        parts = [
            part
            for pick in picks
            if (
                part := _hiring_turnover(
                    view,
                    _Line(
                        pick.name, pick.points, True, view.pick_turnover.get(pick.name)
                    ),
                )
            )
        ]
        if not parts:
            return None
        return {
            "opened": sum(part["opened"] for part in parts),
            "closed": sum(part["closed"] for part in parts),
        }
    turnover = line.turnover
    if not turnover or view.metric != "stock":
        return None
    left: set[int] = set()
    if view.picked:
        jumps = _count_jumps(view, line)
        last = -1
        for j, v in enumerate(line.points):
            if v is None:
                continue
            jump = jumps.get(j)
            if jump and jump.lift is None:
                left.update(range(last + 1, j + 1))
            last = j
    opened = closed = 0
    seen = False
    for j, v in enumerate(turnover["opened"]):
        if j == 0 or v is None or j in left:
            continue
        seen = True
        opened += v
        closed += turnover["closed"][j] or 0
    return {"opened": opened, "closed": closed} if seen else None


def _shares(line: _Line, totals: list) -> list:
    denominators = line.denominators or totals
    return [
        v / denominators[j] * 100 if v is not None and denominators[j] else None
        for j, v in enumerate(line.points)
    ]


def _netted(view: _View, line: _Line) -> dict:
    """Everything the page reads off one line's netting. A note is on the line where it is taken
    out of it, or where it is a removal that scales it."""
    steps = []
    taken_out = set(_line_notes(view, line))
    for k, n in enumerate(view.notes):
        if k not in taken_out and not n["evicted"]:
            continue
        size = _change_size(view, k, line)
        if k in taken_out or size != 0:
            steps.append({"note": k, "size": size, "moved": _moved(view, k, line)})
    return {
        "net": {
            "count": _net(view, line.points, line, None, True),
            "share": _net(view, _shares(line, view.totals), line, None, False),
        },
        "steps": steps,
        "jumps": [
            {"i": j, "size": jump.after - jump.before}
            for j, jump in sorted(_count_jumps(view, line).items())
        ],
        "causes": _causes(view, line),
        "hiring_turnover": _hiring_turnover(view, line),
        "born_by_change": _birth_note(view, line) is not None,
    }


def _sum_points(lists: list[list], width: int) -> list:
    """Lines added run by run: null where none was measured, else each unmeasured one as 0."""
    out = []
    for j in range(width):
        if not any(points[j] is not None for points in lists):
            out.append(None)
            continue
        total = 0
        for points in lists:
            total = total + (points[j] or 0)
        out.append(total)
    return out


def _viewed(answer: dict) -> tuple[dict, _View]:
    """``answer`` with its partial reads dropped, and the view every line of it is netted in.
    Pure: the answer passed in is not changed."""
    answer = {**answer, "series": [dict(line) for line in answer["series"]]}
    companies = answer.get("companies") or []
    # A partial read is dropped before anything is netted or summed; under New a leap is the
    # week's own shape, not a misread Board.
    answer["partial"] = 0
    if companies and answer["metric"] != "new":
        for line in answer["series"]:
            line["points"] = list(line["points"])
        answer["partial"] = drop_partial_reads(answer["series"])
    view = _View(
        metric=answer["metric"],
        drilled=bool(answer.get("family")),
        split_company=answer.get("split_by") == "company",
        stamps=answer["stamps"],
        totals=answer.get("totals") or [],
        notes=_notes(answer),
        pick_series=answer.get("pick_series") or {},
        pick_parts=answer.get("pick_parts") or {},
        pick_turnover=answer.get("pick_turnover") or {},
        company_totals=answer.get("company_totals") or {},
        evicted=answer.get("evicted") or [],
        company_keys=[c["key"] for c in companies],
        picked=bool(companies),
    )
    return answer, view


def net_answer(answer: dict) -> dict:
    """``answer`` (``TrendHistory.answer``'s payload) with every line's netting added. Pure: the
    answer passed in is not changed."""
    answer, view = _viewed(answer)
    stamps = view.stamps
    notes = view.notes
    split_company = view.split_company
    company_totals = answer.get("company_totals") or {}
    for line in answer["series"]:
        line.update(
            _netted(
                view,
                _Line(
                    line["name"],
                    line["points"],
                    turnover=line.get("turnover"),
                    denominators=company_totals.get(line["name"])
                    if split_company
                    else None,
                ),
            )
        )
    parts = [line.get("turnover") for line in answer["series"] if line.get("turnover")]
    series_sum = _Line(
        _TOTAL,
        _sum_points([line["points"] for line in answer["series"]], len(stamps)),
        turnover={
            metric: _sum_points([part[metric] for part in parts], len(stamps))
            for metric in ("opened", "closed", "recounted")
        }
        if parts
        else None,
    )
    answer["series_sum"] = {
        "name": _TOTAL,
        "points": series_sum.points,
        "turnover": series_sum.turnover,
        **_netted(view, series_sum),
    }
    answer["totals_net"] = _net(view, view.totals, None, None, True)
    answer["notes"] = notes
    return answer
