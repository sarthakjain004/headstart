# ADR-0073: Narrow six retail-dominated Workday boards at the source

- Status: Accepted
- Date: 2026-08-20
- Relates to: [ADR-0017](0017-tech-role-filter.md) (named this exact mechanism and deliberately
  deferred it — this ADR is its first activation), [ADR-0025](0025-parallelize-nightly-pipeline.md)
  / [ADR-0026](0026-parallelize-nightly-scrape.md) (the fan-out whose critical path this addresses),
  [ADR-0071](0071-back-to-back-runs-instead-of-a-fixed-cadence.md) (why a shard's duration now
  converts directly into extra runs/day, which is what makes this worth doing)

## Context

A survey of 20 recent pipeline runs (`docs/pipeline/2026-08-20_cadence-settle-in-and-critical-path.md`
§3, §6) found six Workday boards that each, on some run, were the single largest contributor to a
shard's wall-clock — 90-98% of that shard was one board, up to 49.5 minutes in a single shard:
Walmart, CVS Health, Target, TJX, Loblaw, and Lowe's. All six share the same shape: a huge
retail/store-operations board (10,000-19,000+ postings) in which the tech-equivalent category is a
sliver — 0.2% to 4.5% of the total.

ADR-0017 already answered where the *authoritative* tech gate belongs: a post-hoc, recall-biased
filter after the scrape, never a source-side facet, because "department taxonomies are inconsistent
and a server-side facet *will* drop tech jobs mis-filed under an odd department — violating the
recall constraint." That decision stands unchanged for the served index. But that same ADR named
source-query filtering as "the right lever for *scraping* cost… kept as a future, complementary
scrape-volume optimisation, never the authoritative gate" — a deferred option, not implemented on
any scraper until now. This ADR is that activation, scoped to exactly the six boards where the
critical-path cost is now large enough (and, per ADR-0071, directly convertible into runs/day) to
justify the tradeoff ADR-0017 flagged.

## Decision

Six Workday boards are fixed, at the scrape's very first request, to their own tech-labeled
`jobFamilyGroup` value(s) plus `timeType=Full time`. Implemented as a `_FIXED_FACETS_BY_SLUG` map
in `src/headstart/scrapers/workday.py`, keyed by the board's exact slug, seeded into `fetch_raw`'s
initial `_exhaust(applied, …)` call in place of `{}`. Every other Workday board is unaffected — the
existing `_SUBDIVISION_FACETS` recursion (which subdivides a capped query into slices whose
*union* is the whole board, the recall-safe mechanism) is untouched, and the new map defaults to
`{}` for any slug not explicitly listed.

Live-checked against each board's own API, 2026-08-20:

| board | total postings | tech category used | narrowed to | % of board |
| --- | --- | --- | --- | --- |
| Walmart | 19,272 | Technology | 823 | 4.5% |
| CVS Health | 19,283 | Technology | 187 | 1.0% |
| Target | ~11,900 | Technology | 118 | 1.0% |
| TJX | 10,349 | **Information Technology** (TJX's taxonomy has no "Technology" label) | 63 | 0.6% |
| Loblaw (`myview.wd3/paradox_careers`) | 12,438 | Technology + Digital & Ecommerce | 34 | 0.3% |
| Lowe's | 12,029 | Technology + Digital + IND_Digital | 73 | 0.6% |

Two things were decided per board, explicitly, rather than by one blanket rule: TJX's category is
genuinely named differently ("Information Technology", not "Technology"), and Lowe's/Loblaw split
tech-adjacent roles across more than one small bucket — which extra buckets to fold in (Lowe's
"Digital"/"IND_Digital", Loblaw's "Digital & Ecommerce") was a human call each time, checked
against that board's own live facet counts, not inferred from the label alone. Target's board also
carries a `timeType=Variable` majority used for retail scheduling; checked specifically, all of its
Technology postings are independently Full time, so that filter costs nothing extra there.

## Rejected alternatives

- **A per-board scrape timeout or finer shard split, instead of narrowing.** Preserves full recall
  — the option this ADR does *not* take. Rejected for these six specifically because the win from
  narrowing is enormous (each board drops to 0.2-4.5% of its former size, comfortably under
  Workday's single-query 2,000 cap with no further subdivision needed) and directly, permanently
  removes the wasted embedding/index compute on postings HeadStart was never going to keep, not
  just the wasted scrape time. Still the right tool for boards where no clean tech category exists
  or the recall cost looks worse than the time saved — nothing here forecloses it elsewhere.
- **A blanket rule (e.g. always fixed to whatever facet value is literally named "Technology"),
  applied automatically to any heavy board.** Rejected on the same evidence this decision surfaced:
  TJX has no "Technology" label at all, and Lowe's/Loblaw split tech roles across multiple buckets
  a blanket rule would miss. Automating this would silently under-scope some boards and require the
  same manual verification anyway to catch it — so verification happened first, per board, and the
  result is six explicit dict entries, not a rule.
- **Leaving all six as-is.** Rejected: under the back-to-back cadence (ADR-0071), a board that
  floors a shard at 90-98% costs runs/day directly, and these six were consuming that budget for
  postings that were never going to survive the post-hoc tech filter or make it into the served
  index anyway.

## Consequences

**A permanent, board-scoped recall cost, distinct from ADR-0017's guarantee for every other
board.** For these six companies only, a real tech job filed under a department other than the
listed category (or marked Part time / Variable) is now invisible to HeadStart — never fetched, so
the post-hoc filter never gets a chance to see it. This is not "deprioritized," it's dropped. Every
board's specific tradeoff (the categories and postings given up) is documented inline in
`_FIXED_FACETS_BY_SLUG` and in `docs/pipeline/2026-08-20_cadence-settle-in-and-critical-path.md` §6,
so the cost is visible to whoever revisits this, not buried in a diff.

**Extending this to a new board is not a copy-paste.** Each board's own facet labels and bucket
split must be checked live before being added — TJX and Lowe's/Loblaw are proof the pattern isn't
uniform. A future addition should re-run the same live verification this ADR's table came from, not
assume another retailer's "Technology" id or label shape.

**Watch whether these six actually drop off the critical path.** The doc this ADR cites should be
re-checked against the next several scheduled runs (`analyse-fanout-run`) — if a board still floors
a shard after narrowing, the assumption that its `jobFamilyGroup` count reported at survey time
still matches what the live query returns should be re-verified before concluding the fix failed.
