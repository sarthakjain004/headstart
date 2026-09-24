# ADR-0209: A Detail pass that lands nothing stops, and no one detail can hold it open

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (what a lost detail does to a Board's
authority), [ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) (a killed Board's seconds are
a floor on its cost), [ADR-0201](0201-a-scraper-states-its-detail-request-once-and-the-base-runs-the-pass.md) (the one Detail pass this bounds)

## Context

Run `36003741124` (2026-09-24) took 91 minutes against the ~47 of the runs around it. One shard
ran its full 60-minute budget. It finished 1,343 of its 1,344 Boards in under six minutes. The
last one, `oracle:egud.fa.us2.oraclecloud.com`, logged its listing at 13:15:12 ("read 10000 of
12187 requisitions"), then nothing until the budget killed it at 14:11. The shard logged no line
at all in that time: 56 minutes spent on one Board's Detail pass, which Oracle runs over every
listed requisition.

The logs cannot say which of three things happened:

- every detail failed and retried;
- every detail crawled;
- a stream simply never returned.

The shard's network retries (1,056) sit within the range of two other shards in the same run
that finished normally (1,045 and 816). The same Board scraped locally the next day finished
the whole pass in 512 s. The failure was transient and did not reproduce.

The cost was not only the run. The kill recorded egud at 3,465 s in `board_cost.csv`, the floor
ADR-0064 says a kill proves. At that price its 31 tech jobs fall under the value gate, and the
gate now keeps it out for 14 days. Measured normally, egud costs ~5.6 min and would clear the
gate.

Each request already carries a bound: a 30 s timeout, a `Retry-After` capped at 30 s, and 3
attempts plus 2 earned. Nothing bounds the pass itself.

## Decision

`BaseScraper.run_detail_pass` bounds every Detail pass, on both transports, in two ways.

1. **The stall window.** Before each item starts, the pass checks when a detail last *landed*
   (returned fields). If none has landed for `_DETAIL_STALL_S` (600 s), the item is skipped and
   labelled `skipped after the detail pass stalled`. No request was formed for it, so it is noted
   as unattempted and shows as a cause on the Board's one gap line. 600 s is above one item's
   worst legitimate budget, about 430 s: 5 attempts at a 30 s timeout, capped 30 s waits and two
   egress rotations. So a stall that long is not one slow request.
2. **The per-item bound (multiplexed path).** Each item runs under `asyncio.wait_for` with
   `_DETAIL_ITEM_TIMEOUT_S` (900 s), labelled `timed out in the detail pass`. The stall window
   alone cannot end a pass whose in-flight items never return, because `gather` would still wait
   on them. The thread path gets no per-item bound: a Python thread cannot be cancelled. There,
   the budget's `os._exit` remains the backstop.

3. **No cost row for a stalled Board.** `detail_stalled` rides the Board's telemetry, and
   `harvest` does not record that run's seconds, so the previous row stands. The time was spent
   waiting on an origin that answered nothing. Recording it would price a healthy Board through
   the value gate exactly as the kill floor did: a stall-cut egud still costs 17-26 min, and at
   31 tech jobs that is under 2 a minute.

What a skipped detail means is left to each Scraper, as it already is for any lost detail. Oracle
reports and keeps the Job from the listing, and the ADR-0050 store supplies the held description.
A Scraper whose detail pass is load-bearing marks the Board unauthoritative, which is correct for
a Board that stopped answering.

## Options considered

- **A wall-clock cap per Board or per Detail pass.** Rejected. It cuts slow but healthy passes:
  `oracle:ejwl` legitimately spends ~26 min and earns 34 tech jobs a minute. Any cap low enough to
  matter lands inside the range of real passes.
- **Abandoning a Board from `harvest` after a deadline.** Rejected. The thread cannot be killed,
  and the Board would be deferred with nothing banked. It would also be costed at the deadline,
  which is the same wrong ADR-0064 floor this ADR is trying to stop writing.
- **Skipping held details on Oracle (ADR-0048).** Rejected. It would shrink egud's pass from
  10,000 items to a few hundred. But Oracle's detail supplies `JobSchedule` and more that its
  listing does not (80.6% against 1.1%), so a skipped detail would blank those fields on every
  held Job. The trade is a separate decision, not a hang fix.

## Consequences

- **Bounded.** If every detail fails, the pass ends within about 17-25 min of its last landed
  detail: the 600 s window, then the in-flight items' own ~430 s budget. If multiplexed items
  never return, it ends within 25 min at most (900 s bound plus the window).
- **Not bounded.** A pass that still lands details is never cut, however slowly it goes. That
  includes a pass landing as few as one item in about twenty-two, which keeps resetting the
  window. The shard budget, its `os._exit`, and ADR-0064's kill floor stay the backstop there, as
  before. So do three other cases:
  - a stuck item on the thread path (Taleo BE, Taleo Enterprise, Zwayam, Apple);
  - Scrapers that compose their own pass (Workday, ADP, Cornerstone, Gem);
  - the listing phase.
- A pass behind a transient wall longer than 10 min on a low-width Scraper (Trakstar at 4 streams,
  Jobvite and Eightfold at 6) drops its remaining details once the window trips. Where a Scraper
  treats details as load-bearing, the Board is unauthoritative that run (ADR-0053).
- egud's current 3,465 s row was written before this and stands until the gate's 14-day re-check.
  This ADR stops the next one; it does not rewrite the ledger.
