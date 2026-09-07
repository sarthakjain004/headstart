"""What the ADR-0064 value gate would drop, and what a change to it moves — before shipping one.

The gate removes work on purpose, so a change to it is a change to what the index contains. That
makes "how many Boards does this drop, and which" the only question worth asking, and it is not
answerable from the code: the answer lives in `board_cost.csv` and `board_priority.csv`, which are
gitignored HF state and move every run.

`_gated_boards` is pure and takes both ledgers as arguments, so this just calls it — against the
real ledgers, and against a stated *baseline* rule — and diffs the two sets. No reimplementation of
the gate, which would be free to drift from it.

**Read the delta, not the absolutes.** The gated count moves run to run as costs blend and scores
decay; two pulls of the ledger an hour apart gave 70 and 72 for the same code. What a change owns
is the difference: which Boards it newly drops, which it releases, and the board-minutes behind
them.

**Refresh the ledgers first.** They are HF state, so a working-tree copy is stale by however long
since the last pull, and a stale ledger will happily report a confident wrong answer:

    python -c "from huggingface_hub import snapshot_download; snapshot_download(
        'imPoseidon/headstart-index', repo_type='dataset', local_dir='.',
        allow_patterns=['data/state/board_cost.csv','data/state/board_priority.csv'])"

Run: python -u scripts/validate/gate_impact.py
     python -u scripts/validate/gate_impact.py --baseline ratio-only   # what ADR-0116 changed
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from headstart.board_cost import BoardCost
from headstart.board_cost import load as load_cost_ledger
from headstart.board_priority import load_scores
from headstart.ingest import scrape_plan as ps

ROOT = Path(__file__).resolve().parents[2]
COST = ROOT / "data" / "state" / "board_cost.csv"
PRIORITY = ROOT / "data" / "state" / "board_priority.csv"


def _ratio_only(
    keys: list[str],
    cost_rows: dict[str, BoardCost],
    scores: dict[str, float],
    today: str,
) -> dict[str, float]:
    """The pre-ADR-0116 rule: score over seconds, with no veto on a measured zero.

    Deliberately a copy rather than a flag on `_gated_boards`. A flag would be production code
    existing only to let a script describe the past, which is exactly the Speculative Generality
    the repo's review baseline names — and this copy is inert: if it drifts, the diff below
    overstates the change and someone re-reads it, rather than the gate quietly behaving as the
    baseline claims.
    """
    out: dict[str, float] = {}
    for key in keys:
        row = cost_rows.get(key)
        if row is None or row.seconds <= ps._GATE_FLOOR_S:
            continue
        if ps._days_since(row.updated_at, today) >= ps._GATE_RECHECK_DAYS:
            continue
        rate = scores.get(key, 0.0) / (row.seconds / 60)
        if rate < ps._GATE_MIN_TECH_PER_MIN:
            out[key] = rate
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--baseline",
        choices=("none", "ratio-only"),
        default="ratio-only",
        help="what to diff the live gate against ('none' just lists what it drops)",
    )
    ap.add_argument("--today", help="override the date the recheck is measured from")
    ap.add_argument("--top", type=int, default=15, help="how many Boards to name")
    args = ap.parse_args()

    for path in (COST, PRIORITY):
        if not path.exists():
            sys.exit(f"missing {path} — see this file's docstring for the HF pull")
    cost = load_cost_ledger(COST)
    scores = load_scores(PRIORITY)
    today = args.today or datetime.now(UTC).strftime("%Y-%m-%d")
    keys = list(cost)
    print(
        f"{len(cost):,} cost rows | {len(scores):,} priority rows | as of {today}\n",
        flush=True,
    )

    live = ps._gated_boards(keys, cost, scores, today=today)
    if args.baseline == "none":
        print(f"the gate drops {len(live)} Board(s):", flush=True)
        for key in sorted(live, key=lambda k: -cost[k].seconds)[: args.top]:
            row = cost[key]
            print(
                f"  {key:<52} {row.seconds:>7.0f}s jobs={row.jobs} {live[key]:.2f}/min",
                flush=True,
            )
        return 0

    base = _ratio_only(keys, cost, scores, today)
    newly, released = sorted(set(live) - set(base)), sorted(set(base) - set(live))
    reclaimed = sum(cost[k].seconds for k in newly)
    print(
        f"baseline (ratio only) drops {len(base)}; the live gate drops {len(live)}",
        flush=True,
    )
    print(
        f"  newly dropped {len(newly)}  released {len(released)}  "
        f"reclaimed {reclaimed / 60:,.1f} board-min per full pass\n",
        flush=True,
    )
    for label, group in (("NEWLY DROPPED", newly), ("RELEASED", released)):
        if not group:
            continue
        print(f"{label}:", flush=True)
        for key in sorted(group, key=lambda k: -cost[k].seconds)[: args.top]:
            row = cost[key]
            print(
                f"  {key:<52} {row.seconds:>7.0f}s jobs={row.jobs} "
                f"score={scores.get(key, 0.0):>7.1f} @{row.updated_at}",
                flush=True,
            )
    # A Board dropped for measuring zero is a different claim from one dropped for a poor ratio,
    # and only the first can be stale in the way ADR-0116's ordering note describes.
    stale_risk = [k for k in newly if cost[k].jobs == 0]
    if stale_risk:
        print(
            f"\n{len(stale_risk)} of the newly dropped read jobs=0. Each is gated on the claim "
            "that its last COMPLETE scrape found nothing — re-check that any recent fix has been "
            "measured into the ledger before merging a gate change (ADR-0116, 'the ordering is "
            "load-bearing').",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
