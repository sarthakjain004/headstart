# ADR-0162: A gone-verdict expires — quarantined Boards go on parole, not away

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

Of the 23 that answer 200, 16 serve postings — 5,593 raw. **Count the tech subset, not that
number.** Only tech roles are embedded, indexed or shown (ADR-0017), so the figure that reaches
users is what survives `tech_filter.is_tech(title, department)` — the same two arguments
`filter_jobs` passes:

| | Boards | postings |
|---|---|---|
| answered 200 | 23 | — |
| ≥1 posting | 16 | 5,593 |
| **≥1 tech posting** | **12** | **264 (4.7%)** |

The two diverge almost entirely on one Board. `greenhouse:svetness` is **4,980 of the 5,593
(89%)** and is a personal-training franchise — *Circuit Training Instructor*, *In-home Personal
Trainer* — **0 of 4,980 tech**. Reading the raw count as recovered coverage overstates this
change's benefit by 21x. The Board actually worth recovering is `ashby:airapps`: 216 tech of 498
(43.4%) — Data Engineer, AI/ML Engineer — which alone is 82% of the tech recovered. (Title-only
scores airapps at 206; `filter_jobs` passes department too, which recovers 10 more `Data Analyst`
rows under a `Platform` department. 216 is the production-accurate figure.) Two more caveats
against over-reading even 264: `greenhouse:lookout`/`lookoutinc` are one employer under two
slugs, so 2 of the 264 are the same postings twice, and the recall-biased gate counts two
"Forward Deployed Recruiter - SWE" rows on `ashby:sxaler` — tolerated creep, per ADR-0017, not
software jobs.

Recovery is not an artefact of one age bucket: by days-since-quarantine it runs 2/98 (0–6d),
9/94 (7–13d), 3/144 (14–20d), 8/381 (21–27d) — roughly flat, ≈0.8 Boards/day becoming reachable
again. (The 56 connection errors are mostly the probe's own fault: a Personio slug is a whole
host and the ledger carries bare tenants, so the probe built an unresolvable URL. They are not
counted as recoveries.)

So the coverage lost to a one-way door is not hypothetical, and it accrues indefinitely — but it
is 264 tech postings and 12 Boards per month of accrual, not 5,593.

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
because each re-check there costs **15+ minutes of shard time**. Here every one of the 757
quarantined Boards has a measured cost row: **p50 0.10 s, p90 0.94 s, max 16.75 s, 354 s for all
757 together** (`data/state/board_cost.csv`, 2026-09-16). Inheriting the fortnight would be
copying the number without the reasoning behind it.

*Read that number for what it is.* Those seconds are each Board's last real scrape **while it was
404ing** — the cost of a dead round-trip, which is what ~97% of any parole cohort will do again.
The ~3% that recover get a full scrape at the price of a live Board, and that is the outcome this
change exists to buy, not a cost to avoid. The aggregate holds either way: 23 recoveries per sweep
against 734 dead round-trips. Note the asymmetry the tech gate creates — `greenhouse:svetness`
costs the most to re-scrape of anything in the cohort (4,980 postings) and returns 0 tech rows, so
the benefit is concentrated in Boards that are cheap, while the cost is concentrated in one that
is not. At 4,980 postings on a list-only ATS this is still seconds, so it does not move the
cadence; it would if a future cohort held a detail-fetching Board of that size.

The cost that actually binds is **slice slots**, not seconds: the run's slice is capped at 20,000
Boards, and the quarantine is 757 of them (**3.8%**) if re-admitted every run.

Working, so the arithmetic can be checked rather than taken:

- **Runs/day = 24.2.** The review's five `scrape_plan` lines run 05:16:41 → 09:15:08 — 238.4 min
  over *four* intervals, 59.6 min/run. (Dividing 5 runs by the 4h30m window instead gives 26.7
  and counts a gap that is not there.)
- **p(selected) = 0.144.** A quarantined Board scores 0 (`board_priority.update` carries an
  unscraped Board's row unchanged, so its score neither decays nor grows), so re-admission puts it
  in the random exploration tail: 14,000 explore slots over a 97,254-Board tail pool, computed
  against the live liveness and priority ledgers on 2026-09-16.
- **Steady state.** `757 / (7 × 24.2)` = 4.5 Boards become eligible per run; they queue until
  selected, so the standing re-admitted pool is `4.5 / 0.144` ≈ **31 Boards — 0.16% of the
  slice** — of which ~4.5 are actually scraped per run. A recovered Board is back within a week,
  median 3.5 days plus ~7 runs (~7 h) of selection lag.

Not one day: at ≈0.8 recoveries/day, daily parole spends **5,299 requests/week instead of 757**
to catch the same ≈5.6 recoveries, at origins that have already answered 404 five times. Seven
days is one probe per Board per week, against 264 tech postings on 12 Boards recovered by the
first sweep.

## Options considered

1. **Interval parole, re-using `last_seen_gone` as the clock (chosen).** No new ledger column, no
   new state file, and the timestamp already means exactly "when this verdict was last earned".
   Nine lines of new code.
2. **Re-admit every quarantined Board every run.** The measured cost permits it — 354 s of shard
   time spread over 15 shards is 24 s each against a 3,600 s budget. Rejected because it makes
   ADR-0058 a no-op, spends 3.8% of the slice cap on Boards confirmed gone, and puts ~18,300
   404s/day at ATS origins (~7,500/day at `boards-api.greenhouse.io` alone), which is how a
   provider-wide block gets earned.
3. **A fixed quota — parole the N least-recently-probed Boards each run.** Bounds the per-run cost
   exactly regardless of ledger size, where an interval grows linearly with it. Rejected as
   speculative: the bound only starts to matter past ~21,000 rows, and a quota needs its own
   ordering and tie-break for a problem we do not have.
4. **Exponential backoff on strikes** (7d, 14d, 28d …). Rejected on the measurement: recovery is
   flat across every age bucket the ledger covers, so backoff would delay real recoveries to buy
   savings on a cost already measured at 0.10 s.
5. **A separate cheap probe outside the slice** — what the review's own wording suggested ("a
   single cheap probe, not a full scrape"). Rejected because for this population the full scrape
   *is* the cheap probe: a quarantined Board dies on its listing request, which is the same one
   request a bespoke prober would send, and the measurement above is exactly that request's cost.
   A second path would need its own per-ATS URL construction (this ADR's own probe script got
   Personio wrong for precisely that reason), would not know how to read a 200, and would then
   have to hand the Board back to the scraper anyway — so a recovery would land a run later than
   it does now. Machinery for a saving already measured at a tenth of a second.

## Consequences

- The first run after this ships paroles **652** Boards at once — every row older than 7 days,
  p50 age 24 days. Re-admitted is not scraped: they drain through the exploration tail at p =
  0.144, ~94 scrapes in the first run and the rest over the following few, so the 23 recoverable
  Boards return within a day or two, and with them 264 tech postings on 12 of them.
- **Report any future measurement of this in tech postings, not raw ones.** The two differ by 21x
  here on one Board. Nothing in the quarantine path is tech-aware — `board_failures` counts
  Boards, and a Board is quarantined or not regardless of what it serves — so the raw count is the
  one that falls out of the ledger, and it is the misleading one.
- The cadence is stated in **days**, not runs, where the review suggested "once every N runs".
  Days is what the ledger already records (`last_seen_gone`), and it keeps the re-probe rate
  stable when the chain's cadence moves — at the cost of making the cohort size depend on runs/day,
  which is the one input above that is not directly measured.
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
