#!/usr/bin/env python3
"""Attribute a sharded run's wall clock to a cause you can act on.

A fan-out run's total time is owned by its **critical path** — the chain of per-stage *maxima*,
not the sum of its work. Four measurements separate a run you can speed up from one you cannot:

1. **Stage table.** `max` (wall-clock cost) beside `Σ` (compute burned). Hold them apart: a stage
   can double its work and cost nothing if it fans out wider.
2. **Critical path.** Σ of stage maxima. What it leaves against the wall clock is queueing and
   runner setup — if that remainder is large the run is waiting on infrastructure and no code
   change touches it.
3. **Floor ratio.** Per shard, `slowest single board ÷ shard wall`. Shard wall obeys
   `max(Σ work ÷ concurrency, slowest single item)`, and the second term is a floor no packer
   beats. A shard at 96% is one board wearing a shard as a costume; the fix is a per-board
   timeout or splitting that board, never a better cost estimate. Read the `board_seconds`
   percentiles beside it: a p50 of 0.7s against a max of 2,393s is the shape that makes
   stragglers, and no mean will show it to you.
4. **Yardstick.** `actual/predicted`, which `scrape_run` computes for itself and prints on its
   `done:` line.

**Two different predictions exist and confusing them will mislead you.** `scrape_plan` logs
`shard N: 1336 boards (~136.3 min)` — that is *serial* board-seconds for the shard's assignment,
before any within-shard concurrency. `scrape_run` logs `predicted 20.3 min` — the shard *wall*
estimate, which is the serial figure divided by the learned fan-out speedup (ADR-0054). Comparing
a shard's real wall against the `scrape_plan` number makes a healthy cost model look 3-10x wrong.
Only the `scrape_run` ratio is a like-for-like yardstick, so it is the one reported here; the
plan figure is shown separately and labelled as serial.

Per-item rates (s/board) move with the input mix, so they compare runs only when the mix is
identical and it rarely is. The predicted/actual ratio is the fixed rule the mix cancels against.

Run: python scripts/runlog/fanout_timing.py 32272854468
     python scripts/runlog/fanout_timing.py 32261793515 32272854468   # compare
     python scripts/runlog/fanout_timing.py --latest pipeline.yml -n 2
"""

from __future__ import annotations

import ast
import re
import statistics

from run_logs import Run, common_args, runs_from

SLOW_BOARD = re.compile(r"slow board ([a-z]+):(\S+?): (\d+) jobs in (\d+)s")
DONE = re.compile(
    r"\[scrape_run\] done: (\d+) jobs from (\d+) boards in (\d+)s \((\d+) board errors\)"
    r" \| board seconds (\{[^}]+\})(?: \| predicted ([\d.]+) min, actual/predicted ([\d.]+)x)?"
)
PLAN_SHARD = re.compile(r"\[scrape_plan\] shard (\d+): (\d+) boards \(~([\d.]+) min\)")


def stage_table(run: Run) -> float:
    stages = {}
    for j in run.jobs:
        stages.setdefault(run.stage_of(j["name"]), []).append(run.seconds(j) / 60)
    order = run.stages()
    print(
        f"{'stage':14}{'n':>4}{'max_m':>8}{'Σ_m':>9}{'mean_m':>8}{'min_m':>8}",
        flush=True,
    )
    critical = 0.0
    for s in order:
        ds = stages[s]
        critical += max(ds)
        print(
            f"{s:14}{len(ds):4}{max(ds):8.1f}{sum(ds):9.1f}"
            f"{statistics.mean(ds):8.1f}{min(ds):8.1f}",
            flush=True,
        )
    wall = run.wall_minutes()
    print(
        f"wall {wall:.1f}m = critical path {critical:.1f}m + {wall - critical:.1f}m queue/setup",
        flush=True,
    )
    if order:
        owner = max(order, key=lambda s: max(stages[s]))
        print(
            f"owner: {owner} at {max(stages[owner]):.1f}m "
            f"({100 * max(stages[owner]) / wall:.0f}% of wall)",
            flush=True,
        )
    return critical


def floor_table(run: Run) -> None:
    rows = []
    for shard, job, text in run.stage("scrape"):
        items = [(int(m[3]), f"{m[0]}:{m[1]}") for m in SLOW_BOARD.findall(text)]
        done = DONE.search(text)
        spread = ast.literal_eval(done.group(5)) if done else {}
        ratio = float(done.group(7)) if done and done.group(7) else None
        pred = float(done.group(6)) if done and done.group(6) else None
        rows.append(
            (
                run.seconds(job),
                max(items) if items else (0, "-"),
                shard,
                spread,
                pred,
                ratio,
            )
        )
    if not rows:
        return
    print("\n-- scrape: floor vs shard wall --", flush=True)
    print(
        f"{'sh':>4}{'wall_s':>8}{'floor_s':>9}{'floor%':>8}"
        f"{'p50':>7}{'p90':>7}{'p99':>8}{'pred_m':>8}{'a/p':>6}  slowest board",
        flush=True,
    )
    for secs, top, shard, spread, pred, ratio in sorted(rows, reverse=True):
        share = 100 * top[0] / secs if secs else 0
        print(
            f"{shard:>4}{secs:8.0f}{top[0]:9d}{share:7.0f}%"
            f"{spread.get('p50', 0):7.1f}{spread.get('p90', 0):7.1f}{spread.get('p99', 0):8.1f}"
            f"{pred or 0:8.1f}{ratio or 0:6.2f}  {top[1][:44]}",
            flush=True,
        )
    ratios = [r[5] for r in rows if r[5]]
    if ratios:
        print(
            f"  actual/predicted: median {statistics.median(ratios):.2f}  "
            f"min {min(ratios):.2f}  max {max(ratios):.2f}   "
            "(>1 means the shard ran longer than its own wall estimate)",
            flush=True,
        )
    worst = max(rows)
    if worst[1][0] / worst[0] > 0.8:
        print(
            f"  NB: shard {worst[2]} is {100 * worst[1][0] / worst[0]:.0f}% one board "
            f"({worst[1][1]}) — floor-bound. A better packer cannot help it.",
            flush=True,
        )


def plan_note(run: Run) -> None:
    jobs = run.stage_jobs("scrape-plan")
    if not jobs:
        return
    pred = [float(m[2]) for m in PLAN_SHARD.findall(run.log(jobs[0]))]
    if pred:
        print(
            f"\n-- scrape_plan (serial board-seconds, NOT a wall estimate) --\n"
            f"  {statistics.mean(pred):.1f}m/shard, spread {max(pred) - min(pred):.1f}m "
            f"over {len(pred)} shards",
            flush=True,
        )
        if max(pred) - min(pred) < 0.05 * statistics.mean(pred):
            print(
                "  shards planned near-equal (<5% spread) — the planner is solving "
                "Σ/concurrency while the floor decides the wall.",
                flush=True,
            )


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    runs = runs_from(args)
    for run in runs:
        print(
            f"\n===== run {run.id} head={run.head} ({len(run.jobs)} jobs) =====",
            flush=True,
        )
        stage_table(run)
        floor_table(run)
        plan_note(run)
    if len(runs) > 1:
        print("\n===== comparison =====", flush=True)
        for run in runs:
            print(
                f"  {run.id} ({run.head}): wall {run.wall_minutes():.1f}m", flush=True
            )
        print(
            "  NB: one run against one run is an anecdote. Before crediting code for a delta, "
            "diff the SHAs over the paths the pipeline actually runs — a rename or a workflow-only\n"
            "  commit moves the SHA without changing behaviour.",
            flush=True,
        )


if __name__ == "__main__":
    main()
