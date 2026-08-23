#!/usr/bin/env python3
"""What this run taught the ledgers that feed the *next* run's plan.

`update_ledgers` runs four subcommands inside the `join` job, in a fixed order (priority, cost,
failures, gap — CLAUDE.md's repo-conventions section names this order because `fanout_plan.py`'s
`scrape_plan` reads all four the following run). `failures` (quarantine inflow/stock) already has
`fanout_errors.py`; this file covers the other three, which nothing currently reads.

**`priority`** — `N boards in snapshot | M ledger rows (new, pruned, carried)`, then the top-10
boards by score. `carried` boards are ones the ledger remembers but this run's snapshot didn't
touch (an unscraped or gated Board) — a `carried` count that keeps climbing run over run is the
ledger accumulating boards no plan is currently selecting, worth knowing before it's read as churn.

**`cost`** — one line per scrape-fragment shard (`{fragment}: N timed boards`) folded into a total,
then the per-ATS median in seconds, ranked. The medians are what `scrape_plan`'s cold-start pack
falls back to for any board it hasn't measured yet (ADR-0026) — an ATS whose median jumps changes
every unmeasured board of that ATS's predicted cost on the next plan, not just the boards actually
re-timed this run.

**`gap`** — `R stored rows | S settled | U unsettled across B boards (D on a disabled ATS, X gone
from a Board this run scraped in full — both unreachable)`, then the top-10 boards by backlog. This
is the ADR-0050 description-store backlog: `unreachable` ids can never settle (wrong ATS, or the
Board's own authoritative scrape already re-emitted a shorter list that dropped them, #185) and are
excluded from the count on purpose — a `gap` total that includes them would overstate the ledger's
useful backlog and never shrink no matter how many descriptions actually get filled.

All three top-N samples are capped by the emitter itself (priority 10, cost-median 8, gap 10) — see
`fanout_errors.py`'s caution about the same shape on `failures`' quarantine sample: a cap makes the
printed list a ranked sample, never a total, and never counted as one here.

Run: python scripts/runlog/fanout_ledgers.py 32272854468
     python scripts/runlog/fanout_ledgers.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re

from run_logs import Run, common_args, runs_from

PRIORITY_HEADER = re.compile(
    r"\[update_ledgers\] priority: (\d+) boards in snapshot \| (\d+) ledger rows "
    r"\((\d+) new, (\d+) pruned, (\d+) carried\)"
)
PRIORITY_TOP = re.compile(r"\[update_ledgers\]\s+([\d.]+)\s+(\S+) \((\d+) tech jobs\)")
COST_HEADER = re.compile(
    r"\[update_ledgers\] cost: (\d+) boards timed across (\d+) shard\(s\) \| "
    r"(\d+) ledger rows \((\d+) new\) \| Σ (\d+) board-minutes"
)
COST_MEDIAN = re.compile(r"\[update_ledgers\]\s+([\d.]+)s median\s+(\S+)")
GAP_HEADER = re.compile(
    r"\[update_ledgers\] gap: ([\d,]+) stored rows \| ([\d,]+) settled \| "
    r"([\d,]+) unsettled across ([\d,]+) boards \(([\d,]+) on a disabled ATS, "
    r"([\d,]+) gone from a Board this run scraped in full"
)
GAP_TOP = re.compile(r"\[update_ledgers\]\s+([\d,]+) unsettled\s+(\S+)")
GAP_NO_STORE = re.compile(r"\[update_ledgers\] gap: no \S+ yet")
GAP_EMPTY_STORE = re.compile(r"\[update_ledgers\] gap: \S+ holds nothing")


def report(run: Run) -> None:
    jobs = run.stage_jobs("join")
    if not jobs:
        print("  no join job in this run", flush=True)
        return
    text = run.log(jobs[0])

    print("-- priority --", flush=True)
    ph = PRIORITY_HEADER.search(text)
    if ph:
        boards, rows, new, pruned, carried = ph.groups()
        print(
            f"  {boards} boards in snapshot | {rows} ledger rows "
            f"({new} new, {pruned} pruned, {carried} carried)",
            flush=True,
        )
    for score, board, tech in PRIORITY_TOP.findall(text)[:10]:
        print(f"    {float(score):9.1f}  {board} ({tech} tech jobs)", flush=True)

    print("-- cost --", flush=True)
    ch = COST_HEADER.search(text)
    if ch:
        timed, shards, rows, new, total = ch.groups()
        print(
            f"  {timed} boards timed across {shards} shard(s) | {rows} ledger rows "
            f"({new} new) | Σ {total} board-minutes",
            flush=True,
        )
    medians = COST_MEDIAN.findall(text)
    for secs, ats in medians[:8]:
        print(f"    {float(secs):8.1f}s median  {ats}", flush=True)
    if not ch:
        print("  no cost summary line found", flush=True)

    print("-- gap (description-store backlog, ADR-0050) --", flush=True)
    gh = GAP_HEADER.search(text)
    if gh:
        stored, settled, unsettled, boards, disabled, expired = gh.groups()
        print(
            f"  {stored} stored | {settled} settled | {unsettled} unsettled across "
            f"{boards} boards ({disabled} on a disabled ATS, {expired} expired-unreachable "
            "— both excluded from the unsettled count)",
            flush=True,
        )
        for n, board in GAP_TOP.findall(text)[:10]:
            print(f"    {n:>8} unsettled  {board}", flush=True)
    elif GAP_NO_STORE.search(text):
        print("  skipped: no embeddings store yet — nothing embedded", flush=True)
    elif GAP_EMPTY_STORE.search(text):
        print(
            "  skipped: description store holds nothing this run — the download failed, "
            "not that the store is genuinely empty; the ledger was left as-is",
            flush=True,
        )
    else:
        print("  no gap summary line found", flush=True)


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    runs = runs_from(args)
    for run in runs:
        print(f"\n===== run {run.id} head={run.head} — ledgers =====", flush=True)
        report(run)
    if len(runs) > 1:
        print(
            "\n  NB: `carried` (priority) rising across runs means boards the ledger tracks but "
            "no recent plan selected — check whether that's the value gate (fanout_plan.py) or "
            "just exploration variance before calling it a leak.",
            flush=True,
        )


if __name__ == "__main__":
    main()
