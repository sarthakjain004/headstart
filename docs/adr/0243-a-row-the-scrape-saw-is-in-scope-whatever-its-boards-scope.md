# ADR-0243: A row the scrape saw is in scope, whatever its Board's scope

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (what a scope exclusion withholds) and
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (how sync compares a Board to its scope)

## Context

`index sync` evicts a served row once two consecutive scrapes of its Board miss it (ADR-0083), and
only on Boards in the eviction scope. Two kinds of row were never in scope, and so were served
after their postings stopped being tech postings or stopped existing.

**Rows the scrape returned and the tech filter rejected, on an Unauthoritative Board.** ADR-0053
drops a Board whose list came back truncated or raised from the scope, because its missing rows
are unread, not closed. `freshteam:abnhire` is over its 1,000-job widget cap on every run, so it
is Unauthoritative on every run and never re-enters scope. On run 36218633315 its exclusion kept
729 eviction-candidate rows out of scope, and they were not unread: the Board's raw listing held
them and `filter_tech` rejected them, so they are live postings the current filter does not count
as tech. The merge job never saw that: it holds the tech corpus and the Board-key scope
`scrape_join` recorded (ADR-0161), not the raw scrape.

**Rows stored under a Board casing the scrape no longer emits.** `resolve_board` returns the
Board in the id's own casing, and sync compared that to the scope exactly. A row stored as
`workday:boeing/external_careers` while the scrape emits `EXTERNAL_CAREERS` was never in scope.
`plan_prune` collapses casing duplicates, but a fossil with no live-cased twin has none: on
2026-09-26 the embedding store held 1,969 such rows on 343 Boards (Workday 1,824, SmartRecruiters
145), closed postings with nothing to take them out.

## Decision

1. `scrape_join` records the ids every Unauthoritative Board's list returned, beside the Board
   record: `data/state/unauthoritative_board_ids.txt`. It already streams every raw record, so this
   costs one membership check per line and a file the size of those Boards' listings. `index sync`
   subtracts the tech corpus and keeps the ids on this run's scope-excluded Boards. Those ids are
   passed to `plan_sync` as `rejected`: absent from the fresh ids on a Board that *was* read, so
   they take the ordinary grace period whatever their Board's scope. The rest of that Board stays
   out of scope. The ADR-0053 withholding line stops counting them and a line of its own reports
   them.
2. `plan_sync` matches its scope case-folded (`lower_key`), as every other Board-key match does.
   A fossil-cased row its Board's scrape did not re-emit takes the grace period like any other
   absence. `resolve_board` still returns the id's own casing, which `plan_prune`'s casing dedup
   groups by.

Both still go through the grace period: an absence is never evicted on one scrape. A tech-filter
verdict that flips on a degraded read gets the same second look any other short read gets.

## Consequences

* abnhire's 729 rows evict over two runs. The same holds for any Board over a hard cap whose
  listing includes postings the filter rejects.
* The first run after this ships marks the fossils Unconfirmed and the second evicts them: about
  2,000 rows, spread over the runs that scrape their Boards. A fossil whose native id is the live
  row's is a duplicate; prune would have taken it out if the live copy were served, and sync now
  adds that copy once the fossil is gone.
* `unauthoritative_board_ids.txt` rides `data/state` to the merge job and to the Hub like its
  siblings, and is rewritten every run, empty when every list was authoritative. A local sync
  that reads another run's copy is bounded: only ids on Boards this run excluded count.
* Rejected ids on a Board ADR-0053 excludes as *raised* cannot exist: a Board that raised wrote
  no lines.
* Both kinds of eviction are queued for Trends like any other (ADR-0227), so they are booked as
  Closed over the two ticks that evict them: ~730 rejected rows and ~2,000 fossils. A rejected row
  on an in-scope Board has always been booked that way, and the fossils are closures, only late.
* A fossil whose live-cased copy is not embedded yet (non-English, or awaiting an embed) leaves
  the table before that copy arrives. It was a duplicate of a posting the corpus now carries
  under another spelling, so nothing is lost that the next embed does not bring back.
