# ADR-0076: A page lost mid-crawl truncates the list; losing most of them fails the crawl

**Status:** accepted · **Date:** 2026-08-20 · **Amends:**
[ADR-0058](0058-consecutive-gone-quarantine.md) (whose "a listing error must raise" rule this
narrows to the *first* page) · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the truncation channel this uses),
[ADR-0047](0047-pace-against-the-origin.md) (the fan-out that made this bite)

## Context

`workday._paginate` fanned a query's pages out over `_PAGE_STREAMS` concurrent streams and then
re-raised the first exception any of them produced. A page that spends `http.fetch_async`'s retry
ladder on a persisting 429 reaches `raise_for_status()`, so one throttled page discarded every
page that *had* arrived and failed the whole board.

The bias is the problem. A bigger board fires more page requests at one origin, so it is likelier
to trip the per-origin limit — failure was aimed at exactly the boards worth most. Measured on run
32249345870 against the four before it (#194): workday 494,891 jobs against a 632k–672k band,
board failures 144 against 15–57, 104 of them HTTP 429. The 117 failed boards that matched the
priority ledger averaged **4.4x** its mean tech-job count — nvidia (1,880), Northrop Grumman
(1,714), Hitachi (991), Walmart (912) — and the user-visible cost was 9,437 tech jobs, 11.6% of
workday's.

The machinery to do better already existed and was aimed at the wrong case. ADR-0053's
`mark_truncated` was wired into `_paginate` for **404ed** pages only, and the issue records that
tripwire has *never fired*: zero `board partial` lines across three runs. The case that does
occur every run had no path to it.

Two questions had to be settled, and neither has data to settle it:

1. **Where does a partial stop being worth keeping?** "Lose the 20 postings on one page" is
   plainly better than losing nvidia; a query that reads two of its twenty pages plainly is not a
   description of that query's postings, and marking it truncated would tell `index sync` to
   preserve rows we never re-read.
2. **What must still fail?** ADR-0058 counts only a *raised* 404/410 as a gone-verdict, so
   swallowing a listing error is how a dead board becomes un-quarantinable. That rule cannot be
   dropped wholesale.

## Decision

**A page lost mid-crawl — 404ed or retry-exhausted, the cause no longer distinguished — counts
against the query's page total and marks the list truncated. Past `_MAX_LOST_PAGE_SHARE` (0.5) of
that total, the crawl fails instead.**

- **The denominator includes page 1.** `_exhaust` fetches it before `_paginate` runs and it is as
  much a page of `total` as the fanned-out ones. Counting only the fanned-out offsets reads a
  21-40 posting query — one page — as 100% lost the instant it 429s, which on a capped board is
  most of the fix undone: three of nvidia's fifteen `jobFamilyGroup` slices are that size
  (live-checked 2026-08-20).
- **0.5 is a judgement call, stated as one.** Nothing records per-page failure rates, so there is
  no distribution to cut at. Half is where the issue's own two ends land on the side they belong,
  and past it the postings in hand are the minority of what the query said existed. `_paginate`
  logs `{missing} of {page_count}` on both branches so the next reader has the numbers this one
  did not.
- **The first page still raises, and that is what keeps ADR-0058 reachable.** `_exhaust` fetches
  offset 0 with `raise_gone=True` at depth 0, untouched: a board whose first page 404s has no
  partial to keep and must earn its gone-verdict.
- **A subdivided slice that fails costs its own postings, not its siblings'.** Past the 2,000 cap
  every page after the first is fetched inside a slice, so without this the fix reaches no capped
  board at all. `_exhaust` catches a slice's `RequestsError`, records *why* through
  `mark_truncated`, and lets the remaining slices ship — the same trade its first-page-404 branch
  already made.
- **When the threshold fails a crawl, the origin's own exception is re-raised.** `harvest` records
  failures as `"{ExcType}: {message}"` and `board_failures._GONE` reads `HTTP Error (404|410)` out
  of that text; a fresh exception of ours would read as neither gone nor throttled. Verified live
  against `nvidia.wd5` (2026-08-20): `raise_for_status()` produces `HTTPError: HTTP Error 422: `.
  Only an all-404 majority has nothing to re-raise, and its synthesised `RuntimeError` is worded
  so it cannot match `_GONE` — a mid-crawl 404 is one page of a board whose first page served, and
  must not age it toward quarantine.

## Options considered

1. **Keep the raise, widen the fan-out's pacing instead.** Treats the 429s rather than the
   discarding. But the width is already unmeasured (`_PAGE_STREAMS`), pacing trades wall-clock in
   a run that is critical-path bound, and it leaves every *other* cause of a lost page — a 5xx, a
   connection reset — still costing the whole board.
2. **Absorb every failed page, no threshold.** Simplest, and what the 404 branch already did. Its
   failure mode is the one the issue names: a board that read two pages of twenty is marked
   truncated, which is a positive instruction to `index sync` to preserve rows nothing re-read.
3. **Threshold on retry-exhausted pages only, leaving 404 unconditional (rejected).** Narrower —
   it changes nothing about a path the issue shows has never fired. But it needs two counters and
   an arbitrary rule for a query that loses pages both ways, when the question `_paginate` is
   actually asking ("how much of this query did we read?") does not depend on the cause.
4. **Threshold over lost pages, whatever lost them (chosen).** One counter, one rule, cause-blind
   — matching how the issue frames the line ("a board that loses 90% of its pages"). The cost is
   a stricter 404 path than before, on a path measured to be dormant.

## Consequences

- A throttled page now costs its own ~20 postings instead of a board. The gone-quarantine still
  works, because the signal it reads has always been the *first* page.
- A capped board can now finish with some slices missing and say so; it lands in the
  unauthoritative set (ADR-0053) and is exempt from eviction that run, which is the correct
  reading — its absences are not delistings.
- **A 429-storm board goes quiet where it used to be loud.** It produces jobs and a truncation
  reason rather than a failure row, so the shard report's failure counts will drop for reasons
  that are not entirely good news. Read `truncated` alongside them.
- A mid-crawl **410** majority still re-raises as gone text and takes one strike (of five). It did
  before this change too, on a single page rather than a majority — narrowed, not introduced, and
  left alone rather than rewriting the status text the ledger depends on.
