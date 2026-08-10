#!/usr/bin/env python3
"""Plan the scrape fan-out — the scrape-planner of ADR-0026 (ADR-0025 Phase 2).

Runs once, before the scrape matrix. It selects this run's board slice exactly as the monolith
``scrape`` does (``pick_boards``: priority-first + a random exploration tail, capped at
``--max-boards``), then splits the *selected* boards across a dynamic number of shards:

- **Which board goes where** is an LPT bin-pack by each Board's **measured scrape seconds**
  (``board_cost.csv``, ADR-0027), so the shards' wall times balance. A Board with no measurement
  yet is estimated from its ATS's median. Until the ledger exists at all, this falls back to the
  ADR-0026 heuristic (tech-job EWMA × a detail-ATS weight) — which measurement showed carries no
  signal: it rated 14 shards identical and they ran 60 s to 1,222 s.
- **Shard count** follows the same unit: ~``--target-seconds`` of measured work per shard, clamped
  to ``--max-shards``. A full slice saturates the lanes, a small one collapses to a single shard.
  (Cold start has no seconds, so it sizes by ``--target-boards`` instead.)
- With real seconds the planner can also **predict the makespan**, which is what sizes
  ``pipeline.yml``'s ``timeout 60m`` scrape budget rather than a guess.

Each shard runs on its own runner/IP, so keeping per-shard workers at the monolith default (this
planner does not touch ``HEADSTART_WORKERS``) makes every ATS host see a shard as one ordinary
monolith from a distinct IP — per-IP load is unchanged (ADR-0026, "cost-balanced, per-IP safety").

Writes one ``shard-{k}.jsonl`` (``{ats, slug, name}`` per board, priority-desc so a time-boxed shard
scrapes its best boards first) + a ``plan.json`` (``shards`` matrix + board ``count``) the workflow reads.

Run: python -m headstart.ingest.scrape_plan [--max-boards 20000] [--max-shards 15]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from headstart import log
from headstart.board_cost import costs_for
from headstart.board_cost import load as load_cost_ledger
from headstart.board_priority import load_scores, pick_boards
from headstart.config import load_active_companies
from headstart.ingest import REPO_ROOT
from headstart.ingest.binpack import lpt_pack, shard_count

_log = log.get(__name__, __spec__)

_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"
_COST = REPO_ROOT / "data" / "state" / "board_cost.csv"
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
    out_dir: Path, *, shards: list[int], count: int, per_shard: list[int]
) -> None:
    (out_dir / "plan.json").write_text(
        json.dumps(
            {"shards": shards, "count": count, "per_shard_boards": per_shard}, indent=2
        ),
        encoding="utf-8",
    )
    print(json.dumps({"shards": shards, "count": count}), flush=True)


def main() -> int:
    log.setup()
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
        "--target-seconds",
        type=float,
        default=_TARGET_SECONDS,
        help="~measured seconds per shard (sizes the fan-out once costs exist)",
    )
    ap.add_argument(
        "--target-boards",
        type=int,
        default=_TARGET_BOARDS,
        help="cold-start only: ~boards per shard before any measurements exist",
    )
    args = ap.parse_args()

    companies = load_active_companies(Path(args.ledger), min_jobs=0)
    scores = load_scores(Path(args.priority))
    companies = pick_boards(companies, scores, args.max_boards)
    n = len(companies)
    priority = sum(1 for c in companies if scores.get(f"{c.ats}:{c.slug}", 0.0) > 0.0)
    _log.info(f"slice: {n} boards ({priority} priority + {n - priority} exploration)")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind

    if n == 0:
        _write_plan(out_dir, shards=[], count=0, per_shard=[])
        _log.info("no active boards — emitted empty plan")
        return 0

    # Pack on measured seconds when the ledger has them (ADR-0027); fall back to the ADR-0026
    # heuristic only until the first run has populated it.
    keys = [f"{c.ats}:{c.slug}" for c in companies]
    cost_rows = load_cost_ledger(Path(args.cost))
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
            _coldstart_cost(c.ats, scores.get(k, 0.0)) for c, k in zip(companies, keys)
        ]
        sizing_total, sizing_target = float(n), float(args.target_boards)
        _log.info(
            "cost: no measurements yet — cold-start heuristic (ADR-0026); "
            "the join writes data/state/board_cost.csv and the next run packs on seconds"
        )

    total_cost = sum(costs)
    m = shard_count(sizing_total, n, args.max_shards, sizing_target)
    assign, loads = lpt_pack(costs, m)

    shard_boards: list[list[int]] = [[] for _ in range(m)]
    for i, k in enumerate(assign):
        shard_boards[k].append(i)
    per_shard: list[int] = []
    for k in range(m):
        # priority-desc within a shard: a time-boxed shard scrapes its highest-value boards first
        shard_boards[k].sort(
            key=lambda i: scores.get(f"{companies[i].ats}:{companies[i].slug}", 0.0),
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

    _write_plan(out_dir, shards=list(range(m)), count=n, per_shard=per_shard)
    tail = (
        f"; predicted makespan ~{max(loads) / 60:.1f} min "
        f"(total work Σ {total_cost / 60:.1f} min)"
        if measured
        else " (cold-start cost units)"
    )
    _log.info(f"{n} boards across {m} shards{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
