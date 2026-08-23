#!/usr/bin/env python3
"""What the planners already knew before a single shard ran.

`scrape_plan` (its own job) and `embed_plan` (folded into the `join` job, after
`update_descriptions`/`update_ledgers`) both pack Boards/Docs into shards from a measured cost
ledger, and both **self-report their own straggler risk before the run happens** — a signal
`fanout_timing.py` only reconstructs after the fact from `slow board` lines and floor ratios. Read
this file first when diagnosing a run: if the plan already warned "one board costs 52 min, above
the 9 min even share", the actual run confirming a straggler is not a new finding.

**Five things a plan run tells you that nothing else does:**

1. **Slice composition** (`scrape_plan` only) — `slice: N boards (P priority + E exploration); G
   hold unsettled descriptions, out of U gap boards (J jobs) still to drain`. `P` boards are ranked
   by measured tech yield; `E` is random exploration filling out the target. A scrape that looks
   thin on a specific ATS may just be this run's exploration draw, not a regression — check this
   line before calling a per-ATS drop real (the same caution `fanout_corpus.py` already gives for
   comparing two runs).
2. **Cost-ledger coverage** — `cost: measured seconds for H/N boards (L in ledger); rest estimated
   from their ATS median`, or `cost: no measurements yet — cold-start heuristic` on a fresh ledger.
   Low coverage means the pack is sized on medians, not real per-Board cost, and a straggler can
   still hide in the estimated tail.
3. **The value gate** (ADR-0064, `scrape_plan` only) — `value gate: skipped N Board(s) costing over
   15 min for under 2 tech jobs/min — board (rate/min), ...`. This is the gate *removing* Boards
   before packing; the sample is `observability.named_sample`, capped at 10 and sorted worst-first,
   so a count above 10 is a lower bound on what's shown. The gate is reactive — it can only act on a
   Board that already has a cost row — so a giant on its *first* uncapped run is invisible here and
   shows up only as an actual straggler once (ADR-0077's `dollartree` case, and the `AdeebaEServicesPvtLtd`
   case from the 2026-08-23 SmartRecruiters review: a board can look ~0-1% tech on a probe sample and
   still clear the gate on absolute yield once measured for real — read the gate's own number, not
   the probe).
4. **Predicted spread and the makespan floor** — `predicted spread: min A / mean B / max C min (Rx
   mean); single-board floor F min`, then, only when it applies, `one board costs F min, above the
   E min even share — the makespan floor is this board, not the packing`. This is the planner
   naming its own expected straggler in the SAME units `fanout_timing.py`'s floor_table reports
   after the fact — compare them: a plan-predicted floor that didn't show up as the actual floor
   means something changed between plan and run (a Board that failed fast, an egress problem that
   slowed everything evenly instead of one item).
5. **The budget-exceeded warning** — `predicted makespan ~M min exceeds the 60 min shard budget —
   shards matching their prediction will bank partials`. This is an advance warning of budget kills,
   printed before any shard has run. If `fanout_errors.py` then shows 0 kills, the run beat its own
   prediction (worth knowing, not just silently good).

`embed_plan` mirrors 2-4 in its own units (Docs/tokens, not Boards/postings) but has no per-Doc
floor mechanism — cost is a token-bucket lookup, not a measured-per-item cost, so its pack is
normally even and it never names a single-item floor the way `scrape_plan` does. Treat an uneven
`embed` shard spread as suspicious rather than expected; `fanout_embed.py` covers the executed
side.

Run: python scripts/runlog/fanout_plan.py 32272854468
     python scripts/runlog/fanout_plan.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re

from run_logs import Run, common_args, runs_from

QUARANTINE_SKIP = re.compile(
    r"\[scrape_plan\] quarantine: skipped (\d+) of (\d+) confirmed-gone board\(s\)"
)
VALUE_GATE = re.compile(
    r"value gate: skipped (\d+) Board\(s\) costing over (\d+) min for under (\d+) "
    r"tech jobs/min — (.+)"
)
GATE_BOARD = re.compile(r"(\S+) \(([\d.]+)/min\)")
SLICE = re.compile(
    r"\[scrape_plan\] slice: (\d+) boards \((\d+) priority \+ (\d+) exploration\); "
    r"(\d+) hold unsettled descriptions, out of ([\d,]+) gap boards \(([\d,]+) jobs\) "
    r"still to drain"
)
COST_COVERAGE = re.compile(
    r"\[scrape_plan\] cost: measured seconds for (\d+)/(\d+) boards \((\d+) in ledger\)"
)
COST_COLDSTART = re.compile(r"\[scrape_plan\] cost: no measurements yet")
MAKESPAN = re.compile(
    r"\[scrape_plan\] (\d+) boards across (\d+) shards; predicted makespan ~([\d.]+) min "
    r"\(total work Σ ([\d.]+) min\)"
)
SPREAD = re.compile(
    r"predicted spread: min ([\d.]+) / mean ([\d.]+) / max ([\d.]+) min "
    r"\(([\d.]+)x mean\); single-board floor ([\d.]+) min"
)
FLOOR_WARN = re.compile(
    r"one board costs ([\d.]+) min, above the ([\d.]+) min even share"
)
BUDGET_WARN = re.compile(
    r"predicted makespan ~([\d.]+) min exceeds the (\d+) min shard budget"
)

EMBED_PRIOR = re.compile(
    r"\[embed_plan\] prior store: (\d+) embedded ids \((\d+) without a description\)"
)
EMBED_NEW = re.compile(
    r"\[embed_plan\] new Docs: (\d+) \(scanned (\d+), already (\d+), non-English (\d+), "
    r"upgraded (\d+)\)"
)
EMBED_ADMISSION = re.compile(
    r"\[embed_plan\] admission: capped (\d+) -> (\d+) top-priority Docs"
)
EMBED_MAKESPAN = re.compile(
    r"\[embed_plan\] (\d+) Docs across (\d+) shards; predicted makespan ~([\d.]+) min "
    r"\(total work Σ ([\d.]+) min\)"
)


def scrape_plan_report(run: Run) -> None:
    jobs = run.stage_jobs("scrape-plan")
    if not jobs:
        print("  no scrape-plan job in this run", flush=True)
        return
    text = run.log(jobs[0])
    print("-- scrape_plan (advance diagnostics, before any shard ran) --", flush=True)

    q = QUARANTINE_SKIP.search(text)
    if q:
        print(
            f"  quarantine: skipped {q.group(1)} of {q.group(2)} confirmed-gone board(s)",
            flush=True,
        )

    s = SLICE.search(text)
    if s:
        print(
            f"  slice: {s.group(1)} boards ({s.group(2)} priority + {s.group(3)} exploration); "
            f"{s.group(4)} hold unsettled descriptions, {s.group(5)} gap boards "
            f"({s.group(6)} jobs) still to drain",
            flush=True,
        )

    cov = COST_COVERAGE.search(text)
    if cov:
        have, total, ledger = cov.groups()
        pct = 100 * int(have) / int(total) if int(total) else 0.0
        print(
            f"  cost coverage: {have}/{total} boards measured ({pct:.0f}%), "
            f"{ledger} rows in the ledger — rest sized from their ATS median",
            flush=True,
        )
    elif COST_COLDSTART.search(text):
        print(
            "  cost coverage: cold start — no measurements yet (ADR-0026 heuristic)",
            flush=True,
        )

    g = VALUE_GATE.search(text)
    if g:
        n, floor_min, min_rate, sample = g.groups()
        boards = GATE_BOARD.findall(sample)
        print(
            f"  value gate (ADR-0064): skipped {n} board(s) over {floor_min} min costing under "
            f"{min_rate} tech jobs/min",
            flush=True,
        )
        for board, rate in boards[:10]:
            print(f"    {board} ({rate}/min)", flush=True)
        if int(n) > len(boards):
            print(
                f"    ({len(boards)} of {n} shown — observability.named_sample caps the "
                "printed list at 10)",
                flush=True,
            )
    else:
        print("  value gate: no boards skipped this run", flush=True)

    m = MAKESPAN.search(text)
    sp = SPREAD.search(text)
    if m:
        n, shards, makespan, total_work = m.groups()
        print(
            f"  predicted: {n} boards / {shards} shards, makespan ~{float(makespan):.1f} min "
            f"(Σ work {float(total_work):.1f} min)",
            flush=True,
        )
    if sp:
        mn, mean, mx, ratio, floor = (float(x) for x in sp.groups())
        print(
            f"  predicted spread: min {mn:.1f} / mean {mean:.1f} / max {mx:.1f} min "
            f"({ratio:.2f}x mean); single-board floor {floor:.1f} min",
            flush=True,
        )
    fw = FLOOR_WARN.search(text)
    if fw:
        floor, even = fw.groups()
        print(
            f"  NB: the plan itself predicts a floor-bound shard — one board at {floor} min "
            f"against a {even} min even share. Check fanout_timing.py's actual floor_table for "
            "whether it landed where predicted.",
            flush=True,
        )
    bw = BUDGET_WARN.search(text)
    if bw:
        predicted, budget = bw.groups()
        print(
            f"  NB: predicted makespan {predicted} min exceeds the {budget} min shard budget — "
            "expect banked partials; cross-check fanout_errors.py for whether any shard was "
            "actually killed",
            flush=True,
        )


def embed_plan_report(run: Run) -> None:
    # embed_plan runs inside the `join` job, after scrape_join/filter_tech/update_descriptions/
    # update_ledgers — not a separate job, so this reads the same log fanout_corpus.py does.
    jobs = run.stage_jobs("join")
    if not jobs:
        print("  no join job in this run (embed_plan runs inside it)", flush=True)
        return
    text = run.log(jobs[0])
    print("\n-- embed_plan (advance diagnostics for the embed matrix) --", flush=True)

    p = EMBED_PRIOR.search(text)
    if p:
        print(
            f"  prior store: {p.group(1)} embedded ids ({p.group(2)} without a description)",
            flush=True,
        )
    n = EMBED_NEW.search(text)
    if n:
        new, scanned, already, non_en, upgraded = n.groups()
        print(
            f"  new Docs: {new} (scanned {scanned}, already-embedded {already}, "
            f"non-English {non_en}, upgraded {upgraded})",
            flush=True,
        )
    a = EMBED_ADMISSION.search(text)
    if a:
        before, after = a.groups()
        print(
            f"  admission control: capped {before} -> {after} top-priority Docs",
            flush=True,
        )
    m = EMBED_MAKESPAN.search(text)
    if m:
        docs, shards, makespan, total_work = m.groups()
        print(
            f"  predicted: {docs} Docs / {shards} shards, makespan ~{float(makespan):.1f} min "
            f"(Σ work {float(total_work):.1f} min)",
            flush=True,
        )
        print(
            "  NB: embed has no per-Doc floor mechanism (bucket-sized cost, not measured "
            "per-item) — an uneven actual spread in fanout_embed.py is suspicious, not expected.",
            flush=True,
        )
    elif "nothing new to embed" in text:
        print("  nothing new to embed — empty plan", flush=True)


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    for run in runs_from(args):
        print(f"\n===== run {run.id} head={run.head} — plan =====", flush=True)
        scrape_plan_report(run)
        embed_plan_report(run)


if __name__ == "__main__":
    main()
