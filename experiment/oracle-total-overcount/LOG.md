# Does Oracle's paginated walk lose rows, or does `TotalJobsCount` over-count?

**Date:** 2026-09-21. **Outcome:** the counter over-counts; the walk loses nothing.
**Decision:** [ADR-0169](../../docs/adr/0169-an-empty-page-ends-an-oracle-board-totaljobscount-does-not.md).

## Why

The ten-run log review (`docs/pipeline/2026-09-21_ten-run-log-review.md`) found 8 Oracle Boards
scope-excluded on 9 of 9 runs with verdicts like `read 27 of 600 requisitions — the rest is unread,
not absent`. ADR-0053's exclusion has no drain, so those Boards' closed postings were being served
indefinitely. Either the walk was abandoning reachable rows, or the stated total was not a count of
reachable rows.

## Method

`walk_vs_sweep.py` — a differential loop. Per Board: (a) the scraper's real `_listing()` walk,
(b) an exhaustive sweep of every 200-row offset window from 0 to the stated total, *past* the empty
page the walk stops on, deduped by requisition `Id`. `lost = sweep - walk`.

It drives `_listing()` rather than `fetch_raw()` on purpose: `fetch_raw` also runs the detail pass,
which costs a request per posting and measures nothing this asks about.

    python experiment/oracle-total-overcount/walk_vs_sweep.py etud.fa.us8.oraclecloud.com ...

**What the `ceiling` column is for.** The sweep can only read offsets the API serves, so on a Board
stating more than 10,000 it stops at exactly the wall the walk stops at, and `lost = 0` there is
true by construction rather than measured. Those rows are printed and excluded from the claim.

## Result

Two captures, before and after the fix. They are **not quite the same population** — see the note
under the result:

- `artifacts/2026-09-21_walk-vs-sweep-before-the-fix.txt` — **9** truncation verdicts.
- `artifacts/2026-09-21_walk-vs-sweep-after-the-fix.txt` — **1**, and it is the ceiling. Adds
  `jpmc-dev3.fa` (1,608 of a stated 6,786), the largest sub-ceiling case.

**Zero rows missed on every Board, in both captures.** 17 Boards were measured; the claim rests on
the **16** stating under 10,000, where the sweep genuinely looks past where the walk stopped. Stated
totals there spanned 123–6,786, and the **9** carrying a sub-ceiling truncation verdict were all
complete reads. Eight of those 9 are in the *before* capture; the ninth, `jpmc-dev3`, was added
only to the *after* run, and its pre-fix verdict rests instead on production having logged
`read 1608 of 6786 requisitions` for it on **9 consecutive runs**. The one genuinely-incomplete Board, `eubt.fa.us6` (10,000 of 78,431), is the
ceiling case — caught by its own clause, which this change leaves alone.

## Two false starts worth recording

1. **"An empty page isn't the end — it's a pagination bug."** The first probe showed `offset=50`
   returning 0 rows while `offset=200` returned 5 and `offset=400` returned 18, which reads as the
   walk abandoning real data. It is not: offset 50 is not on the walk's 200-step grid, and the sweep
   *on that grid* matches the walk exactly. The pages are **sparse**, not missing — `ialmme-test`
   serves 4 rows in its first 200-row window, then 5, then 18, then ends.
2. **`etud.fa.us8` was the load-bearing counter-example, and it folded.** It is cited in three
   places as a measured loss ("89 of 114 in a single page") and was the widest benign-vs-real margin
   `_SLACK_PER_PAGE = 2` claimed to separate. Re-probed: states 123, serves 98, sweep finds those
   same 98. The technique that cleared `egjl` as benign in the original measurement clears `etud`
   too — it just was not applied to it.
