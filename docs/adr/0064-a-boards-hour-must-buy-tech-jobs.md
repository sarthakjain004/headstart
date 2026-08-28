# ADR-0064: A Board's hour must buy tech jobs

**Status:** accepted · **Date:** 2026-08-18 · **Relates to:**
[ADR-0022](0022-tech-priority-board-ordering.md),
[ADR-0026](0026-parallelize-nightly-scrape.md),
[ADR-0027](0027-measured-scrape-cost-ledger.md),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md),
[ADR-0054](0054-learned-fan-out-speedup.md)


> **Amended 2026-08-28 by [ADR-0096](0096-one-key-for-both-board-ledgers.md).** `_gated_boards` took a `(cost_key, priority_key)` pair because the two ledgers were keyed
> differently; they now share `board_identity` and it takes one key. The gate's behaviour is
> unchanged — measured at 15 Boards gated either way.

## Context

Scrape shards began hitting their 60-minute CI budget. Three of the last four runs ended with a
shard killed at `3599 s` rather than finished — a banked partial, which is a supported outcome, so
every run stayed green while coverage silently went over the side.

The obvious reading is that #161 added volume and the fan-out needs re-packing. **Measured on run
`32133497258`, that reading is wrong.** The planner's pack is nearly perfect in its own units —
147.7–151.3 serial minutes per shard, a 1.02× spread — and every shard drew the same ATS mix
(`workday 234, greenhouse 203, smartrecruiters 196, zoho 128, +14 more`, identical on the fastest
shard and the killed one). Yet wall clocks ran 10.0 to 60.0 minutes.

The decomposition says why. Every shard finished its first 1,200 Boards in **6.2–9.4 minutes** —
throughput is uniform across the fleet. All of the variance is in the last ~140:

| shard | wall | last ~140 Boards | its slowest single Board |
|---|---|---|---|
| 13 (killed) | 60.0 min | 52.9 min (88%) | deferred, never finished |
| 14 | 53.5 min | 46.8 min (88%) | `viacomcbs.careers` 51.9 min |
| 0 | 45.2 min | 38.7 min (86%) | `walmart` 45.1 min |
| 1 (fastest) | 10.0 min | 3.1 min (31%) | 4.8 min |

In eight of fifteen shards the wall clock sits within a minute of that one Board (45.2/45.1,
28.0/28.0, 14.5/14.5, 11.3/11.3, 11.1/11.0). **A shard is as slow as the largest Board it drew.**
LPT packing balances the sum, but a Board is indivisible: the sum finishes in ~8 minutes at
achieved concurrency, and the makespan floor is the biggest item. ADR-0054 already encodes this —
`predict_minutes` floors a shard at its own slowest Board — so the model knew; nothing acted on it.

Two things were verified negative before concluding, because both are more attractive explanations:

* **Not retry-bound.** The giants run at ordinary speed — walmart 5.7 jobs/s, target 5.9, lidl 9.1,
  hcltech 8.7, against 6.3–7.7 for ordinary Boards. They take 20–50 minutes because they hold
  10k–24k postings. Retry counts also fail to separate the fleet: shard 5 absorbed 29,779 retries
  and finished in 11.1 min; shard 3 took 46.9 min on fewer.
* **Not a page-size limit we could raise.** Probed live: Workday rejects `limit` above 20
  (`limit=50` and `limit=100` return no postings), as `scrapers/workday.py` already documented.

The deferred Board on shard 13 was identified by diffing the assignment artifact against the banked
fragment's cost rows: `workday:dollartree/dollartreeus`. It ran the last ~52 minutes and was killed
unfinished. Its live posting count is **24,017** — about 67 minutes to page at 20 per request,
**more than a whole shard budget with the shard to itself.** It can never finish. Its priority
score is **9.7, from 10 tech jobs**.

That exposes a second defect, and the more serious one. A Board that never finishes writes no cost
row, so the ledger kept pricing dollartree at **411.9 s** — a 10× underestimate. It was therefore
packed as a cheap Board, drew a shard, burned ~52 minutes, was killed, deferred, and would be
re-drawn next run, indefinitely. **The one Board whose cost the model most needed to learn was the
only Board it could never measure.** ADR-0054's rule that budget-killed shards are dropped from the
speedup EWMA — correct on its own terms — means nothing anywhere records what happened.

The ledger says this is a small, sharp problem. Of 68,715 costed Boards, **12** cost over 15
minutes, and their tech yield per minute of shard time splits with a visible gap:

| keep | tech/min | | drop | tech/min |
|---|---|---|---|---|
| hcltech (×2) | 124, 146 | | compass | 1.35 |
| careers.ey.com | 24 | | viacomcbs | 0.90 |
| walmart | 20 | | REWE | 0.54 |
| target | 7.1 | | lidl | 0.29 |
| paradox | 5.7 | | advanceauto | 0.03 |
| | | | cbscorporation | 0.01 |

## Decision

**A Board that costs more than 15 minutes must return at least 2 tech jobs per minute of shard
time, or the planner does not select it** — and **a Board killed mid-fetch is costed for the
seconds it burned.**

The two are one change, because either alone fails. The gate cannot see dollartree while the
survivorship hole prices it at 411.9 s; the floor cost alone learns the truth and keeps paying it.
Together: one killed run records 3,120 s, and the next plan reads 0.19 tech-per-min and stops
drawing it.

* `harvest.scrape_all` tracks Boards mid-fetch and, on the way down, writes a cost row for
  whatever is still in flight, **marked `unfinished`**. The seconds a kill proves are a *lower
  bound*, so the ledger takes `max(stored, burned)` rather than its usual EWMA: a bound may raise
  a Board's price, never lower it. Blending instead records less than the kill proved — for
  dollartree, 1,766 s against the 3,120 s demonstrably burned — and a Board whose stored price is
  low enough can blend back *under* the gate floor and be re-packed every run, which is the loop
  this exists to break. The row keeps its prior `jobs` count, since an unfinished run banked no
  complete listing. It is written only for Boards actually fetching — never for queued ones, whose
  cost is unknown, and never for one whose scraper failed to construct, which never fetched.
* `scrape_run` names the deferred Boards in its warning and its shard report, and `scrape_join`
  names them again across the whole fan-out — where a Board deferred run after run reads as a
  pattern rather than as one shard's bad luck. Finding dollartree took downloading two artifacts
  and diffing them; the reports knew the answer the whole time and nothing read it.
* `scrape_plan._gated_boards` gates on value density, before slice selection — a Board dropped
  after selection has already taken a slot from something scrapable.

Three properties the gate must have, each of which cost a failure mode elsewhere:

1. **It judges a Board only on its own measurement.** `costs_for` estimates an unmeasured Board
   from its ATS median; gating on that would drop Boards for their ATS's reputation before they
   ever had a record, taking out every unmeasured SuccessFactors board at once.
2. **It reads each ledger under that ledger's own key** — cost by `{ats}:{slug}`, priority by
   `board_identity` (ADR-0049). Conflating them reads every Workday giant as zero-yield and drops
   walmart with the rest.
3. **A gated Board's measurement expires after 14 days.** A Board that is not scraped cannot update
   its evidence, so a gate on frozen evidence is permanent. Expiry re-admits it for one run, where
   it is measured and judged on what it is now. Being wrong then costs one shard-hour a fortnight
   rather than a Board forever.

## Consequences

Six Boards are gated against today's ledger, freeing **3.5 shard-hours per run**; dollartree makes
seven after a single killed run measures it (411.9 s → 3,120 s → 0.19 tech/min). Six giants stay — walmart, EY, both hcltech boards, paradox, target —
and walmart's 44.5 minutes becomes the makespan floor, so the expected slowest shard lands around
45–50 min against a 60-minute budget.

**That is relief, not headroom.** The floor is still a single Board, still indivisible, and still
grows as walmart posts more. This change buys time to do the structural work; it is not that work.

What it deliberately does not do: it does not touch cheap Boards however little they yield (the
exploration tail is how an unknown Board earns a score at all), it does not cap volume, and it does
not make the tech gate anything other than the post-hoc filter — a Board is dropped for costing too
much per tech job, never for what its postings look like.

The honest alternatives, and why not now:

* **A per-Board time budget in `harvest`**, banking a truncated listing the way the eightfold
  405-path already does. This is the right second wave, and the one that generalises: it protects
  against a *high-yield* giant crossing the budget, which the value gate deliberately will not do.
* **Workday facet reduction** (ADR-0017's source-query layer), and on the measurements taken
  alongside this ADR it is the strongest option by a wide margin. `scrapers/workday.py` already
  subdivides by `jobFamilyGroup` to beat the 2,000-result cap, and every response carries each
  facet value's GUID and true full-corpus count — verified live: `appliedFacets` ORs within one
  key and ANDs across keys, filtered totals match the advertised counts 5/5, and facet GUIDs are
  stable WIDs (3 of 4 survived two months). Dollar Tree's "Information Technology" family holds
  **19 of 24,027 postings (0.08%)**, so a tech-only scrape is one filtered request plus 19 details
  — seconds against ~67 minutes. It is per-ATS work and carries ADR-0017's recall caveat in full:
  a tech job misfiled under "Store Operations" is never scraped and cannot be recovered post-hoc,
  and the facet vocabulary is tenant-configured rather than a shared taxonomy. The measurement
  that decides it is what fraction of real tech postings sit outside the tech-named families;
  that investigation is running separately and this ADR does not presume its answer.
* **Fewer, monster-aware shards.** ~50% of paid fleet minutes are idle wait on the slowest shard.
  A cost problem, not a coverage one, so it ranks below both of the above.
