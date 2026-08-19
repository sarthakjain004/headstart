#!/usr/bin/env python3
"""Board errors, budget kills, deferrals and quarantines — what a run lost, and where.

Four distinct failure surfaces, deliberately reported together because they are easy to confuse
and they have different fixes:

**Board errors** (`done: ... (N board errors)`) are boards whose scrape raised. The per-shard
count is on the `done:` line; the classes are digested into a `N board errors: ...` warning. These
are usually origin-side (HTTP 4xx/5xx, connection resets) and their fix is retry policy or a
scraper change.

**Budget kills** (`time budget reached after N min`) are shards that ran out of wall clock and
banked a partial fragment. Everything they had not reached is *deferred* to the next run and named
in the log. A shard that is killed repeatedly is not a flaky shard; it is a shard carrying a board
too big for the budget, and the deferral names it.

**Deferred boards** are the names above. They matter more than the count: a count sends the next
reader to diff the assignment artifact against the fragment to learn which board ate the shard,
which is how `workday:dollartree/dollartreeus` was found by hand. The names collapse that to a
grep.

**Quarantines** (`update_ledgers: quarantined ats:slug (N strikes, ...)`) are boards struck off
after consecutive failures (ADR-0058). These are the durable outcome — a quarantined board stops
being scraped — so a sudden burst is worth reading as a symptom of the run rather than of the
boards.

**Read the error count against the shard's egress state, not on its own.** A shard that lost its
spare egress eats rate-limit walls that surface here as errors and deferrals, so a spike can be an
egress artefact rather than a board problem. Cross-check with `fanout_retries.py` before treating
an error spike as a scraper defect.

Run: python scripts/runlog/fanout_errors.py 32272854468
     python scripts/runlog/fanout_errors.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re

from run_logs import Run, common_args, runs_from, unstamp

DONE = re.compile(
    r"\[scrape_run\] done: (\d+) jobs from (\d+) boards in (\d+)s \((\d+) board errors\)"
)
ERR_DIGEST = re.compile(r"\d+ board errors: (.+)")
KILLED = re.compile(r"time budget reached after ([\d.]+) min")
DEFERRED = re.compile(r"\[scrape_run\] deferred: (.+)")
QUARANTINE = re.compile(
    r"\[update_ledgers\]\s+quarantined\s+(\S+) \((\d+) strikes, ([^)]+)\)"
)
FAILED = re.compile(r"\[scrape_run\] (\S+?) failed after (\d+)s: (\w+)")


def scrape_errors(run: Run) -> None:
    rows, digests, kills, deferrals, classes = [], [], [], [], {}
    for shard, _job, text in run.stage("scrape"):
        m = DONE.search(text)
        if m:
            rows.append((shard, int(m.group(1)), int(m.group(2)), int(m.group(4))))
        for d in ERR_DIGEST.findall(text):
            digests.append((shard, unstamp(d)))
        for k in KILLED.findall(text):
            kills.append((shard, float(k)))
        for d in DEFERRED.findall(text):
            deferrals.append((shard, unstamp(d)))
        for f in FAILED.findall(text):
            classes[f[2]] = classes.get(f[2], 0) + 1

    if rows:
        print(
            f"{'sh':>4}{'jobs':>9}{'boards':>8}{'errors':>8}{'err/1k boards':>15}",
            flush=True,
        )
        for shard, jobs, boards, errs in rows:
            rate = 1000 * errs / boards if boards else 0
            print(f"{shard:>4}{jobs:>9,}{boards:>8}{errs:>8}{rate:>15.1f}", flush=True)
        tot_err = sum(r[3] for r in rows)
        tot_b = sum(r[2] for r in rows)
        print(
            f"{'Σ':>4}{sum(r[1] for r in rows):>9,}{tot_b:>8}{tot_err:>8}"
            f"{1000 * tot_err / tot_b if tot_b else 0:>15.1f}",
            flush=True,
        )
        worst = max(rows, key=lambda r: r[3])
        if worst[3] > 3 * (tot_err / len(rows)):
            print(
                f"  NB: shard {worst[0]} carries {worst[3]} of {tot_err} errors "
                f"({100 * worst[3] / tot_err:.0f}%) — check its egress in fanout_retries.py",
                flush=True,
            )
    if classes:
        print(
            "  failure classes: "
            + ", ".join(
                f"{k} {v}" for k, v in sorted(classes.items(), key=lambda kv: -kv[1])
            ),
            flush=True,
        )
    for shard, d in digests:
        print(f"  shard {shard}: {d}", flush=True)
    if kills:
        print(f"\n  {len(kills)} shard(s) hit the time budget:", flush=True)
        for shard, mins in kills:
            print(f"    shard {shard} killed at {mins:.1f} min", flush=True)
    else:
        print("\n  no shard hit its time budget", flush=True)
    for shard, d in deferrals:
        print(f"    shard {shard} deferred: {d}", flush=True)


def quarantines(run: Run) -> None:
    jobs = run.stage_jobs("join")
    if not jobs:
        return
    found = QUARANTINE.findall(run.log(jobs[0]))
    if not found:
        print("\n  no boards quarantined this run", flush=True)
        return
    by_ats: dict[str, int] = {}
    for slug, _strikes, _why in found:
        by_ats[slug.split(":", 1)[0]] = by_ats.get(slug.split(":", 1)[0], 0) + 1
    print(
        f"\n  {len(found)} board(s) quarantined: "
        + ", ".join(
            f"{k} {v}" for k, v in sorted(by_ats.items(), key=lambda kv: -kv[1])
        ),
        flush=True,
    )
    for slug, strikes, why in found[:10]:
        print(f"    {slug} ({strikes} strikes, {why})", flush=True)
    if len(found) > 10:
        print(f"    +{len(found) - 10} more", flush=True)


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    for run in runs_from(args):
        print(f"\n===== run {run.id} head={run.head} — errors =====", flush=True)
        scrape_errors(run)
        quarantines(run)


if __name__ == "__main__":
    main()
