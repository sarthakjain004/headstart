# ADR-0022: Tech-priority board ordering — EWMA ledger, priority-first slices

- Status: Accepted
- Date: 2026-07-06
- Builds on [ADR-0020](0020-free-tier-deployment.md) (the nightly slice + state round-trip) and
  the time-budgeted steps added after it; ordering feeds [ADR-0014](0014-search-index-ingestion-and-freshness.md)
  ingestion semantics unchanged

## Context

The pipeline scrapes a random `--max-boards` slice of ~75k live boards and embeds under a CPU
time budget that regularly expires (by design — partial progress banks). The tech corpus's ~72k
jobs come from only ~5.5k boards, so random ordering burns scrape and embed budget on boards
that have never yielded a tech job while high-yield boards wait ~9–10 nights per rotation slot.
Requirement: boards that currently or historically carry tech jobs are scraped first (most tech
jobs first) and their docs embedded first, weighting current counts above historical ones.

## Decision

**Signal: a sticky per-board EWMA of tech-job counts**, persisted as
`data/state/board_priority.csv` (`board,score,last_tech_jobs,updated_at`, `board` =
`{ats}:{slug}` per `corpus.board_of`) in the HF-dataset state round-trip. After each tech
filter, boards present in that run's snapshot update as `score = 0.7·tech_count + 0.3·prev`
(new boards seed from prev = 0); boards the run didn't scrape carry their rows unchanged — a
partial harvest must not decay what it didn't look at. Rows decaying below 0.05 are pruned
(~3 consecutive zero-tech scrapes); exploration re-adds a board that hires tech again.

**Slice: priority head, exploration tail.** `pick_boards` gives scored boards
`max_boards − round(0.3·max_boards)` slots, ordered score-desc (random tiebreak), and fills the
remaining ~30% with a random rotation of the unscored rest — discovery of newly tech-hiring
boards never starves. The head leads the list, and `scrape_all` starts boards in list order, so
a time-budget-truncated scrape still covers the top boards. No ledger file → pure shuffle + cap
(the previous behavior; also the bootstrap path).

**Embed: same scores, within buckets.** `embed_run.py` keeps the token-length bucket batching
(shape pinning, smallest bucket first) and reorders each bucket's doc indices board-score-desc,
so an expiring embed budget banks the highest-value boards' docs first.

## Rejected alternatives

- **Deriving priority from the embedding store's `meta.jsonl`** — no new state, but eviction
  healing (ADR-0021) zeroes exactly the boards being re-embedded, deprioritizing them at the
  worst moment. The sticky ledger survives evictions by construction.
- **Ledger inside `data/embeddings/jobs/`** — free ride on the existing state patterns, but that
  dir is documented as regenerable/wipeable; sticky history doesn't belong in it.
- **All-time-max scoring** — a board that stopped hiring tech would hold a top slot forever;
  the EWMA matches "weight current more, past still counts".
- **Interleaving priority and exploration** — spreads discovery across the scrape window instead
  of risking it under a truncated run, but violates "tech boards scraped first"; deferred as the
  mitigation if truncated nights measurably starve exploration.
- **Committing the ledger to git** — the workflow token is `contents: read`, and nightly churn
  in git history is what ADR-0020 rejected for state generally.

## Consequences

Every time-budgeted run spends itself on the highest-yield boards: the known tech set refreshes
roughly nightly (5.5k boards fit one slice's head) instead of every ~9–10 nights, and embed
budgets bank the docs that matter most first. Exploration throughput drops to ~30% of a slice
(~2.4k boards/run, full unknown-set rotation ≈ every 4–5 days at 4 runs/day). Boards absent
from the live ledger keep sticky rows harmlessly (they can't be scheduled). The ledger seeds
warm from the local full tech corpus; until the first update lands, CI falls back to the old
shuffle.
