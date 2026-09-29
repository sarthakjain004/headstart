# ADR-0292: The description store is not reaped until a "last listed" signal exists

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store, which named this gap),
[ADR-0048](0048-skip-details-we-already-hold.md) (the skip-list the store also is),
[ADR-0190](0190-the-embedding-store-keeps-only-served-and-scraped-jobs.md) (the embedding half, already
reaped), [ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md) ·
**Issue:** #185

## Context

The ADR-0050 description store (`data/descriptions/`) keeps every description it has ever held.
When a posting closes, nothing drops its text. #185 asked for a reaper. Two of its three halves
are done: the gap ledger stopped counting unreachable ids (#224), and ADR-0190's `embed_prune`
drops vectors no served row uses.

Measured on 2026-09-29, from the full store pulled from HF, against served table v298 and the
committed liveness ledgers:

| | ids |
|---|---:|
| held | 1,004,419 |
| served | 496,753 |
| held but not served | 508,324 (50.6%) |
| … on a Board no longer Scrapable | 69,083 (14% of those) |

- The store is 984 MB gzipped on HF. It was ~362 MB on 2026-08-26, so it grows about 19 MB a day.
- The dataset's `usedStorage` is 7.06 GB of the 100 GB quota (#693's Phase 0).

**"Not served" is not "closed."** Most held-but-unserved ids are on Boards still scraped, and many
are live postings:
- Jobs on Dormant Boards (ADR-0250), most of SmartRecruiters' 93,781;
- dedup losers, which sync refuses or prune removes;
- non-English Jobs, which are never embedded.

The store is also the scrape's skip-list (ADR-0048), and `update_descriptions` banks text from
the tech corpus only. So reaping a live posting's text does more than cost one re-fetch: the
scraper would fetch its detail on every scrape, and nothing would ever put the text back.

**ADR-0227's eviction queue is not a closure record either.** `role_trends` drains it every
tick, and Dormant-Board evictions pass through it.

## Decision

**No reaper now.** Only one design is safe: reap an id when its Board has been read
authoritatively at least twice since the id was last *listed*.
- "Listed" means seen in the full scrape, before the tech filter, so a Dormant, non-English or
  dedup-losing posting that is still listed is never reaped.
- It needs a new state ledger of id → last day listed, kept for held ids and written by the join
  job, which reads the full scrape.
- The reap step runs in `cleanup-index` next to `--compact`, and never touches an id the table
  serves.

That puts new state and a new write on the join job's critical path, and a delete path on
source-of-truth data, to recover about half a gigabyte. At today's size and growth, the cost and
the risk outweigh the saving.

**Build it when any of these holds**, measured on HF or in the merge logs:
- the description store passes **2.5 GB** gzipped (about 80 days at the current rate);
- `update_meta`'s sweep, which loads every held description (`held_descriptions`), passes half
  the merge runner's memory;
- `usedStorage` after `reclaim_storage` passes **25 GB**.

## Alternatives

- **Reap every id not served** (a diff against the table in `cleanup-index`). It drops the text
  of live Dormant, non-English and dedup-losing postings, with the re-fetch cost above.
- **Reap what sync evicts, from the eviction queue with a delay.** The queue is drained every
  tick, and it carries Dormant evictions of postings still listed.
- **Reap only ids on Boards no longer Scrapable** (69,083). It is state-free and safe for the
  scrape, but it recovers 7% of the store, and a buried Taleo section (19,803 of them) can come
  back.
- **Build the full design now.** Deferred for the cost and risk above; it is written down here so
  it isn't re-derived.

## Consequences

- The store keeps growing with closures, at about 19 MB a day. Nothing reads the dead entries
  except `compact` and the sweep's full load.
- Whoever builds the reaper starts from the design above, and from the trigger that fired.
