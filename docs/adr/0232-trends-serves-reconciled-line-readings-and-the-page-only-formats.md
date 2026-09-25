# ADR-0232: Trends serves reconciled line readings, and the page only formats

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (company trends
and its netting rules), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) (turnover),
[ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (one
history; netting moved out of the browser)

## Context

ADR-0230 moved netting into Python (`trend_netting`, inside `trend_history.answer`). The page still
builds every figure a reader compares out of the pieces it is sent: each line's `net`, `steps`,
`jumps` and `causes`. About fifteen functions do arithmetic on counts, among them `trendMove`,
`countingMove`, `causesOf`, `changeSizeExact`, `apportion`, `listLines` and `buildOtherSeries`. The
same quantity is therefore worked out in several places, and nothing checks that they agree.

The last live critic of company trends (round 16, 2026-09-25) scored the tab 6/10. Its findings
trace to that shape. Micron serves as the example:

* **The Marked changes don't sum to "Not hiring".** Micron's list sums to −2,447; its "Not hiring"
  reads −2,378. The causes are peeled in separate frames. A removal is lifted by its raw count,
  which includes 69 non-tech rows. Settling steps leak unless the page happens to filter them.
* **The table's caption says "the categories below add up to it".** Rows miss the first row by
  3–18 openings, and by far more at Wipro (+156 against +444). Netting is not additive across
  lines, and the page's "Other" re-sums lines in its own way and rounds each row alone.
* **One removal has three sizes.** It is shown as "1,927 postings removed" and "−1,974 openings",
  and its size changes with the window (−1,974 over All, −1,896 over 7 days).
* **Share is netted a second time.** `net.share` applies an openings-sized lift to percentages,
  and duplicate scaling is skipped there.

## Decision

1. **A new module, `headstart/trend_reading.py`, answers with readings, not pieces.** Today's
   `trend_netting` becomes its private implementation. Its interface:
   * `read_trends(history, question) -> TrendReading` returns every figure the Trends tab shows.
     Each line carries its start, latest, hiring move and "Not hiring", split into named causes.
     The window also carries its Marked changes and day markers. Every count is a whole number,
     rounded once, so every list and every row sums exactly.
   * `read_company_moves(history, window, keys)` returns Hot's figures. A Hot row's move is, by
     construction, the move of the trend its "See trend" link opens.
   * `TrendReading` serialises to the JSON the Space serves.
2. **Invariants are equalities, checked on the reading itself:**
   * a line's latest − start = its hiring + the sum of its "Not hiring" causes;
   * a line's "Not hiring" = the sum of its Marked changes;
   * a breakdown's rows add up to its first row (with the closing row of decision 4);
   * a change's size is the same in every window that holds it;
   * share = netted count ÷ netted denominator, and is never netted on its own.

   One checker states them. Both pytest and the page's node tests run it over the same golden
   readings (`tests/fixtures/trend_readings/`), so neither side can drift from the other.
3. **A duplicate removal has one size, and the growth it had doubled is a cause of its own.** The
   removal is sized in tech openings at its own run, the same in every window. Removals keep
   scaling the history before them (ADR-0185: every job and every hire was counted twice). The
   part of the window's growth that scaling takes out is listed separately under "Not hiring",
   as growth counted twice before the removal. The raw postings count, non-tech rows included,
   no longer reaches the reader.
4. **A company's line is netted as it is today, and its category rows keep their own netted
   figures.** Where the rows do not reach the company, one explicit closing row, "moved between
   categories by a counting change: ±N", makes the table add up and says why.
5. **A category takes its company's duplicate removal by the company's ratio,** as a
   single-company view already does, and such a row is marked as estimated. The history does not
   record a removed row's family. Recording it on each eviction is the source fix.
6. **A reading that fails its checks is still served,** with a note that its figures do not fully
   reconcile, and the Space logs it. The same failure fails CI.
7. **The page formats and draws.** Its arithmetic on counts is deleted, and the payload fields it
   no longer reads leave the wire.

## Alternatives considered

Four interfaces were drafted in parallel (the `codebase-design` skill, "design it twice"):

* **A minimal pair of entry points.** This is the chosen interface.
* **A general event model.** Every cause becomes an event with one of four effects, lines form
  trees, and each surface is a projection. It is extensible, but every extension point would have
  one user today, and callers would have to learn the model.
* **A reading shaped for the page's common case,** with its rows rounded together with its causes.
  That rounding is kept.
* **The JSON payload as a versioned contract,** with one checker in both languages. That checker
  is kept.

All four proposed making a company's line the exact sum of its category lines. The owner chose
that first, then reversed it after a measurement on 3,108 companies (local state, current with HF
at the 2026-09-25 05:15 tick). Summing would have moved 243 companies by 5 or more openings and 5
by 100 or more, and it would have flipped the direction of 16 (AgileEngine from +80 to −188, Wipro
from +444 to +156).

The cause is always the same. A counting change moves more of a company's jobs out of a category
than that category held early in the window: on Sep 24 the classifier moved 1,704 of Wipro's jobs
out of Software Engineering, which had held 1,524 when the window began. Shifting that category's
history would push it below zero, so the erase guard scales it instead, and scaling shrinks its
earlier growth. The company line is barely moved by a reassignment between its own categories, so
its figure is the sound one to headline, and to rank Hot by.

## Consequences

* **Every figure the reader compares has one owner and one size.** The list, "Not hiring", the
  table and Hot cannot disagree without failing a check.
* **The page loses about twenty functions.** The golden answers of ADR-0230 become golden
  readings.
* **The table can show one more row,** the closing row, whenever a counting change reassigned
  more jobs than a category could absorb.
* **Out of this ADR's scope, fixed separately:**
  * Comparable coverage serves no removals (an input fix in `trend_history`, shipped with
    step 2);
  * Hot states "0 opened · 0 closed" while turnover is not counted;
  * a range preset replaces the browser history entry.
* **Steps, one PR each, through the `code-review` skill:**
  1. this ADR;
  2. `trend_reading`, the checker and the golden readings, with the Space serving the reading
     beside the current fields;
  3. the page reads the reading, and its arithmetic is deleted;
  4. Hot reads `read_company_moves`.
