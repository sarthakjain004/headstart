"""Rank the Boards that are hiring hardest right now, for the Hot tab.

Reads what ``role_trends`` already wrote — the ADR-0143 Board-count snapshot and its append-only
delta ledger — and emits a small ranked artifact the Space serves as a static file. Nothing is
computed at query time: the ledgers are tens of megabytes and the answer changes once per run,
so ranking here and serving JSON is the shape the storage and free-tier budget can afford.

Runs after ``role_trends`` in the back-to-back run, because it consumes that stage's output.

## Three lenses, because "actively hiring" is three questions

``expansion`` — net change in open roles across the trailing ``WINDOW_DAYS``. *Who is actually
growing.*
The default: over the 7 days to 2026-09-21 Amazon opened **1,396** roles at a net change of
**+20** — churn at a near-constant size rather than growth, which only this lens says.

``volume`` — the roles opened across the window (ADR-0227's turnover), over the same runs
Expansion sums. *Where the most opportunity is right now.* Always led by the largest employers.
Until ADR-0227 it was ``new``, the roles first seen in the last 7 days *and still open*. That
missed a job opened and closed inside the week.

``rate`` — ``new`` as a share of the Board's open roles. *Who is moving fast for their size*,
which is the only lens that surfaces a small company a user would never otherwise find.

## Four traps, each of which silently produces a plausible wrong list

**The first delta tick is a baseline dump, not a change.** The ledger began with no prior
snapshot, so its first file emits every Board's whole stock as a delta — 196,824 rows against
the next tick's 127. Summing from it reports every Board on the index as brand new; it did, on
the first run of the prototype behind this module.

**A Board whose entire stock arrives inside the window was newly *discovered*, not newly
hiring.** Over the whole ledger and an 8-day span that was 6,996 Boards, a fifth of it; scoped
as this stage scopes it — the trailing 7 days, Boards at or above ``MIN_STOCK`` — it was 108 on
2026-09-21 (both counted with non-tech rows, before this stage dropped them). Either way they
would own every lens. ADR-0143 exists for this confound; the
exclusion here is its Hot-tab-shaped equivalent, and its count ships in the artifact rather than
being quietly applied.

**``watch:`` families double-count** against the families (ADR-0051), so they are dropped
from every total. ``non-tech`` is dropped too: the tab ranks tech hiring, and its rows link to a
trend of tech openings.

**``new`` is a rolling 7-day level, not per-tick inflow** (ADR-0051). It is read as a level and
never summed across ticks. ``stock`` deltas *are* per-tick changes and are summed.

## What this cannot answer yet

"Which company *started* hiring recently" — acceleration — needs a before and an after, and the
delta ledger only began on 2026-09-13. Do not approximate it from ``first_seen``: that records
when **we** indexed a row, not when the employer opened it, so a newly scraped Board would read
as a hiring surge. The ledger accrues every run; the question becomes answerable by waiting.
"""

from __future__ import annotations

import argparse
import collections
import csv
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from headstart import log, roles, version_spans
from headstart.board_identity import tenant
from headstart.ingest import job_turnover
from headstart.ingest.board_naming import board_names, display_name
from headstart.ingest.board_operator import classify

# `__spec__` as well as `__name__`, like every other module that doubles as a `python -m`
# entry point: run that way `__name__` is "__main__", which falls outside the `headstart`
# root that `setup()` configures, and every line is discarded. Missing both this and
# `setup()` below, the stage ran to completion in total silence.
_log = log.get(__name__, __spec__)

REPO_ROOT = Path(__file__).resolve().parents[3]
_BOARD_COUNTS = REPO_ROOT / "data" / "state" / "role_trend_board_counts.parquet"
_BOARD_DELTAS = REPO_ROOT / "data" / "state" / "role_trend_board_deltas"
_OUT = REPO_ROOT / "data" / "state" / "hot_boards.json"
# role_trends' methodology boundaries (ADR-0164), written by the stage just before this one
_EPOCHS = REPO_ROOT / "data" / "state" / "trends_epochs.csv"
# The epoch columns whose change moves a Board's stock: a centroid refit (before ADR-0220), a
# family-list or family-assignment change (ADR-0215, ADR-0220), a tech-filter change or duplicate
# removal. An extraction change (derivations) moves no count. Mirrored by app.js `LINE_MOVING`
# and the Space's `_LINE_MOVING` (ADR-0227).
_STOCK_MOVING = (
    "centroid_version",
    "family_map_fingerprint",
    "family_classifier_version",
    "tech_filter_version",
)
#: Moves only the Boards it can touch (ADR-0186/0187, #632/#649): Eightfold's, and those of a
#: Tenant with two or more Workday or Taleo Enterprise Boards — the Trends tab's own rule for a
#: company (app.js `DEDUP_ATSES`, `MIRROR_ATS`), and the Space's for the index's turnover
#: (`_DEDUP_ATSES`, `_MIRROR_ATS`, ADR-0227). Change one, change them all;
#: tests/test_space_app.py pins that the Space and this module agree.
_DEDUP = "dedup_version"
_DEDUP_SIBLING_ATSES = ("workday", "taleo_enterprise")
_DEDUP_MIRROR_ATS = "eightfold"
_DB = REPO_ROOT / "data" / "lancedb"

#: Rows kept per lens. Enough to scroll, small enough that the artifact stays a few tens of KB
#: and that the Boards on it could be adjudicated by hand — which is the stated way to improve
#: `board_operator` beyond its curated head (see that module's docstring).
TOP_N = 100

#: A Board below this many open roles is not ranked. Its lens values are dominated by noise —
#: one posting on a three-posting Board is a 33% hiring rate — and the Rate lens in particular
#: becomes a list of tiny Boards that happened to post once.
MIN_STOCK = 25

#: A Board is treated as newly discovered, not newly hiring, when this share of its current
#: stock arrived inside the delta window. Not 1.0: a Board first seen mid-window usually also
#: closes a posting or two before the window ends, which would put its ratio just under a
#: strict equality and let it back onto every lens.
NEWLY_FOUND_SHARE = 0.9

#: The trailing window Expansion is measured over. Seven days, to match the `new` metric's own
#: rolling window (ADR-0051's NEW_WINDOW_DAYS) — the two are printed on the same row, so they
#: have to describe the same length of time or the row compares a week against a month.
WINDOW_DAYS = 7

#: A Board counted for fewer days than this is too new to rank. It mirrors app.js MIN_SPAN_DAYS,
#: under which the trend a row opens reads "too new to show a direction yet".
MIN_COUNTED_DAYS = 3

_WATCH = "watch:"  # headstart.roles.WATCH_PREFIX; double-counts (ADR-0051)
# Hot counts tech roles only, as Trends and Search do: with the reserved non-tech family counted
# in, Amazon's "open now" was 9,755 on Hot against 9,229 tech openings on the trend its row
# links to.
_NON_TECH = roles.NON_TECH


def read_levels(path: Path) -> tuple[collections.Counter, collections.Counter]:
    """Current per-Board ``new`` (a rolling 7-day level) and ``stock`` totals."""
    import pyarrow.parquet as pq

    table = pq.read_table(path).to_pydict()
    new: collections.Counter = collections.Counter()
    stock: collections.Counter = collections.Counter()
    for board, metric, family, count in zip(
        table["board"], table["metric"], table["family"], table["count"], strict=True
    ):
        if family.startswith(_WATCH) or family == _NON_TECH:
            continue
        (new if metric == "new" else stock)[board] += count
    return new, stock


def counting_changes(path: Path) -> set[str]:
    """The ticks where a stock-moving epoch column changed (ADR-0164), or none without a file.

    The first row is where recording began, not a change. A tick where only duplicate removal
    changed is not here but in :func:`dedup_changes`, since it moves only some Boards."""
    return _changed_ticks(path, lambda moved: bool(moved & set(_STOCK_MOVING)))


def dedup_changes(path: Path) -> set[str]:
    """The ticks where duplicate removal, and nothing else that moves stock, changed.

    Left out only for the Boards it can touch (:func:`dedup_touches`). Left out for every
    Board, it took a run of ordinary hiring out of each other one: Hot read Google −27 and
    Amazon +58 where the trends their rows open, which keep that run, read −42 and +17."""
    return _changed_ticks(
        path, lambda moved: moved & {*_STOCK_MOVING, _DEDUP} == {_DEDUP}
    )


def _changed_ticks(path: Path, is_change) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {
        row["ts"]
        for prev, row in itertools.pairwise(rows)
        if is_change({col for col in row if row.get(col) != prev.get(col)} - {"ts"})
    }


def dedup_touches(boards) -> set[str]:
    """The Boards duplicate removal can move: every Eightfold Board, and each Board of a Tenant
    holding two or more Boards on one of the ATSes it dedupes within. Tenants compare case-blind,
    as the company directory joins them (`HPE`, `hpe`); a join by curated alias alone is not
    seen here, since this stage runs before the directory is built."""
    keyed = {b: (b.split(":", 1)[0], tenant(b).lower()) for b in boards}
    siblings = collections.Counter(
        key for key in keyed.values() if key[0] in _DEDUP_SIBLING_ATSES
    )
    return {
        b
        for b, (ats, owner) in keyed.items()
        if ats == _DEDUP_MIRROR_ATS
        or (ats in _DEDUP_SIBLING_ATSES and siblings[(ats, owner)] > 1)
    }


def read_window_sum(
    delta_dir: Path,
    changes: set[str] | frozenset[str] = frozenset(),
    dedup: set[str] | frozenset[str] = frozenset(),
    touched: set[str] | frozenset[str] = frozenset(),
    metric: str = "stock",
    arrivals: dict[str, str] | None = None,
    tally: dict[str, int] | None = None,
) -> tuple[collections.Counter, list[str]]:
    """Per-Board sum of one delta-ledger ``metric`` over the trailing window, and the tick stamps
    it covers. Under ``stock`` that is the net change. Under a turnover metric (``opened``,
    ``closed``, ADR-0227) it is the jobs opened or closed over the same runs the net change sums.

    **The window is bounded to the same span as ``new``, and that is the point.** An unbounded
    sum grows by one run every run, so Expansion would quietly measure a longer period each
    time while Volume stayed a rolling 7-day level — and a row prints the two side by side.
    Within a day of shipping, "+1,010 net roles" and "924 opened this week" would have described
    different lengths of time under one heading.

    The ledger's first tick is dropped as well. It is a baseline dump of every Board's whole
    stock rather than a change (196,824 rows against the next tick's 127), and while the ledger
    is younger than the window it would otherwise fall inside it. It is identified by position,
    which is safe *here*: this stage runs on the merge VM immediately after the stage that
    writes the directory, so the directory is complete. A caller reading a partially fetched
    copy would mistake its oldest present tick for the baseline and lose one real measurement.

    **Each version's first tick is dropped, and every other tick of every version summed**
    (ADR-0040/ADR-0143, ADR-0221). The stamp holds the series version (ADR-0220). A new series
    — a centroid refit once, a new classifier head now — finds no Board counts at its own
    version, so `role_trends` writes that version a fresh baseline of every Board's stock;
    summed, it made every Board "newly discovered" for a week. Summing the newest version alone
    instead left a 4-hour window the morning after a refit, under a card that says "this week".
    A tick is a Board's stock change whatever version counted it, so the others are summed; the
    baseline tick is itself a counting change, and its run and the next are left out with the
    others (``changes``).

    The returned stamps describe the window actually measured, never the window intended — a tab
    claiming a week over two days of data would be a lie the data can already tell.

    **A counting change is not hiring** (``changes``, from :func:`counting_changes`). Its tick
    and the one after it are left out: a tech-filter change can land over two runs — Amazon's
    Sep 17 change was +308 at its tick and −439 at the next — and Hot then called Amazon "+532
    net roles" while its "See trend" link, which leaves the change out, read it falling. The
    cost is one ordinary run of real change per counting change. A duplicate-removal change
    (``dedup``, from :func:`dedup_changes`) is left out, with its run after, only for the Boards
    it can move (``touched``) — the runs the Trends chart leaves out of the line a row opens.

    ``tally``, when given, is filled with how many ticks were read and why each dropped one was
    dropped, so a caller left with no stamps can say which rule emptied the window.
    """
    import pyarrow.parquet as pq

    # (path, first stamp) built as pairs, so a tick that somehow holds no rows drops out of
    # both lists together. Reading the two separately and zipping them `strict=True` was only
    # correct because `role_trends` never writes an empty tick — a coupling to another module's
    # behaviour that nothing here would have shown.
    ticks = []
    for path in sorted(delta_dir.glob("*.parquet")):
        table = pq.read_table(path, columns=["ts"])
        stamps_in_file = table.column("ts").to_pylist()
        if stamps_in_file:
            version = (table.schema.metadata or {}).get(b"centroid_version", b"-1")
            ticks.append((path, stamps_in_file[0], int(version)))
    if not ticks:
        return collections.Counter(), []
    # Every span's ticks (headstart.version_spans, ADR-0221), less each span's first: a refit's
    # first tick re-writes every Board's stock as a delta (a baseline), while every later tick of
    # any version is a real change. Keeping the newest version alone left Hot a 4-hour window the
    # morning after a refit, under a card that said "this week". A stray tick of a version other
    # than its span's is dropped, as the Space drops it.
    span_list = version_spans.spans((ts, version) for _, ts, version in ticks)
    baselines = {start for _, start, _ in span_list}

    # Counting changes are located on the ticks as written, baselines included: a refit's change
    # is its baseline tick, and locating it after dropping that tick took out two later runs.
    def runs_of(found: set[str] | frozenset[str]) -> set[str]:
        out: set[str] = set()
        for change in found:
            k = next((k for k, (_, ts, _) in enumerate(ticks) if ts >= change), None)
            if k is not None:
                out.update(ts for _, ts, _ in ticks[k : k + 2])
        return out

    left_out = runs_of(changes)
    # A duplicate-removal change, with its run after, only for the Boards it can move
    # (``touched``, from :func:`dedup_touches`).
    left_out_touched = runs_of(dedup)
    read = len(ticks)
    n_baselines = sum(ts in baselines for _, ts, _ in ticks)
    ticks = [
        (path, ts)
        for path, ts, version in ticks
        if ts not in baselines and version_spans.version_at(span_list, ts) == version
    ]
    if tally is not None:
        tally.update(
            ticks=read,
            baselines=n_baselines,
            stray=read - n_baselines - len(ticks),
            left_out=0,
        )
    if not ticks:
        return collections.Counter(), []
    newest = max(ts for _, ts in ticks)
    cutoff = (datetime.fromisoformat(newest) - timedelta(days=WINDOW_DAYS)).isoformat()
    if tally is not None:
        tally["left_out"] = sum(ts >= cutoff and ts in left_out for _, ts in ticks)

    moved: collections.Counter = collections.Counter()
    stamps: list[str] = []
    for path, first_ts in ticks:
        if first_ts < cutoff or first_ts in left_out:
            continue
        table = pq.read_table(path).to_pydict()
        for board, row_metric, family, delta, ts in zip(
            table["board"],
            table["metric"],
            table["family"],
            table["delta"],
            table["ts"],
            strict=True,
        ):
            if (
                row_metric == metric
                and not family.startswith(_WATCH)
                and family != _NON_TECH
                and not (first_ts in left_out_touched and board in touched)
                # A Board's first tick lands its whole backlog at once: not hiring.
                and (arrivals or {}).get(board) != ts
            ):
                moved[board] += delta
                stamps.append(ts)
    return moved, stamps


def board_arrivals(delta_dir: Path) -> dict[str, str]:
    """Each Board's first tick in the delta ledger, over every version: where its backlog landed.

    Mirrors the Space's ``_board_arrivals``, so Hot and the trend a row opens agree on when a
    Board arrived. Sphinixusa, counted from Sep 23, ranked second on Hot at "+250 net" off the
    backlog it landed with, while its trend called it too new to read."""
    import pyarrow.parquet as pq

    first: dict[str, str] = {}
    for path in delta_dir.glob("*.parquet"):
        table = pq.read_table(path, columns=["ts", "board", "metric"]).to_pydict()
        for ts, board, metric in zip(
            table["ts"], table["board"], table["metric"], strict=True
        ):
            if metric == "stock" and (board not in first or ts < first[board]):
                first[board] = ts
    return first


def young_boards(arrivals: dict[str, str], newest: str) -> set[str]:
    """The Boards counted for under ``MIN_COUNTED_DAYS`` at ``newest``: too new to rank, as the
    trend a row opens calls a company counted that briefly too new to show a direction."""
    cutoff = (
        datetime.fromisoformat(newest) - timedelta(days=MIN_COUNTED_DAYS)
    ).isoformat()
    return {board for board, first in arrivals.items() if first > cutoff}


def window_base(delta_dir: Path, first: str) -> str | None:
    """The newest tick before ``first``: the run the window's first change is measured from."""
    import pyarrow.parquet as pq

    before = []
    for path in delta_dir.glob("*.parquet"):
        stamps = pq.read_table(path, columns=["ts"]).column("ts").to_pylist()
        if stamps and stamps[0] < first:
            before.append(stamps[0])
    return max(before, default=None)


def _collapse_same_company(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per company within a lens, keeping the best-ranked Board.

    An employer routinely holds several Boards — EWOR sits on Teamtailor *and* Recruitee, and
    both reach the Rate lens, so an uncollapsed list spends two of its top five rows on one
    company. Cross-ATS dedup does not exist in the index (``index_plan.evict_duplicate`` groups
    within a Board), so this is a display-level fix and is scoped to the display: it drops the
    weaker row rather than merging the two Boards' counts, because adding them would double-count
    any posting the company lists on both.

    Callers pass rows already sorted by the lens, so "first seen wins" is "best-ranked wins".
    """
    seen: set[str] = set()
    kept = []
    for row in rows:
        key = display_name(row["company"], row["board"]).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def rank(
    new: collections.Counter,
    stock: collections.Counter,
    moved: collections.Counter,
    names: dict[str, str],
    opened: collections.Counter,
    closed: collections.Counter,
    young: set[str] | frozenset[str] = frozenset(),
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """The three lenses, plus the counts of what was ranked and what each exclusion removed.

    ``opened`` and ``closed`` are the window's turnover (ADR-0227). Volume ranks by ``opened``,
    and every row carries both, because a net change alone read Amazon's week of 914–1,532
    openings as "+17". ``young`` are the Boards too new to rank (:func:`young_boards`); they are
    counted with the newly discovered.

    Exclusions are counted and returned rather than silently applied: a tab that quietly drops a
    fifth of the ledger should say so, and the numbers are how anyone checks this stage is
    behaving. Every candidate Board is scored once and the three lenses sort the same rows, so a
    Board cannot appear as an employer on one lens and a services firm on another.
    """
    candidates, newly_found, unnamed = [], 0, 0
    for board, open_roles in stock.items():
        if open_roles < MIN_STOCK:
            continue
        if board in young or moved.get(board, 0) >= NEWLY_FOUND_SHARE * open_roles:
            newly_found += 1
            continue
        company = display_name(names.get(board, ""), board)
        # A row with no company says nothing about who is hiring, and `_collapse_same_company`
        # would fold every such Board into one (ADR-0212).
        if not company:
            unnamed += 1
            continue
        candidates.append(
            {
                "board": board,
                "ats": board.split(":", 1)[0],
                "company": company,
                "stock": open_roles,
                "new7": new.get(board, 0),
                "net": moved.get(board, 0),
                "opened": opened.get(board, 0),
                "closed": closed.get(board, 0),
                # Percent rather than a fraction: it is a display value, and rounding it here
                # keeps every consumer from inventing its own precision.
                "rate": round(100 * new.get(board, 0) / open_roles),
                "operator": classify(board, names.get(board)),
            }
        )
    # Collapse *before* the cut, never after: trimming to TOP_N first would let a company's
    # second Board occupy a slot that then disappears, leaving a lens of fewer than TOP_N rows.
    lenses = {
        "expansion": _collapse_same_company(
            sorted((c for c in candidates if c["net"] > 0), key=lambda c: -c["net"])
        )[:TOP_N],
        "volume": _collapse_same_company(
            sorted(
                (c for c in candidates if c["opened"] > 0), key=lambda c: -c["opened"]
            )
        )[:TOP_N],
        "rate": _collapse_same_company(
            sorted((c for c in candidates if c["new7"] > 0), key=lambda c: -c["rate"])
        )[:TOP_N],
    }
    shown = {c["board"]: c for lens in lenses.values() for c in lens}
    counted = collections.Counter(c["operator"] for c in shown.values())
    # Named `counts`, not `excluded`: it holds what was *kept* as well as what was dropped, and
    # a key called `excluded["ranked"]` reads as the opposite of the number it carries.
    counts = {
        "ranked": len(candidates),
        "newly_discovered": newly_found,
        "unnamed": unnamed,
        "below_min_stock": sum(1 for v in stock.values() if v < MIN_STOCK),
        # The threshold travels with the counts it explains. The UI prints "fewer than N open
        # roles", and with N hardcoded there, changing MIN_STOCK would leave that sentence
        # quietly stating a number the ranking no longer uses.
        "min_stock": MIN_STOCK,
        "services": counted["services"],
        "aggregator": counted["aggregator"],
    }
    return lenses, counts


def main() -> int:
    # `setup()` before `context()`: without it nothing this stage logs is ever emitted, so a
    # silently skipped Hot list looked exactly like a successful run in CI.
    log.setup()
    log.context("hot_boards")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board-counts", type=Path, default=_BOARD_COUNTS)
    ap.add_argument("--board-deltas", type=Path, default=_BOARD_DELTAS)
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument("--epochs", type=Path, default=_EPOCHS)
    args = ap.parse_args()

    # Both ledgers are role_trends' output, and that stage is `continue-on-error`: a run where
    # it skipped leaves them absent, which is a missing prerequisite rather than a defect here.
    if not args.board_counts.exists() or not any(args.board_deltas.glob("*.parquet")):
        _log.warning(
            f"skipping the hot list — {args.board_counts} or {args.board_deltas} is missing "
            "(role_trends writes both; it may have skipped this run)"
        )
        return 0

    from headstart.embedding_conventions import PROD_TABLE

    new, stock = read_levels(args.board_counts)
    arrivals = board_arrivals(args.board_deltas)
    # The runs and Board ticks every lens leaves out, whichever metric it sums: a counting
    # change and the run after it, a duplicate-removal change on the Boards it can move, and each
    # Board's own arrival.
    if not args.epochs.exists():
        # Said here, once, rather than in each of the two readers below that fall back to none.
        _log.info(
            f"epochs file {args.epochs} missing — counting and dedup changes not excluded"
        )
    window_rules = {
        "changes": counting_changes(args.epochs),
        "dedup": dedup_changes(args.epochs),
        # Every Board the ledger has read, not only those holding stock now: #603 can empty one
        # of a Tenant's two Workday sites, and its sibling is still one duplicate removal can move.
        "touched": dedup_touches(set(stock) | set(arrivals)),
        "arrivals": arrivals,
    }
    tally: dict[str, int] = {}
    moved, stamps = read_window_sum(args.board_deltas, **window_rules, tally=tally)
    # The window's turnover (ADR-0227), over the same runs the net change sums, so a row's three
    # figures describe one stretch of time.
    opened, turnover_stamps = read_window_sum(
        args.board_deltas, **window_rules, metric=job_turnover.OPENED
    )
    closed, _ = read_window_sum(
        args.board_deltas, **window_rules, metric=job_turnover.CLOSED
    )
    if not stamps:
        # No measured change is left to rank: only baselines exist yet, or every in-window tick
        # was left out. A lens built on a baseline would rank every Board as newly created. The
        # output is not touched, so the previous list stays served — which the line says.
        previous = "none"
        if args.out.exists():
            previous = json.loads(args.out.read_text(encoding="utf-8")).get(
                "generated_at", "undated"
            )
        _log.warning(
            f"no measured change in the window: {tally.get('ticks', 0)} tick(s) in "
            f"{args.board_deltas}, {tally.get('baselines', 0)} span baseline(s), "
            f"{tally.get('stray', 0)} of a stray version, {tally.get('left_out', 0)} left out as "
            f"counting changes — the previous hot list ({previous}) stays served"
        )
        return 0
    lenses, counts = rank(
        new,
        stock,
        moved,
        board_names(args.db, PROD_TABLE),
        opened,
        closed,
        young=young_boards(arrivals, max(stamps)),
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # `base`: the run each window's first change is measured from, so a trend opened from
        # a row starts where Hot's figure starts; from `from` it left the first tick's change out.
        "window": {
            "from": min(stamps),
            "to": max(stamps),
            "base": window_base(args.board_deltas, min(stamps)),
            # Turnover began with ADR-0227, so for its first week it covers less of the window
            # than the net change does, and the tab says from when.
            "turnover_from": min(turnover_stamps, default=None),
        },
        "lenses": lenses,
        "counts": counts,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    _log.info(
        f"hot list: {counts['ranked']} Boards ranked, "
        f"{counts['newly_discovered']} newly discovered excluded, "
        f"{counts['services']} services and {counts['aggregator']} aggregators labelled "
        f"across the window {payload['window']['from']} -> {payload['window']['to']}"
    )
    _log.info(
        f"hot list: {counts['unnamed']} unnamed and {counts['below_min_stock']} below "
        f"{counts['min_stock']} open roles excluded | lenses: "
        + ", ".join(f"{lens} {len(rows)}" for lens, rows in lenses.items())
        + f" | {len(window_rules['changes'])} counting-change tick(s) left out"
    )
    return 0


if __name__ == "__main__":
    # The step is `continue-on-error`, so an unguarded exception would end in a green run with
    # no annotation at all; one ERROR names it and says what is stale. SystemExit and
    # KeyboardInterrupt are not `Exception`, so they pass through untouched.
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - the one catch-all per entry point, logged and re-exited
        _log.error(
            "hot_boards failed — the previous hot list stays served this run",
            exc_info=True,
        )
        raise SystemExit(1) from None
