#!/usr/bin/env python3
"""Plan the scrape fan-out — the scrape-planner of ADR-0026 (ADR-0025 Phase 2).

Runs once, before the scrape matrix. It selects this run's board slice exactly as the monolith
``nightly_harvest`` does (``pick_boards``: priority-first + a random exploration tail, capped at
``--max-boards``), then splits the *selected* boards across a dynamic number of shards:

- **Shard count** is sized by board *count* (transparent: ~``--target-boards`` per shard, clamped to
  ``--max-shards``) — a full slice saturates the lanes, a small one collapses to a single shard.
- **Which board goes where** is an LPT bin-pack by a per-Board cost estimate, so the shards' wall
  times balance. Cost ≈ the board's EWMA tech-job count (``board_priority`` score) × a per-ATS
  weight: detail-fetching ATSes (a per-job request each) cost far more per job than list-only ones,
  and the 140-min budget is blown by exactly those.

Each shard runs on its own runner/IP, so keeping per-shard workers at the monolith default (this
planner does not touch ``HEADSTART_WORKERS``) makes every ATS host see a shard as one ordinary
monolith from a distinct IP — per-IP load is unchanged (ADR-0026, "cost-balanced, per-IP safety").

Writes one ``shard-{k}.jsonl`` (``{ats, slug, name}`` per board, priority-desc so a time-boxed shard
scrapes its best boards first) + a ``plan.json`` (``shards`` matrix + board ``count``) the workflow reads.

Run: python scripts/pipeline/plan_scrape.py [--max-boards 8000] [--max-shards 15]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from headstart.binpack import lpt_pack, shard_count
from headstart.board_priority import load_scores, pick_boards
from headstart.config import load_active_companies

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "data" / "validate" / "liveness"
_PRIORITY = _ROOT / "data" / "state" / "board_priority.csv"
_OUT = _ROOT / "data" / "scrape" / "assignments"

# ATSes whose scraper fetches each posting's detail in a per-board pool (grep: _DETAIL_WORKERS /
# fan_out). A detail board of N jobs costs ~N per-job requests vs one list fetch — so its per-job
# cost is far higher. The weight is coarse (LPT tolerates cost noise); tune if a shard straggles.
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
_EXPLORE_BASELINE = (
    5.0  # unscored exploration boards: assume a small board (no history to size by)
)
_MAX_SHARDS = 15  # == pipeline.yml `max-parallel`
_TARGET_BOARDS = 600  # ~boards per shard; a full 8k slice → ~13-15 shards


def board_cost(ats: str, score: float) -> float:
    """Relative scrape cost of one board (arbitrary units — LPT only needs the ordering)."""
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
        default=8000,
        help="boards to scrape this run (0 = all live)",
    )
    ap.add_argument(
        "--max-shards",
        type=int,
        default=_MAX_SHARDS,
        help="fan-out cap (== workflow max-parallel)",
    )
    ap.add_argument(
        "--target-boards",
        type=int,
        default=_TARGET_BOARDS,
        help="~boards per shard (sizes the fan-out)",
    )
    args = ap.parse_args()

    companies = load_active_companies(Path(args.ledger), min_jobs=0)
    scores = load_scores(Path(args.priority))
    companies = pick_boards(companies, scores, args.max_boards)
    n = len(companies)
    priority = sum(1 for c in companies if scores.get(f"{c.ats}:{c.slug}", 0.0) > 0.0)
    print(
        f"[plan-scrape] slice: {n} boards ({priority} priority + {n - priority} exploration)",
        file=sys.stderr,
        flush=True,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind

    if n == 0:
        _write_plan(out_dir, shards=[], count=0, per_shard=[])
        print(
            "[plan-scrape] no active boards — emitted empty plan",
            file=sys.stderr,
            flush=True,
        )
        return 0

    costs = [board_cost(c.ats, scores.get(f"{c.ats}:{c.slug}", 0.0)) for c in companies]
    m = shard_count(n, n, args.max_shards, args.target_boards)
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
        print(
            f"[plan-scrape] shard {k}: {len(shard_boards[k])} boards (cost ~{loads[k]:.0f})",
            file=sys.stderr,
            flush=True,
        )

    _write_plan(out_dir, shards=list(range(m)), count=n, per_shard=per_shard)
    print(f"[plan-scrape] {n} boards across {m} shards", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
