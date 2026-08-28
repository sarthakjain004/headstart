# ADR-0083: Evict only on a second consecutive absence

**Status:** Accepted · **Date:** 2026-08-23 · **Supersedes nothing; amends ADR-0014's sync rule**

## Context

`plan_sync` decided eviction from a single scrape. An indexed id absent from this run's fresh ids
was deleted, immediately and permanently:

```python
missing = {i for i in ids if i not in fresh}
delete |= missing
```

Nothing in that comparison distinguishes **"the posting closed"** from **"this one scrape could
not confirm it"**. Both arrive as an id in the index and not in `fresh`.

`docs/pipeline/2026-08-23_false-board-eviction-root-cause.md` measured what that ambiguity cost.
Across the 15 runs audited, live postings were deleted from the served index by three unrelated
mechanisms that all terminate in the line above:

- **Greenhouse** returns silently short lists — HTTP 200, valid JSON, no error, no truncation
  reported. `databricks` returned 816 jobs of 821 in run `32574982652`; `metrostarsystems` 84 of
  90. Confirmed from the runs' own `scrape-fragment` artifacts, and the shard reported both boards
  as cleanly scraped. The scraper cannot detect this, so nothing downstream can either.
- **SuccessFactors** dropped jobs whose detail page loaded but yielded no parseable title. The
  loss was invisible to `report_detail_gaps`, so `mark_truncated` never fired (fixed in PR #266).
- **Workday** derived unstable ids, so the same live posting cycled under a new id every scrape
  (fixed in PR #265).

The two existing guards do not cover this shape:

- **`COLLAPSE_RATIO` (ADR-0046, bounded by ADR-0055)** is a *board-level* circuit breaker for mass
  truncation. It measures the share of a board's rows lost in one run and caps the drain at 25%.
  The measured incidents were 5 of 821 (0.6%) and 6 of 90 (6.7%) — far under the threshold. A
  handful of ids quietly missing from an otherwise normal-looking board is exactly what it is
  designed *not* to fire on.
- **`mark_truncated` (ADR-0053)** removes a board from the eviction scope entirely, but it
  requires the scraper to *know* its list came back short. Greenhouse's whole failure is that the
  response looks perfect.

Both guards are per-board and depend on the scrape being able to describe its own failure. The
loss is per-id, on scrapes that report success.

## Decision

**An indexed id is deleted only after it has been absent from two consecutive scrapes of its own
board.** A first absence is recorded and withheld.

`plan_sync` gains a `was_unconfirmed` parameter — the `unconfirmed` set it returned last run — and
a matching `unconfirmed` field on `SyncPlan`. `index sync` persists that set to
`data/state/unconfirmed_ids.txt` and hands it back next run, round-tripping through the HF dataset
like every other state file.

Four properties make this work, and each was a decision in its own right:

**The unit is scrapes of that board, not runs.** Only ~20,000 of ~66,000 live boards are in any
run's slice (`--max-boards 20000`, `EXPLORE_FRAC = 0.7`), and `index sync` already keeps
Unauthoritative Boards out of `scraped_boards` (ADR-0053). A board this run did not read is **no
evidence**, so its ids keep the state they had — the same convention the description-gap ledger
already uses ("A Board the run did not scrape, and an Unauthoritative Board, are no evidence and
keep their counts"). Counting an unscraped board as a confirmed sighting would reset every streak
and make the grace period unreachable; counting it as an absence would evict against a board
nobody looked at.

> **Amended 2026-08-28 by the Board-counting vocabulary (CONTEXT.md §Counting Boards).** The
> figure above was current when written; it is 85,631 today, and the phrase "live Boards" names
> no single number — the count a Slice is drawn from is the **Scrapable Board**. The ratio has
> widened, which strengthens the argument here rather than weakening it.

**N = 2, on measured grounds.** Every false eviction in the investigation was a *single isolated*
miss. The one id evicted twice, `successfactors:careers.hcltech.com:1364226855`, was verified
**present** in the scrape between its two evictions — and the mechanics force that pattern, since
a second eviction requires a re-add, which requires reappearing in `fresh_ids`. N=3 would buy
margin only against a failure not yet observed, at double the staleness.

**The collapse guard measures the eligible set.** The grace period runs first, so on the first run
of a mass truncation everything is withheld and `COLLAPSE_RATIO` never fires. If the board is
still short on its next scrape those ids become eligible, and the ratio caps the drain as before.
The two compose rather than duplicate: grace period = transient (one scrape), ratio = persistent
(two or more).

**Carry-forward is bounded by the live ledger.** An id whose board was not scraped keeps its entry
— but only while its board is still live. A board that leaves the ledger is never scraped again,
so its entries would otherwise persist for good, which is precisely the ratchet ADR-0055 had to
unwind for `held`. Those rows leave the index through `plan_prune`'s off-board sweep and their
entries leave with them. The check is per-board, not per-id, so it costs a handful of lookups
rather than an intersection against the whole index.

## Consequences

**A genuinely closed posting stays in the index one extra scrape of its board.** For a
priority-head board that is ~1 run (~1 hour); for a board reached through the random exploration
tail, ~4–5 runs. This is the cost, and it is the right side to err on: a stale posting is a bad
search result, a falsely evicted one is a live job the product cannot show at all.

**`plan_sync` stays pure.** The set is passed in and returned, exactly as `first_seen` already is,
so the eviction invariants remain unit-testable on CI's base-deps-only install — the property that
module's docstring exists to protect.

**Cold start needs no migration.** The file not existing reads as an empty set: the first run
after this ships withholds every absence and deletes nothing, and the run after it evicts
normally. No opt-out flag ships with it: nothing asked for one, and a half-wired escape hatch
is worse than none — disabling the grace period also has to suppress the state write, or the
"disabled" run overwrites the persisted set with an empty one and silently costs every id
its streak. `plan_sync` still accepts `was_unconfirmed=None` for callers that predate this.

**The file is rewritten each run, never appended**, so it cannot accrete: it is derived from the
run's own scrape plus what it carries forward. An id that reappeared, was pruned, or sat on a
board that left the ledger is simply not written again.

**This does not fix Workday's id instability**, and cannot: an id that changes value every scrape
is never absent *twice in a row* — it is a different id each time. PR #265 was still required.
The grace period covers transient absence, not identity churn.

**Greenhouse remains without a per-ATS guard.** Its `meta.total` signal is instrumented but
unshipped (issue #268) because it is unverified for the short-response case. The grace period
covers Greenhouse regardless of mechanism, which is why it was the stronger fix to make first.
