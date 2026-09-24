# ADR-0200: A Board scraped empty is in the eviction scope

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0161](0161-the-eviction-scope-travels-as-board-keys.md) (its rejected option "derive the
scope from the shard reports") · **Relates to:**
[ADR-0014](0014-search-index-ingestion-and-freshness.md) (board-scoped eviction),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (prune),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (Unauthoritative Boards),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (grace period)

## Context

`index sync` evicts a served row only when the row's Board is in the run's eviction scope,
`data/state/scraped_boards.json`, and the row's id is missing from the scrape. `scrape_join` built
that scope from emitted job lines only. A Board that answers cleanly with no postings writes no
line, so it never entered the scope, and none of its rows could be evicted.

ADR-0161 considered reading the shard reports instead and rejected it. Its reason: a Board that
yields zero jobs "is deliberately out of scope today (ADR-0023 prune owns those rows)". **That
premise is false.** Prune removes rows only on Boards missing from its keep-set, and the keep-set is
`scrapable_boards.load(min_jobs=0)`. A Board the ledger calls live stays kept with zero postings,
and liveness rows are re-probed rarely (many were last probed on 2026-07-03). Nothing owned those
rows.

Measured on served table v121 (2026-09-24): 1,036 Boards whose last scrape recorded 0 jobs still
served 5,293 rows. 185 of those Boards (1,627 rows) are quarantined as gone, which is a separate
case (see below). Live checks of the rest found them empty, for example `lever:whoop` (74 rows
served) and `lever:mistral`. Three scrapers' comments (ashby, workable, recruitee) already assumed
the opposite behaviour: that a quiet `[]` "would land it in `boards_ok` and evict its rows two runs
later".

## Decision

`scrape_join` adds every shard report's `boards_ok` Board to the scope, keyed through
`board_key_of`: the `board_key()` its ids carry, so it matches `resolve_board` on those ids. Nothing
else changes:

- A truncated Board is in `boards_ok` too. `index sync` still subtracts it as Unauthoritative
  (ADR-0053).
- A Board that raised, a 404 included, is not in `boards_ok` and stays out.
- The ADR-0083 grace period still applies. A row is evicted only after two consecutive clean
  scrapes of its Board omit it.

## Rejected

- **Keep the ADR-0161 behaviour and let prune own empty Boards.** Prune cannot own them without
  dropping live-but-empty Boards from its keep-set. Those Boards would then leave the scrape list's
  mirror in prune, and their first new posting would have to wait for a liveness re-probe to be
  served.
- **Evict an empty Board's rows at once.** That bypasses the grace period, and a transient empty
  answer would delete a whole Board.

## Consequences

- Up to about 851 Boards / 3,666 rows drain over each Board's next two clean scrapes, as their
  Boards reach a run's slice. That figure is an upper bound (the cost ledger was joined by
  lowercased key).
- **Every quiet `[]` now evicts after two runs.** That covers the `note_unreadable_board` exits in
  ashby, bamboohr, clearcompany, freshteam, cornerstone, google and tesla, and SuccessFactors'
  three listing surfaces, which each map a non-200 to "nothing". A scraper that returns `[]` on a
  failure it could have raised on now costs evictions, where before it cost nothing. Such a
  failure should raise.
- **Not covered: gone Boards.** A Board that answers 404 raises, so it never reaches `boards_ok`.
  [ADR-0058](0058-consecutive-gone-quarantine.md) quarantine stops scraping it, but its rows
  stay served (910 quarantined Boards, 6,004 rows on 2026-09-24). That needs its own change.
