# ADR-0143: Trends retain Board deltas for arbitrary comparable cohorts

**Status:** accepted · **Date:** 2026-09-12 · **Extends:** [ADR-0040](0040-role-trend-ledger.md), [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md), and [ADR-0075](0075-ats-becomes-a-trends-ledger-dimension.md)

## Context

The Trends tab measures served-index stock. That deliberately avoids the partial-scrape noise
of one pipeline slice, but it still means a newly discovered Board contributes its whole existing
inventory at once. Adding an ATS, a company list, or a large Board can therefore look like role
growth even when no employer opened a role. Share removes a uniform corpus increase, but not a
new source whose role mix differs from the current index.

The existing ledger aggregates only `(ts, version, metric, family, band, ats)`. It has no Board
dimension, so a date-range-specific matched panel cannot be reconstructed from history without
inventing values.

## Decision

Every `role_trends` tick still appends the existing small aggregate ledger unchanged. In parallel,
it resolves every served Job to the same canonical Board identity index sync and prune use, counts
`(Board, metric, family, band, ATS)`, and writes only changes from the previous Board snapshot to
an append-only, version-stamped parquet delta ledger. The current Board snapshot is retained so
the next tick emits only its changes. A delta is durable before the snapshot; a following tick
recovers a delta that survived an interrupted snapshot write before it calculates another one.

`/trends?coverage=comparable&base=<time>` chooses the latest available measurement at or before
`base`, includes every Board first observed by that measurement, and replays only those Boards'
deltas through subsequent ticks. Thus a reader can choose any collected base point and see its
cohort at every later point. A Board added after the base remains in the default **All coverage**
series but cannot manufacture a rise in the comparable series.

The Board-delta ledger starts with this feature. Bases earlier than its first measurement return
no comparable data rather than inventing a historical cohort.

## Rejected alternatives

- **A single permanent established cohort.** Cheap, but it only answers one baseline question
  and becomes less representative as coverage expands.
- **A full Board snapshot every run.** Simple reads but rewrites the whole high-cardinality
  population even when little changed. Deltas make storage proportional to change instead.
- **Jobs per Board.** Boards vary by orders of magnitude, so this replaces source-composition
  bias with a noisier denominator.
- **Silently rebase full coverage whenever intake changes.** It hides the useful fact that
  HeadStart added coverage and erases the full-population question instead of separating it.

## Consequences

- Comparable history starts at the first post-deployment delta. It grows forward only and never
  claims to reconstruct the aggregate-only past.
- This isolates Board onboarding, not every measurement change. Centroid refits already use
  their own version boundary. A broad parser or model upgrade that changes rows on established
  Boards remains a methodology break and must be explicitly versioned/rebased when it is made.
- The aggregate Trends ledger stays small; the new Board-delta ledger grows with changed Board
  groups and is fetched beside it by the Space.
