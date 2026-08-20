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

**Quarantines** are boards struck off after consecutive failures (ADR-0058). These are the durable
outcome — a quarantined board stops being scraped — so a sudden burst is worth reading as a symptom
of the run rather than of the boards.

**That count is a stock, not a flow.** `failures:` reports `len(quarantined(rows))` over the entire
ledger — every board currently at or over the strike threshold, most of them struck out on earlier
runs. It is not "boards quarantined this run", and reading it as inflow is the same mistake the
trends ledger already cost this repo. Compare it against the previous run's figure to get a
delta; the number alone is a level.

**Count it from the `failures:` line, never by counting `quarantined` lines.** `update_ledgers`
emits `for board in sorted(quarantined)[:20]` — a capped, *alphabetically sorted* sample. Counting
those lines reported 20 quarantines for a run whose own `failures:` line said **114**, a 5.7x
undercount, and made the per-ATS breakdown a 20-row alphabetical prefix (all `ashby`) rather than a
distribution. The sample is still worth printing, but only as a sample, and its ATS mix says
nothing about the whole.

**Read the error count against the shard's egress state, not on its own.** A shard that lost its
spare egress eats rate-limit walls that surface here as errors and deferrals, so a spike can be an
egress artefact rather than a board problem. Cross-check with `fanout_retries.py` before treating
an error spike as a scraper defect.

Run: python scripts/runlog/fanout_errors.py 32272854468
     python scripts/runlog/fanout_errors.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re

from run_logs import DONE, Run, common_args, runs_from, unstamp

ERR_DIGEST = re.compile(r"\d+ board errors: (.+)")
KILLED = re.compile(r"time budget reached after ([\d.]+) min")
DEFERRED = re.compile(r"\[scrape_run\] deferred: (.+)")
QUARANTINE = re.compile(
    r"\[update_ledgers\]\s+quarantined\s+(\S+) \((\d+) strikes, ([^)]+)\)"
)
# The authoritative totals. The per-board `quarantined` lines above are capped at 20 by the
# emitter, so this line is the only honest source for "how many".
FAILURES = re.compile(
    r"\[update_ledgers\] failures: (\d+) board\(s\) reported gone \(404/410\) across (\d+) shard\(s\)"
    r" \| (\d+) ledger rows \((\d+) cleared by a successful scrape\) \| (\d+) at/over (\d+) strikes"
)
FAILED = re.compile(r"\[scrape_run\] (\S+?) failed after (\d+)s: (\w+)")


def scrape_errors(run: Run) -> None:
    rows, digests, kills, deferrals, classes = [], [], [], [], {}
    print(
        f"{'sh':>4}{'jobs':>9}{'boards':>8}{'errors':>8}{'err/1k boards':>15}  (arrival order)",
        flush=True,
    )
    for shard, _job, text in run.stage("scrape"):
        m = DONE.search(text)
        if m:
            jobs_n, boards_n, errs_n = int(m.group(1)), int(m.group(2)), int(m.group(4))
            rows.append((shard, jobs_n, boards_n, errs_n))
            rate = 1000 * errs_n / boards_n if boards_n else 0
            print(
                f"{shard:>4}{jobs_n:>9,}{boards_n:>8}{errs_n:>8}{rate:>15.1f}",
                flush=True,
            )
        for d in ERR_DIGEST.findall(text):
            digests.append((shard, unstamp(d)))
            print(f"     shard {shard}: {unstamp(d)}", flush=True)
        for k in KILLED.findall(text):
            kills.append((shard, float(k)))
            print(f"     shard {shard} KILLED at {float(k):.1f} min", flush=True)
        for d in DEFERRED.findall(text):
            deferrals.append((shard, unstamp(d)))
            print(f"     shard {shard} deferred: {unstamp(d)}", flush=True)
        for f in FAILED.findall(text):
            classes[f[2]] = classes.get(f[2], 0) + 1

    if rows:
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
    if kills:
        print(f"\n  {len(kills)} shard(s) hit the time budget:", flush=True)
    else:
        print("\n  no shard hit its time budget", flush=True)


def quarantines(run: Run) -> None:
    jobs = run.stage_jobs("join")
    if not jobs:
        return
    text = run.log(jobs[0])
    totals = FAILURES.search(text)
    if totals:
        gone, shards, ledger, cleared, quarantined, at = totals.groups()
        print(
            f"\n  failures: {gone} board(s) reported gone across {shards} shard(s) | "
            f"{ledger} ledger rows ({cleared} cleared) | "
            f"**{quarantined}** at/over {at} strikes — a STANDING TOTAL over the whole ledger, "
            f"not this run's inflow",
            flush=True,
        )
    sample = QUARANTINE.findall(text)
    if not sample:
        print("  no quarantine lines logged this run", flush=True)
        return
    # Deliberately NOT counted as a total, and no per-ATS breakdown: the emitter caps this at 20
    # and sorts alphabetically, so its ATS mix is an artefact of the alphabet.
    print(
        f"  sample of the quarantined boards (emitter caps this list at 20, sorted by name — "
        f"{len(sample)} shown, not a total):",
        flush=True,
    )
    for board, strikes, why in sample[:10]:
        print(f"    {board} ({strikes} strikes, {why})", flush=True)
    if len(sample) > 10:
        print(f"    +{len(sample) - 10} more in the sample", flush=True)


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    for run in runs_from(args):
        print(f"\n===== run {run.id} head={run.head} — errors =====", flush=True)
        scrape_errors(run)
        quarantines(run)


if __name__ == "__main__":
    main()
