# ADR-0062: Drain the description gap by aiming the slice, and record what each vector actually saw

**Status:** accepted · **Date:** 2026-08-18 · **Relates to:**
[ADR-0022](0022-tech-priority-board-ordering.md),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md),
[ADR-0048](0048-skip-details-we-already-hold.md),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md),
[ADR-0050](0050-persist-descriptions-across-runs.md),
[ADR-0059](0059-two-board-keyspaces.md) · **Amends:**
[ADR-0061](0061-refreshable-metadata.md)

## Context

ADR-0061 made stored metadata refreshable, and closed with the one thing it could not fix: the
rows it can repair are the rows whose description the ADR-0050 store *settles*, and a large share
of the index has no store entry at all. Those Jobs cannot be repaired by any version bump, because
re-deriving without the text a value came from could only downgrade it. The only way to fix them is
to scrape their Board again — which never happens, because the slice is priority-ordered and these
Boards have not earned a score.

**Measured against the live store** (cached HF snapshot, 2026-08-18; the probe and the shipped
`update_ledgers gap` agree to the row):

| | jobs | boards |
|---|---:|---:|
| embedding store | 481,396 | |
| settled by the description store | 303,989 | |
| **unsettled** | **195,475** | 14,340 |
| − `join` (in `registry.DISABLED_ATS`, unreachable) | −1,093 | −632 |
| − case-variant Boards folded together (ADR-0023) | | −1,106 |
| − dead / excluded / parked, not on any slice | −15,177 | −166 |
| **= reachable backlog** | **179,205** | **12,436** |

ADR-0061 quoted 127,501 of 263,769 and called them "served rows". Both numbers are the **embedding
store**, read from a stale snapshot: `manifest.json` and `meta.jsonl` agree at exactly 263,769 in a
snapshot last written **2026-07-25**, and the store has grown 263,769 → 434,523 (Aug 10) → 481,396
(Aug 18) since. There is no served-vs-store distinction behind the discrepancy, only 24 days. The
figures above are re-measured against the current store, which is what `update_meta` rewrites and
what the join has on disk. The *served* LanceDB table is a third number again — 285,065 rows at the
2026-08-18 08:00 run — smaller than the store because `index prune` evicts table rows while the
store keeps them. Repairing a store row is never wasted work: it is correct the moment `index sync`
re-adds it.

## Decision

### 1. A fourth per-board ledger, and a reserved slice of every run

`update_ledgers gap` counts, per Board, the stored Jobs the description store has never settled,
and writes `data/state/board_description_gap.csv`. It runs in the join, after
`update_descriptions`, which is the one stage holding both inputs — `meta.jsonl` and the store —
and `data/state/` is the channel `scrape-plan` already fetches wholesale.

`pick_boards` reserves `GAP_FRAC` (0.05) of the **exploration tail** for those Boards. The priority
head is never touched: a random exploration pick is strictly worse than a Board already known to be
worth visiting, so the quota comes out of exploration or nowhere. Within the quota, **cheapest
class first** — the 6,488 listing-only Boards, whose descriptions arrive with the listing at one
request per Board, before the 7,220 detail-pass Boards that need a fetch per Job — then most
unsettled first.

**It self-cancels.** The ledger is recomputed from scratch each run, so a Board leaves it the moment
its descriptions settle, and an empty ledger reserves nothing: the slice is then byte-identical to
what it was before this existed. At ~700 slots per run the backlog drains in ~18 runs.

**Keyed lowercase.** Measured, this is not a detail: 1,693 of 13,708 gap Boards — 45,375 Jobs, 23%
of the backlog — matched the live slice only case-insensitively, and would have been stranded
forever by an as-observed key. It also folds ADR-0023's case-variant pairs into one row instead of
two half-counts.

**Guarded against a lost store.** The join fetches the description store on `|| echo ::warning::`,
so an empty store means a failed download, not a settled corpus. The ledger is not written in that
case — writing it would mark every Board gap-ful and hand the next run a slice built from a missing
file.

### 2. A settled description marks its row for re-derivation

Closing the gap repairs the *text* and leaves every number behind it stale: `embed_plan` skips ids
it has embedded, and `update_meta`'s sweep only fires on a `DERIVATIONS_VERSION` bump. Nothing else
would ever revisit the row, and the signal is not regenerable — once settled, a description is never
"newly settled" again.

So `update_descriptions` appends the ids it settles to `data/state/pending_rederive.txt`, and
`update_meta` runs the cascade for exactly those rows at an unchanged version, then clears the file.
Both entry kinds are queued: a text entry gives the cascade something new to read, an authoritative
`null` is equally a settled answer. It lives under `data/state/` rather than riding the corpus
artifact alone so that a lost artifact or a failed merge retries next run instead of stranding those
rows until the next version bump. The same lost-store guard applies: with no descriptions loaded the
queue is left alone rather than consumed, because re-deriving with no text would wipe the very
floors it exists to repair.

### 3. `has_description` is backfilled, and `embed_plan` stops guessing

**This amends ADR-0061**, which froze `has_description` on the grounds that refreshing it from the
store would hide title-only vectors from the upgrade path. That reasoning holds for a row that
*has* the flag. It does not hold for the ~294k rows that never had one: there, the flag's absence
is not information to protect, it is a hole `embed_plan` has been filling with a guess —

```python
if flag is False or (flag is None and row.get("ats") in detail_pass):
```

— which conflates *this ATS fetches descriptions separately* with *that fetch failed*. Measured, it
condemned 152,383 rows. `experience_source == "regex"` means the stored floor was read out of a
description, which proves one existed when the Doc was built, and that alone rescues **66,296** of
them; ADR-0050 measured the genuinely title-only population at ~16,771 index-wide, so the guess
over-approximated by roughly 9×.

`update_meta` now writes the flag once on any row lacking it: `True` where the evidence proves it,
and otherwise exactly what the inference already concluded — so recording it changes no behaviour,
it only stops the guess being re-made every run. `embed_plan` then reads `has_description is False`
and nothing else. The degraded set goes from 152,383 to **86,087**.

A row that already carries the flag is never rewritten. That part of ADR-0061 stands: it is a fact
about the vector, and only a re-embed may change it.

This is cheap where it counts — `has_description` is in `doc_prep.PLANNER_ONLY_FIELDS` and never
reaches the served table, so the backfill is a store-only write with no LanceDB churn.

## Consequences

Settling a gap Board's descriptions now (a) repairs the corpus text, (b) re-derives that row's
experience numbers in the same run, and (c) queues its vector for re-embedding only when the flag
actually says the vector was built without a description. The wave that follows is paced by
`GAP_FRAC` and by the corrected degraded set rather than by the old guess.

`update_meta` runs in the merge, one stage *after* `embed_plan` reads `meta.jsonl` in the join, so
the backfill reaches the planner on the following run. For one run, rows still lacking the flag
queue no upgrades at all — a delay, not a loss. The accepted risk of deleting the guess in the same
change is that `update_meta`'s step is `continue-on-error`: if the backfill never lands, those rows
are never queued for repair and nothing says so. The `given a has_description they never had` count
in the refresh log is what confirms it landed.

Two things this deliberately does **not** fix: `join`'s 1,093 rows stay unreachable (they leave by
eviction), and the 166 off-slice Boards holding 15,177 Jobs — dead, excluded or parked — are counted
in the ledger but can never be picked, so the reported backlog is slightly larger than the drainable
one.

## Alternatives considered

- **Compute the gap over the served table instead of the store** — smaller and more honest about
  what a user can hit, but it moves the ledger into the merge job (the only one holding
  `data/lancedb`) and adds a LanceDB read to the job whose reconcile cost ADR-0061 already flags as
  unmeasured. Repairing a store row is never wasted: it is correct the moment the row is re-added.
- **Boost gap Boards' priority scores instead of reserving slots** — a smaller diff, but `known`
  filters on `score > 0` and most gap Boards are unscored, so it would have to synthesise a score
  into a ledger whose units are EWMA tech-job counts.
- **Enlarge the slice until the backlog drains** — clears it in one or two runs at +54% Boards,
  against a predicted-makespan warning at 60 min and a storage quota that buys ~54 runs.
- **Suppress upgrades for gap rows entirely** — keeps this change out of the embed planner, but
  `update_descriptions` already fills the corpus from the store, so any scraped Board's degraded
  rows are upgraded today; suppressing would switch off a repair that currently works.
- **Read this run's store fragments as the re-derivation signal** — the ids are already on disk and
  need no new file, but it couples `update_meta` to fragment sequence numbering, which
  `cleanup-index`'s `--compact` folds away.
