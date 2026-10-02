# ADR-0248: The Trends tab speaks to a job seeker

**Status:** accepted · **Date:** 2026-09-28 · **Amends:**
[ADR-0119](0119-the-trends-chart-plots-change-not-level.md) (its reassignment caveat, kept
verbatim and open), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
§"What the page shows" (its turnover sentences) and
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (same-named
companies labelled by ATS) · **Relates to:**
[ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (reassignment),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (closures a scrape cannot see),
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the reading) · changes no
figure, reading field, API field or stored data · **Extended by:**
[ADR-0255](0255-every-tab-speaks-to-a-job-seeker.md) (every other tab, the company sentence and
the table's headers) · **Amended by:**
[ADR-0378](0378-trends-separates-first-counted-coverage-from-observed-activity.md)
(Sites tracked at start is the UI default; first-counted coverage and observed activity are separate)

## Context

The owner read the Trends tab as a job seeker would and found it too wordy and too technical
(issue #755, items 11 and 15). Three examples from the live page:

- The index sentence: "All tech roles: about −1,700 net from hiring — about 3,300 opened, 5,100
  closed (not counted on 3571 of 5747 boards) since Sep 25, runs where HeadStart changed how it
  counts left out."
- The caption under a Change chart: about 110 words on index bases, coverage, runs and "further
  rows in non-tech categories".
- "How to read this": 190 words on family reassignment ending in an ADR-0057 link, open by
  default. ADR-0119 had asked for it to stay verbatim and open.

Every clause in them was true and had been added by a review. Each one made the tab harder to
read for the person it is for. The owner asked that the tab never show ADR links or the
pipeline's words (boards counted, runs, rows, families, reassignment, coverage).

## Decision

**Words.** The page uses the reader's words: a Board is a "job site", a run is an "update", a
role family is a "category", and Comparable coverage is "Job sites: Tracked from start". The one
Python label a reader sees with a Board in it, a Marked change's "N more boards found", reads "N
more job sites found".

**No ATS names** (issue #755, item 14: a job seeker does not care which system hosts a company's
jobs). A company picker option reads "9,214 tech openings · 3 job sites", with no ATS list. The
directory keeps same-named employers apart (ADR-0185), and they used to be labelled by ATS
("Citi (workday)") or by key ("Citi (workday:citi/2)"). They are now labelled by their current
tech openings, "Citi (44 openings)", wherever two are picked together: the chips, the chart and the
sentences. (The picker itself offers one company per name.) Two with the same openings too are
numbered in key order, "Citi (0 openings, 1 of 2)". On 2026-09-28 the directory held 1,303 names
more than once, and 408 of them had two or more companies at the same openings (38 of those at
zero). A tie of that kind is often one employer listed twice, which nothing else the page has
tells apart. The count is the company's whole tech
total, so inside a category it is larger than the line beside it. The Source filter still lists
ATSes, as Search's "ATS provider" filter does: there the ATS is what the reader chose to filter by.

**The answer carries its figures, not their method.** The index sentence gives its net as opened
against closed, "about 650 more opened than closed — about 9,300 opened, 8,600 closed since Sep
25". It never says "more openings": the lines keep a counting change's jump, so a line up 400
would have read "about 10 more openings" beside it. It no longer names how many Boards' closures
went uncounted, nor that the runs of a counting change are left out.

**One caption, one folded note.** Under the chart there is one short caption for the unit on
screen: where Change lines start, and what the dashed line is. With no pick it adds that adding
companies lifts every line, which is ADR-0119's reason for the dashed line. "How to read this" is
folded by default. It says what Count, Share and Change mean, and states each caveat once, in one
plain sentence:

- closed can run low, because some sites do not show a closure and counting-change days are left
  out (this replaces the Board fraction and the "runs … left out" clause);
- grey lines mark jumps that are not hiring;
- a job can move to another category, so read a category over weeks (ADR-0057's caveat,
  shortened, still hidden where a line cannot show it).

The non-tech count ("123,933 further rows sit in non-tech categories") is dropped. It is the tech
filter's health figure, not something a job seeker reads a chart by.

## Consequences

- ADR-0119's "stays, verbatim" and "open" no longer hold for the reassignment caveat. Its meaning
  stays on the page; its words and its default state do not.
- The Space still sends `closures_unseen`, `boards_in_scope`, `turnover_left_out` and `non_tech`,
  which the Trends page no longer reads. They are left in place: this decision changes copy, not
  data. The Hot tab still reads its own closure fields and still says "boards".
- A future caveat should earn its place in the folded note first, and reach the caption or the
  answer only if a reader would misread the chart without it.

## Options not taken

- **Keep the caveats, only shorten them.** The Board fraction and the runs clause cannot be made
  short enough to sit inside the one sentence a reader came for.
- **Drop the caveats.** Closed counts can run low, and a category can lose jobs it never closed.
  A chart read without those two facts misleads.
