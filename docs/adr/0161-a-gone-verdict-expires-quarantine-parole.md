# ADR-0161: A gone-verdict expires — quarantined Boards go on parole, not away

**Status:** accepted · **Date:** 2026-09-16 · **Amends:**
[ADR-0058](0058-consecutive-gone-quarantine.md) (the consecutive-gone ledger this drains) ·
**Relates to:** [ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) (the same "expire the evidence"
move, at a cadence set by a cost three orders of magnitude larger),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (one absence is not a verdict)

## Context

ADR-0058 closed a real loop — a Board that 404s five consecutive scrapes leaves the slice — and
left the reverse loop open. It states "a board that dies and later revives is re-admitted
automatically: its first successful scrape clears the streak". That sentence describes code that
cannot run. `scrape_plan` removes a quarantined Board from the slice, so it never scrapes, so it
never enters `produced`, so `board_failures.update`'s `rows.pop(board)` is unreachable for exactly
the population it was written for.

**Measured, five runs of 2026-09-16** (`experiment/pipeline-review-2026-09-16/artifacts/join-stage.md`,
finding 2): `update_ledgers failures` printed `0 cleared by a successful scrape` in **5 of 5**
runs, while the quarantine total went 749 → 749 → 751 → 752 → **755**, monotone, ≈+1.3 per run
over 4h30m. Nothing reported that as loss, because the log stated a level and no delta.

**Measured, this change** (`experiment/quarantine-redemption/`): every one of the **757** Boards
quarantined in the live ledger (`data/state/board_failures.csv`, pulled from HF 2026-09-16) was
re-probed once at the same listing URL its own scraper builds.

| outcome | Boards |
|---|---|
| still 404 | 666 |
| **200** | **23** |
| connection error (56) / read timeout (6) / unbuildable probe URL (6) | 68 |

Of the 23 that answer 200, **16 serve live postings — 5,593 in total**, the largest
(`greenhouse:svetness`) 4,980 and `ashby:airapps` 498. `greenhouse:lookout`/`lookoutinc` and
`greenhouse:goprojobs`/`goprocareers` are slug renames that came back under a second spelling.
Recovery is not an artefact of one age bucket: by days-since-quarantine it runs 2/98 (0–6d),
9/94 (7–13d), 3/144 (14–20d), 8/381 (21–27d) — roughly flat, ≈0.8 Boards/day becoming reachable
again. (The 56 connection errors are mostly the probe's own fault: a Personio slug is a whole
host and the ledger carries bare tenants, so the probe built an unresolvable URL. They are not
counted as recoveries.)

So the coverage lost to a one-way door is not hypothetical, and it accrues indefinitely.

## Decision

**A gone-verdict expires.** `board_failures.paroled(rows, now)` returns the quarantined Boards
whose `last_seen_gone` is `PAROLE_DAYS = 7` or older, and `scrape_plan` subtracts them from the
skip set — they are re-admitted to the slice for one run. Nothing else changes: `update` already
does the right thing with whatever comes back. A fresh 404 takes another strike and restamps
`last_seen_gone`, restarting the clock; any output at all deletes the row.

`update_ledgers failures` now states the flow beside the stock: `755 at/over 5 strikes (+6 new,
-1 released)`. Both halves are printed even at zero — an omitted-when-zero clause is how a
consumer regex silently drops a whole line instead of erroring.

### Why seven days

Not taste — a ratio. ADR-0064's value gate makes the identical move (`_GATE_RECHECK_DAYS = 14`,
"evidence that cannot change makes the gate a one-way door") and rations it at a fortnight
because each re-check there costs **15+ minutes of shard time**. A quarantine re-probe costs one
listing request, and every one of the 757 quarantined Boards has a measured cost row:
**p50 0.10 s, p90 0.94 s, max 16.75 s, 354 s for all 757 together**
(`data/state/board_cost.csv`, 2026-09-16). Inheriting the fortnight would be copying the number
without the reasoning behind it.

The cost that actually binds is **slice slots**, not seconds: the run's slice is capped at 20,000
Boards, and the quarantine is 757 of them (3.8%) if re-admitted every run. At a 7-day interval
and ≈21 runs/day (five runs in the review's 4h30m window), the steady-state re-admitted cohort is
`rows / (7 × 21) ÷ p(selected)`. The exploration tail selects a Board with p = 0.144 (14,000
explore slots over a 97,254-Board tail pool, measured against the live ledgers), so ~36 Boards
sit re-admitted at any time — **0.18% of the slice** — and ~5 are actually scraped per run. A
recovered Board is back in the product within a week, median 3.5 days plus ~5 hours of
selection lag.

Not one day: at 0.8 recoveries/day, probing all 757 Boards ~21 times a week to catch them is
~99% wasted requests, aimed at origins that have already answered 404 five times. Seven days is
one probe per Board per week — 757 requests/week, against 5,593 postings recovered on the first
sweep.

## Options considered

1. **Interval parole, re-using `last_seen_gone` as the clock (chosen).** No new ledger column, no
   new state file, and the timestamp already means exactly "when this verdict was last earned".
   Nine lines of new code.
2. **Re-admit every quarantined Board every run.** The measured cost permits it — 354 s of shard
   time spread over 15 shards is 24 s each against a 3,600 s budget. Rejected because it makes
   ADR-0058 a no-op, spends 3.8% of the slice cap on Boards confirmed gone, and puts 15,900
   404s/day at ATS origins (6,489/day at `boards-api.greenhouse.io` alone), which is how a
   provider-wide block gets earned.
3. **A fixed quota — parole the N least-recently-probed Boards each run.** Bounds the per-run cost
   exactly regardless of ledger size, where an interval grows linearly with it. Rejected as
   speculative: the bound only starts to matter past ~21,000 rows, and a quota needs its own
   ordering and tie-break for a problem we do not have.
4. **Exponential backoff on strikes** (7d, 14d, 28d …). Rejected on the measurement: recovery is
   flat across every age bucket the ledger covers, so backoff would delay real recoveries to buy
   savings on a cost already measured at 0.10 s.

## Consequences

- The first run after this ships paroles ~640 Boards at once — every row older than 7 days, p50
  age 24 days. They drain through the exploration tail over ~7 runs at ~0.14 selection
  probability, ~92 scrapes in the first run, and the 23 recoverable Boards return within a day
  or two.
- **A quarantined Board whose re-probe fails some other way (timeout, TLS, 429) stays paroled**
  until a verdict arrives, because `update` leaves its row untouched and the parole clock never
  restarts. That is deliberate and it is the recall-safe direction: quarantine's premise is
  *confirmed* gone, and a Board we can no longer confirm is not one we have grounds to keep
  excluding. It costs one failed scrape per run for as long as it lasts, and self-heals on the
  first 404 or first success. (This is also what would give the 16 TLS-expired SuccessFactors
  hosts and the DNS-dead `careers.octapharma.com` a route back — except those never earn a
  gone-verdict in the first place, so they are not in this ledger at all. ADR-0058's "known gaps"
  still owns them.)
- The quarantine total can now go **down**, so anything reading it as monotone is wrong. The log
  states the movement in both directions and `scripts/runlog/fanout_errors.py` reports it.
- Still untouched, exactly as ADR-0058 left them: `data/validate/liveness/` stays probe-owned,
  and `live_keep_set` — which feeds `index prune` — never sees quarantine either way, so no
  scraping decision evicts a served row.
