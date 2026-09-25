# ADR-0188: A change to which rows count as duplicates is a Trends epoch

**Status:** accepted · **Date:** 2026-09-24 · **Extends:**
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (the epoch ledger this adds
a fifth value to) · **Relates to:** [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the
duplicate prune), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the alias ledger)

## Context

Two dedup changes were decided on 2026-09-24. Taleo Enterprise career sections whose requisitions
are a subset of another section of the same tenant are aliased onto it in the alias ledger (served
index v654: 20,338 of 20,454 duplicate rows), and a Workday requisition is served once per tenant
instead of once per site (7,146 rows). Merged, they remove about 27k served rows, each in the one
tick its change first runs. A third, aliasing the Eightfold side of 45 cross-ATS Board pairs, will
remove another 9,872.

`role_trends` records a removed row as a negative stock delta. The Trends tab would draw the
removal as a sharp decline in whichever families those postings sat in, which is the misreading
ADR-0164 exists to prevent: a change in how we count shown as a change in hiring. ADR-0164's
markers fire on a centroid refit, a family-map edit, or a tech-filter or derivations version bump,
none of which a dedup change touches. The Company-level Trends in flight on PR #597 step down the
same way, since an aliased Board's history stays in the Board-delta ledger.

## Decision

1. **A fifth stamped value, `index_plan.DEDUP_VERSION`.** `role_trends` stamps it beside the other
   four, and the Space labels a change in it "duplicate removal changed". It lives beside
   `plan_prune` because that is where every dedup change takes effect: rows leave the served table
   there, whether their Board was aliased or they lost a duplicate group. `board_aliases` points
   back to it, since a new alias signal is the other way to change the rules.
2. **Bumped by hand, only for a rule change.** A new grouping in `plan_prune` or a new alias
   signal bumps it, and so does an existing signal's first ledger for an ATS (amended by
   [ADR-0222](0222-an-icims-portal-that-redirects-to-another-is-an-alias.md): iCIMS's first
   `redirect` ledger removed ~5,239 rows in one tick). A routine rewrite of an existing ledger does not,
   because the rows it moves are the ordinary churn of Boards changing, not a new definition, and
   neither does an `excluded_and_parked.PARKED_BOARDS` entry, which is a temporary hold (CONTEXT.md §Parked),
   not a duplicate rule. A content hash of the alias ledgers would move on every such rewrite and
   mark noise.
3. **A dedup rule removes rows through `index prune`.** The marker sits on the step only because
   prune evicts with no grace period. A rule that instead stopped emitting ids at scrape time would
   drain through `sync`'s two-scrape grace (ADR-0083) Board by Board over many runs, and the chart
   would show a slow decline after the marker rather than a marked step. Both rules decided on
   2026-09-24 go through prune; the counter's comment asks the next one to as well.
4. **The file is upgraded, not rebuilt.** `trends_epochs` rebuilds a file whose header it does not
   recognise, and adding a column would otherwise have tripped that and erased every marker
   recorded so far. A file with exactly the pre-column header is rewritten in the new shape
   instead, each old row taking `1`, the version the rules had when the column was added. The
   value is fixed rather than the live constant, so a bump that lands before the first upgrading
   tick still reads as a boundary. The rewrite goes to a temporary file renamed over the original,
   and that file is removed if the write fails, so neither a truncated file nor a stray `.tmp`
   reaches the merge stage's upload of `data/state`. Checked against the production file on
   2026-09-24: its six rows survive, the upgrade adds no row, and a bump to `2` adds exactly one.
5. **The Space reads the column with `.get`.** Between this change's deploy and the next pipeline
   tick, the Space loads a file that has no `dedup_version` yet.

## Consequences

- A dedup change marks the tick its rows leave, provided its PR bumps the counter. That is a number
  someone has to remember to move, the failure `DERIVATIONS_VERSION` has shown twice; the
  counter's own comment says when to bump.
- Series are marked, not corrected: the step is still drawn, with a marker saying why. Hiding it
  from deltas entirely would need `role_trends` to know why each row left, and was not chosen.

## Amendment (2026-09-25, ADR-0210)

Decision 3's premise — a dedup rule's removals land on the tick the marker sits on — no longer holds
for every rule. ADR-0210's requisition rule removes an Eightfold row only once both it and its
backing row carry a `requisition` stamp, and stamps arrive as each Board is re-scraped. Measured
on v654 and the 2026-09-24 scrape cadence, 99.6% of its 10,296 removals land on the marker's tick
and the rest within about a day. The marker is still stamped (`DEDUP_VERSION` 5), and every
removal a dedup rule makes is now also recorded, per run, Board and rule, in
`data/state/dedup_evictions.csv`, under the same `ts` `role_trends` stamps — so a Trends reader can
add dedup removals back exactly, whenever they land, rather than lean on the marker's timing.
