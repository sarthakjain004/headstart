#!/usr/bin/env python3
"""Plan the scrape fan-out — the scrape-planner of ADR-0026 (ADR-0025 Phase 2).

Runs once, before the scrape matrix. It selects this run's board slice exactly as the monolith
``scrape`` does (``pick_boards``: priority-first + a random exploration tail, capped at
``--max-boards``, with part of that tail reserved for Boards holding unsettled descriptions —
ADR-0062), then splits the *selected* boards across a dynamic number of shards:

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
  ``pipeline.yml``'s ``timeout 60m`` scrape budget rather than a guess.

Each shard runs on its own runner/IP, so keeping per-shard workers at the monolith default (this
planner does not touch ``HEADSTART_WORKERS``) makes every ATS host see a shard as one ordinary
monolith from a distinct IP — per-IP load is unchanged (ADR-0026, "cost-balanced, per-IP safety").

Writes one ``shard-{k}.jsonl`` (``{ats, slug, name}`` per board, priority-desc so a time-boxed shard
scrapes its best boards first), a ``plan.json`` (``shards`` matrix + board ``count``) the workflow
reads, and a copy of the detail skip-list (ADR-0048, re-keyed by ADR-0050) so each shard can skip
re-fetching details we already **hold** — all three ride the one artifact the shards download.

Run: python -m headstart.ingest.scrape_plan [--max-boards 20000] [--max-shards 15]
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from headstart import board_description_gap, log
from headstart.board_cost import BoardCost, costs_for
from headstart.board_cost import load as load_cost_ledger
from headstart.board_priority import load_scores, pick_boards
from headstart.config import board_identity, load_active_companies
from headstart.ingest import (
    HELD_DETAILS_PATH,
    REPO_ROOT,
    board_failures,
    observability,
    shard_speedup,
)
from headstart.ingest.binpack import lpt_pack_capped, shard_count

_log = log.get(__name__, __spec__)

# The shard's CI work budget (pipeline.yml's `timeout 60m`). Mirrored here only to warn when a
# plan predicts past it — the workflow stays the single place that enforces it.
_BUDGET_MIN = 60.0

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
_GATE_FLOOR_S = 900.0  # 15 min: below it a Board cannot threaten a 60 min makespan
_GATE_MIN_TECH_PER_MIN = 2.0  # tech jobs per minute of shard time, in the gap above
# A gated Board is not scraped, so its cost and score freeze — and evidence that cannot change
# makes the gate a one-way door. Expiring the measurement re-admits it for one run every so
# often, where it is measured again and judged on what it is now. The cost of being wrong is
# then one shard-hour a fortnight, not a Board lost forever.
_GATE_RECHECK_DAYS = 14


def _gated_boards(
    identities: list[tuple[str, str]],
    cost_rows: Mapping[str, BoardCost],
    scores: Mapping[str, float],
    *,
    today: str | None = None,
) -> dict[str, float]:
    """Boards whose measured hour buys too little tech to be worth a shard's makespan.

    ``identities`` pairs each Board's **cost** key (``{ats}:{slug}``) with its **priority** key
    (``board_identity``); the two ledgers are keyed differently and reading one with the other's
    key is what left every Workday board unscored (ADR-0049). Returns ``{cost_key: tech per
    minute}`` — the number, not just the verdict, so the caller can log why each Board went.

    Only ever judges a Board on **its own** measurement. An unmeasured Board is costed from its
    ATS's median by :func:`costs_for`, and gating on that would drop a Board for its ATS's
    reputation before it ever had a record of its own.
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    gated: dict[str, float] = {}
    for cost_key, priority_key in identities:
        row = cost_rows.get(cost_key)
        if row is None or row.seconds <= _GATE_FLOOR_S:
            continue
        if _days_since(row.updated_at, today) >= _GATE_RECHECK_DAYS:
            continue  # measurement expired — re-admit it and measure again
        tech_per_min = scores.get(priority_key, 0.0) / (row.seconds / 60)
        if tech_per_min < _GATE_MIN_TECH_PER_MIN:
            gated[cost_key] = tech_per_min
    return gated


def _days_since(updated_at: str, today: str) -> float:
    """Days between two ``YYYY-MM-DD`` stamps; ``inf`` if the stored one is unreadable.

    Unreadable reads as ancient on purpose: the gate then re-admits the Board and re-measures
    it, which is the safe direction — a bad date must never be grounds for dropping work.
    """
    fmt = "%Y-%m-%d"
    try:
        then = datetime.strptime(updated_at, fmt)  # noqa: DTZ007
        now = datetime.strptime(today, fmt)  # noqa: DTZ007
    except (TypeError, ValueError):
        return float("inf")
    return float((now - then).days)


_MAX_SHARDS = 15  # == pipeline.yml `max-parallel`
_TARGET_SECONDS = 600.0  # ~10 min of measured work per shard; a 20k slice → ~14 shards
_TARGET_BOARDS = (
    600  # cold-start only: ~boards per shard when there are no measurements
)


def _coldstart_cost(ats: str, score: float) -> float:
    """Cold-start cost of one board (arbitrary units — LPT only needs the ordering)."""
    return max(score, _EXPLORE_BASELINE) * (
        _DETAIL_WEIGHT if ats in _DETAIL_ATS else 1.0
    )


def _write_plan(
    out_dir: Path,
    *,
    shards: list[int],
    count: int,
    per_shard: list[int],
    per_shard_minutes: list[float] | None = None,
    per_shard_serial_minutes: list[float] | None = None,
) -> None:
    plan: dict[str, object] = {
        "shards": shards,
        "count": count,
        "per_shard_boards": per_shard,
    }
    if per_shard_minutes is not None:
        plan["per_shard_minutes"] = [round(m, 2) for m in per_shard_minutes]
    if per_shard_serial_minutes is not None:
        # The packed sum, shipped *beside* the prediction rather than instead of it. The join
        # measures the fan-out's speedup against this; measuring against per_shard_minutes —
        # which is derived from the speedup — would make the estimate chase its own tail
        # (ADR-0054).
        plan["per_shard_serial_minutes"] = [
            round(m, 2) for m in per_shard_serial_minutes
        ]
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"shards": shards, "count": count}), flush=True)


def main() -> int:
    log.setup()
    observability.context("scrape-plan")
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
        default=20000,  # == pipeline.yml's max_boards default
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
        help="board_description_gap.csv (ADR-0062); part of the exploration tail is reserved "
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

    companies = load_active_companies(Path(args.ledger), min_jobs=0)
    quarantine = {
        b.lower()
        for b in board_failures.quarantined(board_failures.load(args.failures))
    }
    if quarantine:
        # Boards confirmed gone (404/410) on QUARANTINE_AT consecutive scrapes — skip them here,
        # and only here: the liveness ledger stays the probe-owned truth, and `live_keep_set`
        # (which feeds `index prune`) must not shrink, or a scraping decision would evict rows.
        # Lowercased on both sides, like every other Board-key comparison in the plan path: the
        # ledger's casing and `board_key()`'s need not agree (ADR-0049).
        before = len(companies)
        companies = [
            c for c in companies if board_identity(c).lower() not in quarantine
        ]
        _log.info(
            f"quarantine: skipped {before - len(companies)} of {len(quarantine)} "
            "confirmed-gone board(s)"
        )
    scores = load_scores(Path(args.priority))
    # Loaded before the slice is picked, not after: the value gate (ADR-0064) needs measured
    # seconds to decide what is worth a shard's makespan, and a Board dropped after selection
    # would still have taken a slot from something that would have been scraped.
    cost_rows = load_cost_ledger(Path(args.cost))
    gated = _gated_boards(
        [(f"{c.ats}:{c.slug}", board_identity(c)) for c in companies],
        cost_rows,
        scores,
    )
    if gated:
        companies = [c for c in companies if f"{c.ats}:{c.slug}" not in gated]
        # Named, every run, not just counted. This gate removes work on purpose, and the only
        # way that stays honest is if the list is in front of whoever reads the run — a Board
        # gated in error is invisible everywhere else, because nothing downstream misses it.
        worst = sorted(gated.items(), key=lambda kv: kv[1])
        _log.warning(
            f"value gate: skipped {len(gated)} Board(s) costing over "
            f"{_GATE_FLOOR_S / 60:.0f} min for under {_GATE_MIN_TECH_PER_MIN:.0f} tech "
            f"jobs/min — "
            + observability.named_sample([f"{k} ({d:.2f}/min)" for k, d in worst])
        )
    unsettled = board_description_gap.load(Path(args.gap))
    companies = pick_boards(companies, scores, args.max_boards, unsettled=unsettled)
    n = len(companies)
    priority = sum(1 for c in companies if scores.get(board_identity(c), 0.0) > 0.0)
    # Boards in the slice that hold unsettled descriptions — deliberately NOT reported as "the
    # quota picked N". With ~12k gap Boards and a ~14k random exploration tail, coincidental hits
    # dominate the ~700 reserved slots, so a count phrased as quota fill would read as progress
    # that the reservation did not make. What the ledger still tells us honestly is the backlog.
    gap_in_slice = sum(
        1 for c in companies if board_description_gap.key_for(c) in unsettled
    )
    _log.info(
        f"slice: {n} boards ({priority} priority + {n - priority} exploration); "
        f"{gap_in_slice} hold unsettled descriptions, out of {len(unsettled):,} gap boards "
        f"({sum(unsettled.values()):,} jobs) still to drain"
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind
    # Same reason: a re-plan with no source list must not ship the previous run's copy.
    (out_dir / HELD_DETAILS_PATH.name).unlink(missing_ok=True)

    if n == 0:
        _write_plan(out_dir, shards=[], count=0, per_shard=[])
        _log.info("no active boards — emitted empty plan")
        return 0

    # Pack on measured seconds when the ledger has them (ADR-0027); fall back to the ADR-0026
    # heuristic only until the first run has populated it.
    # Two keyspaces, deliberately: the cost ledger is written by `harvest` under `{ats}:{slug}`
    # and read back the same way, while the priority ledger is written from `corpus.board_of`
    # and so is keyed by `board_key()` (ADR-0049). Conflating them is what left every Workday
    # and Personio board permanently unscored.
    keys = [f"{c.ats}:{c.slug}" for c in companies]  # cost ledger
    measured = bool(cost_rows)  # branch once; every later format choice reads this
    if measured:
        costs = costs_for(keys, cost_rows)
        # shard count follows the same unit as the packing: seconds of work, not board count
        sizing_total, sizing_target = sum(costs), args.target_seconds
        have = sum(1 for k in keys if k in cost_rows)
        _log.info(
            f"cost: measured seconds for {have}/{n} boards "
            f"({len(cost_rows)} in ledger); rest estimated from their ATS median"
        )
    else:
        costs = [
            _coldstart_cost(c.ats, scores.get(board_identity(c), 0.0))
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
        # priority-desc within a shard: a time-boxed shard scrapes its highest-value boards first
        shard_boards[k].sort(
            key=lambda i: scores.get(board_identity(companies[i]), 0.0),
            reverse=True,
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
            + (f"(~{load:.1f} min)" if measured else f"(cost ~{load:.0f})")
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
        shards=list(range(m)),
        count=n,
        per_shard=per_shard,
        per_shard_minutes=per_shard_minutes,
        per_shard_serial_minutes=per_shard_serial_minutes,
    )
    makespan = max(per_shard_minutes) if per_shard_minutes else 0.0
    tail = (
        f"; predicted makespan ~{makespan:.1f} min "
        f"(total work Σ {total_cost / 60:.1f} min)"
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
            f"predicted spread: min {min(loads) / 60:.1f} / mean {even:.1f} / "
            f"max {widest:.1f} min ({widest / even if even else 0:.2f}x mean); "
            f"single-board floor {floor:.1f} min"
        )
        if floor > even:
            # The packing cannot go below its slowest single item, so when one board outweighs
            # an even share the shard count is no longer the lever — that board is. Saying so
            # here stops the next person tuning the packer at a problem it cannot reach.
            _log.warning(
                f"one board costs {floor:.1f} min, above the {even:.1f} min even share — "
                "the makespan floor is this board, not the packing"
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
