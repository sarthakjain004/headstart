#!/usr/bin/env python3
"""The embed matrix, actual vs plan — the stage this pipeline's own wall-clock is usually owned
by (`fanout_timing.py`'s stage table already shows this; nothing before this file explained why a
given shard was slow).

`embed_run` shards are stateless: `embed_plan` (in the `join` job, see `fanout_plan.py`) already
did the corpus scan, the English gate, tokenizing, and bucket-sorting, so a shard just encodes the
Docs it was handed. That means embed has none of scrape's per-Board floor mechanism — cost is a
token-bucket lookup (`_S_PER_DOC`), not a measured per-item cost — so an even plan-predicted spread
is normal, and a wide ACTUAL spread across shards is the interesting finding, not expected noise.

**The confound this file exists to catch: device.** `embed_run` logs `loading {model} on {device}
...` once per shard — `cpu`, `mps`, or `cuda` depending on the runner it landed on. Throughput
differs by an order of magnitude between them. Comparing `jobs/s` or wall-clock across shards, or
across runs, without checking they ran on the same device turns a hardware difference into a
phantom performance story. This is the yardstick step (`SKILL.md` §4) applied to embed specifically
— report device beside every rate.

**Yardstick.** Unlike `scrape_run`, `embed_run` does not log its own predicted-vs-actual ratio —
there is no per-shard prediction inside the embed job's log. This file computes one anyway by
joining `embed_plan`'s per-shard prediction (`shard k: N docs, ~M min`, logged in the `join` job)
against this shard's real wall-clock (`started_at`/`completed_at` from the Actions API) — the same
join `fanout_timing.py` does within a single job, done here across two jobs.

Run: python scripts/runlog/fanout_embed.py 32272854468
     python scripts/runlog/fanout_embed.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re
import statistics
from typing import NamedTuple

from run_logs import Run, common_args, runs_from

LOADING = re.compile(r"\[embed_run\] loading (\S+) on (\S+) \.\.\.")
ASSIGNMENT = re.compile(r"\[embed_run\] assignment: (\d+) docs from \S+ \| (.+)")
DONE = re.compile(
    r"\[embed_run\] done: shard embedded (\d+) \((\d+) failed\) -> \S+ \((\d+) vectors\)"
)
BATCH_FAILED = re.compile(r"batch FAILED \(([^)]+)\)")
WEDGED = re.compile(r"(\d+) consecutive failures — allocator looks wedged")
PLAN_SHARD = re.compile(r"\[embed_plan\] shard (\d+): (\d+) docs, ~([\d.]+) min")


class Shard(NamedTuple):
    shard: int | None
    wall_s: float
    model: str | None
    device: str | None
    docs: int | None
    buckets: str
    done: int | None
    failed: int
    vectors: int | None
    wedged: bool
    batch_failures: int
    predicted_min: float | None

    @property
    def ratio(self) -> float | None:
        if not self.predicted_min:
            return None
        return (self.wall_s / 60) / self.predicted_min


def _plan_predictions(run: Run) -> dict[int, float]:
    jobs = run.stage_jobs("join")
    if not jobs:
        return {}
    text = run.log(jobs[0])
    return {int(m[0]): float(m[2]) for m in PLAN_SHARD.findall(text)}


def report(run: Run) -> None:
    predicted = _plan_predictions(run)
    print(
        f"{'sh':>4}{'wall_s':>8}{'device':>8}{'docs':>7}{'done':>7}{'fail':>6}"
        f"{'vectors':>9}{'pred_m':>8}{'a/p':>6}  buckets",
        flush=True,
    )
    rows: list[Shard] = []
    for shard, job, text in run.stage("embed"):
        load = LOADING.search(text)
        asn = ASSIGNMENT.search(text)
        done = DONE.search(text)
        row = Shard(
            shard=shard,
            wall_s=run.seconds(job),
            model=load.group(1) if load else None,
            device=load.group(2) if load else None,
            docs=int(asn.group(1)) if asn else None,
            buckets=asn.group(2) if asn else "-",
            done=int(done.group(1)) if done else None,
            failed=int(done.group(2)) if done else 0,
            vectors=int(done.group(3)) if done else None,
            wedged=bool(WEDGED.search(text)),
            batch_failures=len(BATCH_FAILED.findall(text)),
            predicted_min=predicted.get(shard) if shard is not None else None,
        )
        rows.append(row)
        ratio = f"{row.ratio:.2f}" if row.ratio else "-"
        print(
            f"{row.shard!s:>4}{row.wall_s:8.0f}{row.device or '?':>8}{row.docs or 0:7,}"
            f"{row.done or 0:7,}{row.failed:6}{row.vectors or 0:9,}"
            f"{row.predicted_min or 0:8.1f}{ratio:>6}  {row.buckets[:44]}",
            flush=True,
        )
    if not rows:
        print("  no embed shards in this run", flush=True)
        return

    devices = {r.device for r in rows if r.device}
    if len(devices) > 1:
        print(
            f"  NB: shards ran on MIXED devices this run — {sorted(devices)}. Rates and "
            "wall-clock are not comparable across them; split before drawing any conclusion.",
            flush=True,
        )
    elif devices:
        print(f"  all shards ran on: {devices.pop()}", flush=True)

    ratios = [r.ratio for r in rows if r.ratio]
    if ratios:
        print(
            f"  actual/predicted: median {statistics.median(ratios):.2f}  "
            f"min {min(ratios):.2f}  max {max(ratios):.2f}",
            flush=True,
        )
    else:
        print(
            "  no plan prediction matched (embed_plan emitted 0 shards, or the join job's "
            "log wasn't available)",
            flush=True,
        )

    tot_done = sum(r.done or 0 for r in rows)
    tot_failed = sum(r.failed for r in rows)
    tot_vec = sum(r.vectors or 0 for r in rows)
    print(
        f"  Σ: {tot_done:,} embedded ({tot_failed} failed) -> {tot_vec:,} vectors written "
        "across fragments",
        flush=True,
    )
    wedged = [r for r in rows if r.wedged]
    if wedged:
        print(
            f"  NB: {len(wedged)} shard(s) hit a wedged allocator (shard(s) "
            f"{[r.shard for r in wedged]}) — those stopped early; re-run with --resume",
            flush=True,
        )
    batchy = [r for r in rows if r.batch_failures]
    if batchy:
        print(
            f"  {sum(r.batch_failures for r in batchy)} batch failure(s) across "
            f"{len(batchy)} shard(s) — see fanout_errors.py-style per-line detail via "
            "run_logs.py --stage embed --shard N",
            flush=True,
        )

    spread = sorted(r.wall_s for r in rows)
    if len(spread) > 2 and spread[-1] > 2 * statistics.median(spread):
        slowest = max(rows, key=lambda r: r.wall_s)
        print(
            f"  NB: shard {slowest.shard} ({slowest.wall_s:.0f}s) is >2x the median wall-clock "
            f"({statistics.median(spread):.0f}s) with no per-Doc floor to explain it — check "
            "device, doc count and bucket mix above before assuming a fluke.",
            flush=True,
        )


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    for run in runs_from(args):
        print(f"\n===== run {run.id} head={run.head} — embed =====", flush=True)
        report(run)


if __name__ == "__main__":
    main()
