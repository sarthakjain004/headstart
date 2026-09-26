#!/usr/bin/env python3
"""Plan the scrape fan-out — the scrape-planner of ADR-0026 (ADR-0025 Phase 2).

Runs once, before the scrape matrix. It selects this run's board slice with ``pick_boards``:
priority-first, then a Tail that rotates through the unscored Boards oldest look first
(ADR-0229), capped at ``--max-boards``, with part of that Tail reserved for Boards holding
unsettled descriptions (ADR-0062). It then splits the *selected* boards across a dynamic number of
shards:

- **Which board goes where** is an LPT bin-pack by each Board's **measured scrape seconds**
  (``board_cost.csv``, ADR-0027), so the shards' wall times balance. A Board with no measurement
  yet is estimated from its ATS's median. Until the ledger exists at all, this falls back to the
  ADR-0026 heuristic (tech-job EWMA × a detail-ATS weight) — which measurement showed carries no
  signal: it rated 14 shards identical and they ran 60 s to 1,222 s. The pack is **grouped by
  ATS** (ADR-0047): shards are distinct network origins, so an ATS's Boards are spread across them
  rather than clustered, which spends every shard's rate-limit budget instead of a few.
- **Shard count** follows the same unit: ~``--target-seconds`` of measured work per shard, clamped
  to ``--max-shards``. A full slice saturates the lanes, a small one collapses to a single shard.
  (Cold start has no seconds, so it sizes by ``--target-boards`` instead.)
- With real seconds the planner can also **predict the makespan**, which is what sizes
  ``pipeline.yml``'s ``timeout 75m`` scrape budget rather than a guess.

Each shard runs on its own runner/IP, so keeping per-shard workers at the monolith default (this
planner does not touch ``HEADSTART_WORKERS``) makes every ATS host see a shard as one ordinary
monolith from a distinct IP — per-IP load is unchanged (ADR-0026, "cost-balanced, per-IP safety").

Writes one ``shard-{k}.jsonl`` (``{ats, slug, name}`` per board: Boards measured over a minute
first, slowest first, then priority-desc so a time-boxed shard scrapes its best boards first), a ``plan.json`` (``shards`` matrix + board ``count``) the workflow
reads, and a copy of the detail skip-list (ADR-0048, re-keyed by ADR-0050) so each shard can skip
re-fetching details we already **hold** — all three ride the one artifact the shards download.

Run: python -m headstart.ingest.scrape_plan [--max-boards 80000] [--max-shards 15]
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from headstart import log
from headstart.boards import (
    cost_ledger,
    description_gap_ledger,
    priority_ledger,
    scrapable_boards,
)
from headstart.boards.cost_ledger import BoardCost, costs_for
from headstart.boards.cost_ledger import load as load_cost_ledger
from headstart.boards.priority_ledger import load_scores, pick_boards
from headstart.ingest import (
    HELD_DETAILS_PATH,
    REPO_ROOT,
    board_failures,
    observability,
    shard_plan,
    shard_speedup,
)
from headstart.ingest.binpack import lpt_pack_capped, shard_count

if TYPE_CHECKING:
    from headstart.boards.scrapable_boards import ScrapableBoard

_log = log.get(__name__, __spec__)

# The shard's CI work budget (pipeline.yml's `timeout 75m`). Mirrored here only to warn when a
# plan predicts past it — the workflow stays the single place that enforces it.
_BUDGET_MIN = 75.0

_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"
_COST = REPO_ROOT / "data" / "state" / "board_cost.csv"
_SPEEDUP = REPO_ROOT / "data" / "state" / "shard_speedup.csv"
_FAILURES = REPO_ROOT / "data" / "state" / "board_failures.csv"
_GAP = REPO_ROOT / "data" / "state" / "board_description_gap.csv"

_OUT = REPO_ROOT / "data" / "scrape" / "assignments"

# Legacy heuristic, kept only as the cold-start path: before the cost ledger has any rows (a fresh
# repo, or the first run after ADR-0027), fall back to the ADR-0026 estimate — the board's tech
# EWMA times a per-ATS weight for the detail-fetching scrapers (grep: _DETAIL_WORKERS / fan_out).
# It is a poor proxy (see ADR-0027) but it beats packing blind, and it self-replaces after one run.
_DETAIL_ATS = frozenset(
    {
        "join",
        "keka",
        "ripplehire",
        "rippling",
        "smartrecruiters",
        "trakstar",
        "workday",
        "zoho",
    }
)
_DETAIL_WEIGHT = 6.0
_EXPLORE_BASELINE = 5.0  # unscored board with no measurement and no history to size by

# The value gate (ADR-0064). A shard's wall clock is set by its single slowest Board — LPT
# balances the *sum*, but a Board is indivisible, so the makespan floor is the biggest item and
# no amount of re-packing moves it. Measured on run 32133497258: every shard finished 1,200 of
# its ~1,340 Boards in 6-9 min, then sat on a handful of giants for the rest of the hour, and in
# eight of fifteen shards the wall clock was within a minute of that one Board.
#
# So the lever is which giants are worth an hour. Only 12 Boards in a 68,715-row ledger cost
# more than 15 min, and their tech yield per minute of shard time splits cleanly in two: hcltech
# 124-146, EY 24, walmart 20, target 7.1, paradox 5.7 — then a gap — compass 1.3, viacomcbs 0.9,
# REWE 0.5, lidl 0.3, dollartree 0.2, advanceauto 0.03, cbscorporation 0.01. Anything in the gap
# separates the same two sets, which is why this is a threshold and not a tuned parameter.
#
# The floor was 15 min while shards ran close to their 60 min budget. By 2026-09-24 they finished
# in ~9 min, so a 10-15 min Board set the wall clock unjudged: `jibe:petsmart`, 760 s at a score
# of 2.8 (4 tech jobs). On score, petsmart and greatclips sat far under 2 tech/min, ulta on the
# line (2.08), and no fresh Board between 6 and 10 min was under it (ADR-0064 amendment).
# ADR-0229 lengthened shards to a predicted ~25 min and kept this floor until they are measured.
_GATE_FLOOR_S = 600.0  # 10 min: just above the ~9 min a shard took when it was set
_GATE_MIN_TECH_PER_MIN = 2.0  # tech jobs per minute of shard time, in the gap above
# A gated Board is not scraped, so its cost and score freeze — and evidence that cannot change
# makes the gate a one-way door. Expiring the measurement re-admits it for one run every so
# often, where it is measured again and judged on what it is now. The cost of being wrong is
# then one shard-hour a fortnight, not a Board lost forever.
_GATE_RECHECK_DAYS = 14
# A Board whose last complete scrape found nothing is judged from a lower floor. Its yield is known
# to be zero, so there is no ratio to protect and only the seconds matter: on run 36218633315
# `jibe:commonspirit` read 0 jobs in 515 s, started last on its shard and lengthened the whole
# stage by ~339 s, and the 10-min floor above kept the gate from ever looking at it (ADR-0242).
_GATE_ZERO_FLOOR_S = 120.0

# The Tail back-off (ADR-0242). An unscored Board whose last complete scrape found no postings is
# read at most once per this interval rather than every rotation (~1.5 runs at an 80k Slice). Of
# 52,890 complete empty looks across the seven runs of 2026-09-25/26, 7 were followed by a
# non-empty one; meanwhile ~2,200 empty ADP Boards cost ~10 serial hours of every run.
_EMPTY_BACKOFF = timedelta(hours=24)

# Boards measured slower than this start first in their shard, whatever their score (ADR-0242). A
# shard's wall clock ends with its last Board, so a slow one submitted last — the priority order
# puts every zero-score Board there — runs alone past everything else: `jibe:commonspirit` started
# at t=1,181 s and ran 515 s on run 36218633315.
_LONG_BOARD_S = 60.0


def _measured_nothing(row: BoardCost) -> bool:
    """Did this Board's last **complete** scrape find no postings at all?

    Named rather than inlined because two places ask it — the veto in :func:`_gated_boards` and
    the log line that reports why a Board went — and a rule encoded twice is a rule that drifts.
    `None` is deliberately not "nothing": it means no complete scrape has ever measured this Board
    (see `BoardCost.jobs`), which is a reason to keep looking, not a reason to stop.
    """
    return row.jobs == 0


def _gated_boards(
    keys: list[str],
    cost_rows: Mapping[str, BoardCost],
    scores: Mapping[str, float],
    *,
    today: str | None = None,
) -> dict[str, float]:
    """Boards whose measured hour buys too little tech to be worth a shard's makespan.

    One key per Board — `cost_ledger.key_for`, equal to `priority_ledger.key_for` — reads both ledgers
    since ADR-0096. It used to take a *pair*, because the cost ledger was keyed `{ats}:{slug}` and
    the priority ledger by `board_key`, and reading one with the other's key is what left every
    Workday board unscored (ADR-0049). Returns ``{board_key: tech per minute}`` — the number, not
    just the verdict, so the caller can log why each Board went.

    Only ever judges a Board on **its own** measurement. An unmeasured Board is costed from its
    ATS's median by :func:`costs_for`, and gating on that would drop a Board for its ATS's
    reputation before it ever had a record of its own.

    That includes the score itself. ``scores`` is a *carried* priority-ledger value — a scrape
    that yields zero jobs writes no priority row to decay it (``priority_ledger.update``: a Board
    absent from the snapshot carries its row unchanged), so a Board whose real yield has
    collapsed to zero can keep a stale non-zero score forever. ``BoardCost.jobs`` has no such
    hole: ``cost_ledger.update`` overwrites it unconditionally for every Board this run actually
    measured, whether it yielded anything or not. So a measured zero there is trusted over the
    score outright — the one incident this is written for (2026-09-07) is a Board that cleared
    the gate 3x over on a four-day-stale score while its own cost row said 0 jobs, every run, for
    five runs running. See :func:`_measured_nothing` for what a 0 is now guaranteed to mean.
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    gated: dict[str, float] = {}
    for key in keys:
        row = cost_rows.get(key)
        if row is None or row.seconds <= (
            _GATE_ZERO_FLOOR_S if _measured_nothing(row) else _GATE_FLOOR_S
        ):
            continue
        if _days_since(row.updated_at, today) >= _GATE_RECHECK_DAYS:
            continue  # measurement expired — re-admit it and measure again
        # Measured 2026-09-07, `careers.te.com` read 6.32 tech/min — clear of the threshold —
        # while returning 0 jobs in five consecutive runs and setting the whole scrape stage's
        # makespan. ADR-0145 has the full reasoning; ADR-0115's writeup has the episode that
        # exposed it.
        #
        # The Board's own measured `jobs` is the half that stayed current, so it gets a veto.
        # Units are safe without reinterpreting the threshold: tech jobs are a subset of all jobs,
        # so a scrape that found **no** jobs found no tech ones either.
        #
        # What makes the veto safe is that `cost_ledger` now says whether a 0 is a *finding*. It
        # used to mean four things — a real empty Board, a raise, a first-ever budget kill, and a
        # scrape whose every id was a duplicate — because `harvest` records `n_fresh = 0`
        # regardless of outcome. An errored or unfinished run no longer overwrites a known count,
        # and where none was ever known the ledger writes **None**. So a 0 reaching here is a
        # complete run that found nothing, and None — never measured — falls through to the ratio.
        # A guard on the old field would have gated any giant that failed once for a fortnight;
        # `run_one`'s own comment names that hazard, and errors run 19-40 a run.
        tech_per_min = (
            0.0 if _measured_nothing(row) else scores.get(key, 0.0) / (row.seconds / 60)
        )
        if tech_per_min < _GATE_MIN_TECH_PER_MIN:
            gated[key] = tech_per_min
    return gated


def _days_since(updated_at: str, today: str) -> float:
    """Whole days between two stamps' dates; ``inf`` if the stored one is unreadable.

    ``updated_at`` is a UTC timestamp since ADR-0229 and a bare ``YYYY-MM-DD`` before it; only its
    date counts, so both read the same.

    Unreadable reads as ancient on purpose: the gate then re-admits the Board and re-measures
    it, which is the safe direction — a bad date must never be grounds for dropping work.
    """
    fmt = "%Y-%m-%d"
    try:
        then = datetime.strptime(updated_at[:10], fmt)  # noqa: DTZ007
        now = datetime.strptime(today, fmt)  # noqa: DTZ007
    except (TypeError, ValueError):
        return float("inf")
    return float((now - then).days)


def _tail_stamps(cost_rows: Mapping[str, BoardCost]) -> dict[str, str]:
    """Each Board's last-look stamp as the Tail rotation orders it (ADR-0229, ADR-0242).

    A Board whose last complete scrape found nothing competes as if it had been looked at
    :data:`_EMPTY_BACKOFF` later than it was, so it comes round once per interval instead of once
    per rotation — and still comes round, which a skip-list would not guarantee. Only the Tail
    reads these stamps, so a Scored Board in the head is never delayed. An unreadable stamp is
    left as it is: a bad date must not be grounds for reading a Board less often.
    """
    stamps: dict[str, str] = {}
    for key, row in cost_rows.items():
        stamp = row.updated_at
        if _measured_nothing(row):
            try:
                stamp = (datetime.fromisoformat(stamp) + _EMPTY_BACKOFF).isoformat(
                    timespec="seconds"
                )
            except (TypeError, ValueError):
                pass
        stamps[key] = stamp
    return stamps


_MAX_SHARDS = 15  # == pipeline.yml `max-parallel`
_TARGET_SECONDS = (
    600.0  # ~10 min of measured work per shard; an 80k slice saturates all 15
)
_TARGET_BOARDS = (
    600  # cold-start only: ~boards per shard when there are no measurements
)


def _coldstart_cost(ats: str, score: float) -> float:
    """Cold-start cost of one board (arbitrary units — LPT only needs the ordering)."""
    return max(score, _EXPLORE_BASELINE) * (
        _DETAIL_WEIGHT if ats in _DETAIL_ATS else 1.0
    )


def _write_plan(out_dir: Path, plan: shard_plan.ScrapePlan) -> None:
    (out_dir / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    print(json.dumps({"shards": plan.shards, "count": plan.count}), flush=True)


def main() -> int:
    log.setup()
    log.context("scrape_plan")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ledger",
        default=str(_LEDGER),
        help="liveness ledger dir (default: data/validate/liveness)",
    )
    ap.add_argument(
        "--priority",
        default=str(_PRIORITY),
        help="board_priority.csv for slice order + cost",
    )
    ap.add_argument(
        "--out-dir", default=str(_OUT), help="where to write shard-*.jsonl + plan.json"
    )
    ap.add_argument(
        "--max-boards",
        type=int,
        default=80000,  # == pipeline.yml's max_boards default
        help="boards to scrape this run (0 = all live)",
    )
    ap.add_argument(
        "--max-shards",
        type=int,
        default=_MAX_SHARDS,
        help="fan-out cap (== workflow max-parallel)",
    )
    ap.add_argument(
        "--cost",
        default=str(_COST),
        help="board_cost.csv of measured scrape seconds (ADR-0027); "
        "absent/empty falls back to the ADR-0026 heuristic",
    )
    ap.add_argument(
        "--speedup-ledger",
        default=str(_SPEEDUP),
        help="shard_speedup.csv, the measured fan-out speedup the makespan divides by "
        "(ADR-0054); absent predicts serial, as before",
    )
    ap.add_argument(
        "--failures",
        default=str(_FAILURES),
        help="board_failures.csv of consecutive gone-runs; boards at/over "
        f"{board_failures.QUARANTINE_AT} strikes are skipped (absent skips nothing)",
    )
    ap.add_argument(
        "--gap",
        default=str(_GAP),
        help="board_description_gap.csv (ADR-0062); part of the Tail is reserved "
        "for its Boards, so their descriptions can finally be settled. Absent reserves nothing",
    )
    ap.add_argument(
        "--target-seconds",
        type=float,
        default=_TARGET_SECONDS,
        help="~measured seconds per shard (sizes the fan-out once costs exist)",
    )
    ap.add_argument(
        "--held-details",
        default=str(HELD_DETAILS_PATH),
        help="skip-list to ship to the shards so they skip details we already hold (ADR-0048, "
        "re-keyed by ADR-0050); absent means every detail is fetched",
    )
    ap.add_argument(
        "--target-boards",
        type=int,
        default=_TARGET_BOARDS,
        help="cold-start only: ~boards per shard before any measurements exist",
    )
    args = ap.parse_args()

    companies = scrapable_boards.load(Path(args.ledger), min_jobs=0)
    scrapable = len(companies)
    failure_rows = board_failures.load(args.failures)
    # Boards whose gone-verdict has expired come back for one run to re-earn it (ADR-0161).
    # Without this, quarantine is a one-way door: a Board removed from the slice never scrapes,
    # so it never enters `produced`, so `board_failures.update` can never clear it.
    on_parole = board_failures.paroled(
        failure_rows, datetime.now(UTC).isoformat(timespec="seconds")
    )
    quarantine = {
        board_failures.key_for(b)
        for b in board_failures.quarantined(failure_rows) - on_parole
    }
    # Boards confirmed gone (404/410) on QUARANTINE_AT consecutive scrapes — skip them here.
    # The liveness ledger stays the probe-owned truth, and `index prune` evicts only a
    # verdict parole re-confirmed, never a first-time quarantine (ADR-0206).
    # Compared in the ledger's own key form (`board_failures.key_for`, lowercased): the
    # ledger's casing and `board_key()`'s need not agree (ADR-0049).
    before = len(companies)
    companies = [c for c in companies if board_failures.key_for(c) not in quarantine]
    quarantined_out = before - len(companies)
    # Logged even at zero: a lost failures ledger reads as an empty one and silently returns every
    # quarantined Board to the slice, and a zero here is the only sign of it in the plan's log.
    # Two things make this line honest. The parole count is stated even when it is zero, so
    # a consumer never has to treat the clause as optional going forward. And the ledger's
    # own quarantined total is named, because the `of N` denominator no longer *is* that
    # total — it is the total minus parole, and a figure that quietly changed population is
    # how a number misleads (CLAUDE.md, §Counting Boards).
    _log.info(
        f"quarantine: skipped {quarantined_out} of {len(quarantine)} "
        f"confirmed-gone board(s); {len(on_parole)} re-admitted on parole, of "
        f"{len(quarantine) + len(on_parole)} quarantined"
    )
    scores = load_scores(Path(args.priority))
    # Loaded before the slice is picked, not after: the value gate (ADR-0064) needs measured
    # seconds to decide what is worth a shard's makespan, and a Board dropped after selection
    # would still have taken a slot from something that would have been scraped.
    cost_rows = load_cost_ledger(Path(args.cost))
    gated = _gated_boards(
        [cost_ledger.key_for(c) for c in companies],
        cost_rows,
        scores,
    )
    unsettled = description_gap_ledger.load(Path(args.gap))
    if gated:
        # Gap Boards the gate holds out can never drain while gated, so the slice line says so.
        gated_gap = sum(
            1
            for c in companies
            if cost_ledger.key_for(c) in gated
            and description_gap_ledger.key_for(c) in unsettled
        )
        companies = [c for c in companies if cost_ledger.key_for(c) not in gated]
        # Named, every run, not just counted. This gate removes work on purpose, and the only
        # way that stays honest is if the list is in front of whoever reads the run — a Board
        # gated in error is invisible everywhere else, because nothing downstream misses it.
        worst = sorted(gated.items(), key=lambda kv: kv[1])

        # Say *which* rule dropped each one. A Board vetoed for returning nothing and a Board with
        # a genuinely poor ratio both print `0.00/min`, and they want opposite remedies — the
        # first is usually a scraper or origin fault worth chasing, the second is the gate working
        # as designed. ADR-0064 requires this list precisely because "a Board gated in error is
        # invisible everywhere else"; an ambiguous entry only half-honours that.
        def _why(key: str, rate: float) -> str:
            row = cost_rows.get(key)
            # "last complete scrape", not "this run": an errored run carries the previous count
            # forward, so the 0 being acted on may predate the row's own date.
            reason = (
                " — last complete scrape found 0 jobs"
                if row is not None and _measured_nothing(row)
                else ""
            )
            return f"{key} ({rate:.2f}/min){reason}"

        _log.warning(
            f"value gate: skipped {len(gated)} Board(s) costing over "
            f"{_GATE_FLOOR_S / 60:.0f} min for under {_GATE_MIN_TECH_PER_MIN:.0f} tech "
            f"jobs/min, or over {_GATE_ZERO_FLOOR_S / 60:.0f} min for none — "
            + log.named_sample([_why(k, d) for k, d in worst])
        )
        # The warning's sample stops at ten; ADR-0064 wants every gated Board named every run.
        for k, d in worst:
            _log.info(f"value gate: {_why(k, d)}")
    else:
        gated_gap = 0
    # The head holds every Scored Board only while they fit (ADR-0229). Past that, the
    # lowest-scored overflow joins the Tail and waits its turn by its last look like any
    # unscored Board, which nothing downstream would notice, so it is named here.
    overflow = priority_ledger.head_overflow(companies, scores, args.max_boards)
    if overflow:
        head_cap = priority_ledger.head_slots(args.max_boards)
        _log.warning(
            f"head: {head_cap + overflow:,} Scored Boards for {head_cap:,} head slots; the "
            f"lowest-scored {overflow:,} join the Tail (ADR-0229)"
        )

    def _empty_tail(boards: list[ScrapableBoard]) -> int:
        """Unscored Boards whose last complete scrape found nothing — the Tail back-off's set."""
        return sum(
            1
            for c in boards
            if not priority_ledger.is_scored(c, scores)
            and (row := cost_rows.get(cost_ledger.key_for(c))) is not None
            and _measured_nothing(row)
        )

    empty_candidates = _empty_tail(companies)
    companies = pick_boards(
        companies,
        scores,
        args.max_boards,
        unsettled=unsettled,
        # When each Board was last looked at, so the Tail rotates oldest-first (ADR-0229), with
        # a measured-empty Board's look pushed back (ADR-0242).
        last_looked=_tail_stamps(cost_rows),
    )
    n = len(companies)
    scored = sum(1 for c in companies if priority_ledger.is_scored(c, scores))
    # The head is capped; Scored Boards past the cap were picked by the Tail (ADR-0229).
    head = (
        min(scored, priority_ledger.head_slots(args.max_boards)) if overflow else scored
    )
    _log.info(
        f"tail back-off: {_empty_tail(companies)} of {empty_candidates} measured-empty "
        f"unscored Board(s) are due this run; each comes round at most once per "
        f"{_EMPTY_BACKOFF.total_seconds() / 3600:.0f} h (ADR-0242)"
    )
    # Boards in the slice that hold unsettled descriptions — deliberately NOT reported as "the
    # quota picked N". A gap Board also reaches the slice through the head or the Tail
    # on its own, so a count phrased as quota fill would claim picks the reservation did not
    # make. What the ledger still tells us honestly is the backlog.
    gap_in_slice = sum(
        1 for c in companies if description_gap_ledger.key_for(c) in unsettled
    )
    _log.info(
        f"slice: {n} boards ({head} Head + {n - head} Tail); "
        f"{gap_in_slice} hold unsettled descriptions, out of {len(unsettled):,} gap boards "
        f"({sum(unsettled.values()):,} jobs) still to drain; {gated_gap} of them value-gated, "
        "which cannot drain while gated"
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind
    # Same reason: a re-plan with no source list must not ship the previous run's copy.
    (out_dir / HELD_DETAILS_PATH.name).unlink(missing_ok=True)

    if n == 0:
        _write_plan(
            out_dir, shard_plan.ScrapePlan(shards=[], count=0, per_shard_boards=[])
        )
        # A warning, not the INFO it was: the whole run scrapes nothing, and the causes are
        # already counted here, so the one line can say which of them emptied the slice.
        _log.warning(
            f"empty slice: {scrapable} Scrapable Boards, {quarantined_out} quarantined, "
            f"{len(gated)} gated — no shards planned"
        )
        return 0

    # Pack on measured seconds when the ledger has them (ADR-0027); fall back to the ADR-0026
    # heuristic only until the first run has populated it.
    # One keyspace: both ledgers are keyed by `board_key` (ADR-0096). They were split until
    # 2026-08-28 — cost written by `harvest` under `{ats}:{slug}`, priority from `board_identity.board_of`
    # under `board_key()` — and conflating them is what left every Workday and Personio board
    # unscored (ADR-0049). The fix was to make them agree, not to keep pairing them up.
    keys = [cost_ledger.key_for(c) for c in companies]
    measured = bool(cost_rows)  # branch once; every later format choice reads this
    if measured:
        costs = costs_for(keys, cost_rows)
        # shard count follows the same unit as the packing: seconds of work, not board count
        sizing_total, sizing_target = sum(costs), args.target_seconds
        have = sum(1 for k in keys if k in cost_rows)
        _log.info(
            f"cost: measured seconds for {have}/{n} boards ({len(cost_rows)} in ledger)"
            + ("; rest estimated from their ATS median" if have < n else "")
        )
    else:
        costs = [
            _coldstart_cost(c.ats, scores.get(priority_ledger.key_for(c), 0.0))
            for c in companies
        ]
        sizing_total, sizing_target = float(n), float(args.target_boards)
        _log.info(
            "cost: no measurements yet — cold-start heuristic (ADR-0026); "
            "the join writes data/state/board_cost.csv and the next run packs on seconds"
        )

    total_cost = sum(costs)
    m = shard_count(sizing_total, n, args.max_shards, sizing_target)
    # Grouped by ATS, not cost alone: parallel shards get distinct egress IPs, so an ATS that
    # rate-limits per origin gets one budget per shard — spreading its Boards spends all of them
    # rather than a few (ADR-0047).
    assign, loads = lpt_pack_capped(costs, [c.ats for c in companies], m)

    shard_boards: list[list[int]] = [[] for _ in range(m)]
    for i, k in enumerate(assign):
        shard_boards[k].append(i)
    per_shard: list[int] = []
    for k in range(m):
        # Measured-slow Boards first, slowest first, so none starts late and runs alone past the
        # rest (ADR-0242); then priority-desc, so a time-boxed shard scrapes its best Boards first.
        shard_boards[k].sort(
            key=lambda i: (
                (0, -costs[i])
                if measured and keys[i] in cost_rows and costs[i] > _LONG_BOARD_S
                else (1, -scores.get(priority_ledger.key_for(companies[i]), 0.0))
            )
        )
        path = out_dir / f"shard-{k}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for i in shard_boards[k]:
                c = companies[i]
                fh.write(
                    json.dumps({"ats": c.ats, "slug": c.slug, "name": c.name}) + "\n"
                )
        per_shard.append(len(shard_boards[k]))
        load = loads[k] / 60 if measured else loads[k]
        _log.info(
            f"shard {k}: {len(shard_boards[k])} boards "
            + (f"(~{load:.1f} serial min)" if measured else f"(cost ~{load:.0f})")
        )

    # Ship the per-shard prediction, not just the board counts: a shard can then say what it
    # actually cost against what it was promised, which is the only way a drifting cost model
    # shows up. Minutes only when the ledger has measured seconds — cold-start cost units
    # would be a meaningless ratio, so they are omitted rather than written as fake minutes.
    # Serial: what the pack sums to. Wall clock: that divided by the fan-out's measured speedup,
    # floored at the shard's OWN slowest Board — a global floor would over-predict every shard
    # that doesn't hold it (ADR-0054).
    per_shard_serial_minutes = [loads[k] / 60 for k in range(m)] if measured else None
    speedup = shard_speedup.load(args.speedup_ledger)
    # A missing or corrupt ledger reads as 1.0x, which over-predicts every shard and can fire the
    # budget warning below falsely — so say which one this plan divided by.
    _log.info(
        f"speedup: {speedup.ratio:.2f}x ({speedup.shards} shards, updated {speedup.updated_at})"
        if speedup.shards
        else f"speedup: cold start {speedup.ratio:.2f}x (ledger absent or unreadable at "
        f"{args.speedup_ledger})"
    )
    per_shard_minutes = (
        [
            shard_speedup.predict_minutes(
                serial,
                floor_minutes=max(
                    (costs[i] / 60 for i in shard_boards[k]), default=0.0
                ),
                ratio=speedup.ratio,
            )
            for k, serial in enumerate(per_shard_serial_minutes)
        ]
        if per_shard_serial_minutes is not None
        else None
    )

    # Ship the skip-list inside the same artifact every shard already downloads, so the scrape
    # stage can skip detail fetches for Jobs whose detail we already hold without a second
    # download path (ADR-0048). Absent on a first run — the shards then fetch every detail.
    ids_src = Path(args.held_details)
    if ids_src.exists():
        # Always shipped under the canonical name whatever --held-details was called: the shard
        # looks for that exact name, so naming the copy after the source would break it silently.
        shutil.copyfile(ids_src, out_dir / HELD_DETAILS_PATH.name)
        size_mb = ids_src.stat().st_size / 1e6
        _log.info(f"shipped {HELD_DETAILS_PATH.name} ({size_mb:.1f} MB) to the shards")
    else:
        _log.info(
            f"no {HELD_DETAILS_PATH.name} yet — shards will fetch every job detail"
        )

    _write_plan(
        out_dir,
        shard_plan.ScrapePlan(
            shards=list(range(m)),
            count=n,
            per_shard_boards=per_shard,
            per_shard_minutes=per_shard_minutes,
            per_shard_serial_minutes=per_shard_serial_minutes,
        ),
    )
    makespan = max(per_shard_minutes) if per_shard_minutes else 0.0
    tail = (
        f"; predicted makespan ~{makespan:.1f} min "
        f"(total work Σ {total_cost / 60:.1f} serial min)"
        if measured
        else " (cold-start cost units)"
    )
    _log.info(f"{n} boards across {m} shards{tail}")
    if measured:
        # The packer's own spread, which nothing logged: a planner that reports even shards is
        # doing its job on Σ÷concurrency while the slowest single board decides the outcome.
        # All three numbers are SERIAL pack minutes (`loads`), not wall clock — the makespan line
        # above already reports wall clock, and mixing the two units here once printed the
        # impossible "min 100.6 / mean 100.8 / max 37.4".
        even = total_cost / 60 / m
        widest = max(loads) / 60
        floor = max(costs) / 60 if costs else 0.0
        _log.info(
            f"predicted serial spread: min {min(loads) / 60:.1f} / mean {even:.1f} / "
            f"max {widest:.1f} min ({widest / even if even else 0:.2f}x mean); "
            f"single-board floor {floor:.1f} min"
        )
        # `even` above is SERIAL pack minutes; `floor` is WALL clock — `predict_minutes` takes
        # it as a shard's makespan floor unchanged. Comparing them raw asked "23 > 171" and could
        # never be true, so this warning stayed silent through all five runs of 2026-09-08, every
        # one of which had `predicted makespan == single-board floor` to the decimal. Dividing by
        # the same measured speedup the makespan uses puts both sides in wall minutes: 24.0 vs a
        # 13.1 min even share on run 34203005531, i.e. a floor 1.8x an even share, reported.
        # Through `predict_minutes` with no floor of its own, so this share and the
        # makespan it is compared against can never divide by different numbers.
        even_wall = shard_speedup.predict_minutes(even, 0.0, speedup.ratio)
        if floor > even_wall:
            # The packing cannot go below its slowest single item, so when one board outweighs
            # an even share the shard count is no longer the lever — that board is. Saying so
            # here stops the next person tuning the packer at a problem it cannot reach.
            # The Board named last: `fanout_plan.FLOOR_WARN` parses the line's start.
            _log.warning(
                f"one board costs {floor:.1f} min, above the {even_wall:.1f} min even share — "
                "the makespan floor is this board, not the packing: "
                + keys[costs.index(max(costs))]
            )
        if makespan > _BUDGET_MIN:
            # Worth an annotation: the planner is packing shards it expects to exceed the CI
            # budget, so any shard whose prediction is close to right gets killed mid-harvest.
            _log.warning(
                f"predicted makespan ~{makespan:.1f} min exceeds the {_BUDGET_MIN:.0f} min "
                "shard budget — shards matching their prediction will bank partials"
            )
    observability.summary(
        "Scrape plan",
        [
            f"- {n} boards across {m} shards",
            f"- predicted makespan **{makespan:.1f} min**"
            + (f" (Σ work {total_cost / 60:.1f} min)" if measured else " — cold start"),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
