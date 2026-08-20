# ADR-0047: Retry the wall, spread the load — and why pacing cannot fix it

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0026 · **Amended by:** [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md),
[ADR-0078](0078-width-narrows-once-the-origin-has-walled.md)

## Context

ADR-0046 stopped the served index flapping, but it treated the blast radius. The cause is that the
Eightfold scrape is rate-limited and swallows it in two places: `_api_search` breaks pagination on
any non-200 and returns a partial position list, and a failed `position_details` yields `None` so
the description is dropped. Across five production runs Eightfold lost **161,649 of 213,199**
descriptions (75.8%), with 20–32 Boards per run losing *every* description.

That is worse than the flap it caused. `description` is not in the served schema — it exists only
to build the embedding Doc and to extract experience — so a job scraped without one is embedded
from its **title alone**, and `embed_plan` skips ids already in the store, so the degraded vector
is never revisited. Eightfold carries **22,125** tech jobs in the priority ledger, 6.2% of the
356,161 tech jobs across all ATSes, so at the measured loss rate roughly **16,771** of them are
already in the index as title-only vectors. Users experience this as "search is bad for NVIDIA
and Qualcomm", not as an error.

Three things were established by experiment rather than assumed, each of which killed a plausible
theory:

1. **Not the egress IP.** The same code at the same fan-out width, run from a GitHub Actions
   runner, returned 0/120 missing with zero retries. A datacentre origin is not what trips it.
2. **Not fan-out width alone.** 120 details at widths 6, 25 and 100 all came back clean.
3. **Sustained volume against one origin.** 1,500 details at width 100 reproduced production
   almost exactly — **1179/1500 missing (78.6%)**, settling `{200: 321, 429: 204, 405: 975}`.
   After roughly 320 successes the edge switches from 429 to **405**, and 405 was not in
   `http._TRANSIENT`, so those 975 requests were never retried at all: they settled instantly as
   non-200 and became `None`. A follow-up arm showed the block then applied to *every*
   `*.eightfold.ai` tenant, not just the one being fetched, which is why five Boards flap together.

A fourth measurement decided the sharding question. Parallel Actions shards get **distinct egress
IPs** (verified: six shards, six addresses), so each shard carries its own origin budget — but
cost-only LPT packing concentrates an ATS rather than spreading it, so most of that budget goes
unspent while a few shards absorb the whole limit.

## Decision

Three changes, plus one deliberate non-change:

1. **405 joins `_TRANSIENT`** and is counted as its own `405-wall` retry reason, so the dominant
   failure mode is both retried and visible in the per-shard retry line.
2. **`Retry-After` is honoured** over the local backoff curve when a host sends one as a delta,
   capped at 30 s — a host that states its own window knows it better than our curve does.
3. **The scrape planner caps how many Boards of one ATS a shard may take** (`lpt_pack_capped`):
   plain LPT inside a ceiling of `ceil(n/m)` per ATS per shard. Only the upper bound protects the
   origin budget — a shard holding *few* Boards of an ATS costs nothing — so capping beats forcing
   an even deal, and keeps almost all of LPT's cost balance.

4. **Eightfold's detail pass runs at 25 multiplexed streams**, down from the shared default of
   100. Measured against a live board from a clean runner origin, 1,500 details per arm
   (`scripts/bench/probe_eightfold_throttle.py`):

| width | missing | wall | settled |
|---|---|---|---|
| 4 | 28.3% | 574 s | 200×1075, 429×59, 405×366 |
| 8 | 58.7% | 540 s | 200×619, 429×88, 405×793 |
| 25 | 59.2% | 199 s | 200×612, 429×200, 405×688 |
| 100 | 78.6% | 25 s | 200×321, 429×204, 405×975 |

Width 25 is chosen because it is the point where the cost is affordable and the gain is real.
Replaying the planner's own selection, the slice carries 89 Eightfold Boards totalling 50,521 jobs
(mean 568/Board), which the ATS cap spreads to **~3,400 detail fetches per shard, 4,965 at worst**:

| width | loss | typical shard | worst shard |
|---|---|---|---|
| 100 | 78.6% | 0.9 min | 1.4 min |
| 25 | 49.9% | 6.6 min | 9.7 min |
| 12 | 47.9% | 11.9 min | 17.6 min |
| 4 | 24.9% | 20.7 min | 30.6 min |

Width 4 halves the loss again but costs ~31 minutes on the worst shard, which does not fit beside
everything else in a 60-minute budget. Width 12 buys two points over width 25 for seven more
minutes on the worst shard. Width 25 costs ~10 minutes at worst for ~29 points of recovered
descriptions, and that fits.

These arms were run sequentially with a six-minute cool-off between them, because an earlier
concurrent matrix had them contending for the same per-origin budget and reading 8-19 points
pessimistic (78.6/59.2/58.7/28.3 at widths 100/25/8/4). One caveat remains: every rate here
predates the 405 retry, which spends wall-clock to recover fetches, so it moves both the loss and
the timing. The width is provisional, and one production run should be read before moving it.

## Consequences

**The description gap is narrowed, not closed.** The best affordable width still loses ~59% of
descriptions before the 405 retry recovers any of them, because the origin budget is far below
what a full Eightfold scrape needs at any speed a 60-minute shard can pay for. This ADR makes the
failure retried and visible, spends every shard's budget instead of a few, and buys back a fifth
of the descriptions; it does not make the rest arrive.

Grouped packing changes shard assignment for *every* ATS, not just Eightfold. Replaying the
planner's own selection path (`pick_boards` over the live ledger, 20,000 Boards into 15 shards):

| | cost-only LPT | capped by ATS |
|---|---|---|
| Eightfold Boards per shard | 1–9 | **4–7** |
| worst per-shard spread, any ATS | 42–51 | **13–14** |
| predicted makespan | ~6,770 s | **+1.8–2.9%** |
| makespan spread (max/min shard load) | 1.000× | 1.018–1.031× |

Ranges, not points: `pick_boards` carries a random exploration tail, so the slice differs between
replays and every figure here moves with it. The cap costs roughly 2–3% of predicted makespan,
which the 3–14× over-prediction absorbs. An earlier version ranked shards by `(count, load)`
rather than capping, forcing a perfectly even deal; it cost ~12% makespan and a 1.16× spread for
no extra protection, and was dropped.

A ceiling bounds the maximum, not the minimum, so it does not by itself guarantee every shard
holds some of an ATS. It does so here only because Eightfold has ~89 Boards against 15 shards:
with `n` well above `m`, the pigeonhole leaves no empty shard. An ATS with fewer Boards than
shards will still sit on a subset — which is fine, because a small ATS cannot exhaust an origin
budget in the first place.

Retrying 405 is a deliberate over-reach: 405 genuinely means "method not allowed", and on a host
that means it literally, three attempts are now spent discovering that. The codebase already
accepted this trade for 403. The `405-wall` counter exists so the cost is measurable rather than
assumed.

**This does not repair the ~16,771 jobs already embedded title-only.** They need a forced
re-embed once descriptions are reliably fetched, which is deliberately not in this change: doing it
before the gap closes would just re-embed them title-only again.

> Delivered by [ADR-0050](0050-persist-descriptions-across-runs.md): descriptions are now
> persisted across runs, and a vector recorded as built without one is re-embedded when its text
> arrives — so the repair is the pipeline's steady state rather than a one-off.

**The next change is required, not optional: cache descriptions across runs.** A job's description
does not change, so most of the ~42,600 detail fetches per run are re-fetches of text already
scraped once. It is the only lever that gets *under* the origin budget rather than spreading the
load or slowing it down, because it stops making the requests instead of making them politely. It
needs new persisted storage plus a per-shard fetch — a shard currently downloads nothing but its
board list — which is why it is a change of its own rather than a line in this one. The measurement
in this ADR is what promoted it from "revisit if needed" to the required follow-up.
