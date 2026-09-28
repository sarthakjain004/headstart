# ADR-0255: Every tab speaks to a job seeker

**Status:** accepted · **Date:** 2026-09-28 · **Extends:**
[ADR-0248](0248-the-trends-tab-speaks-to-a-job-seeker.md) (plain words, from Trends to every tab)
· **Amends:** [ADR-0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md)
decision 8 (Hot's "not counted on N of M boards"), the words of
[ADR-0112](0112-the-door-earns-the-sign-in-before-it-asks.md)'s door · changes no figure, reading
field, API field or stored data

## Context

ADR-0248 took the pipeline's words off the Trends tab and left the rest: "The Hot tab still reads
its own closure fields and still says 'boards'." The owner asked for the same everywhere a job
seeker looks (issue #755, items 14 and 15): no boards, rows, runs, ticks, families,
reassignment, coverage, pipeline, ADR links or ATS names, and no dense sentences. Two Trends
pieces were also still dense: a company's sentence and the table view's headers.

Counted on 2026-09-28 in the rendered page (text, folded text and tooltips), before this change:
the Hiring now tab said "board" 6 to 17 times depending on the view ("3 closed (not counted on 1
of 2 boards)" on each row, and a footnote on boards, runs and the index); Search 3 to 4 and "ATS
provider"; Saved 4; Matches 3; the page footer and tagline 3; the sign-in page 6 "board",
"pipeline run", "ATS providers" and four ATS names. The Trends table's caption spoke of "the first
row" and "the closing row".

## Decision

**Words.** Outside Trends the page does not name a Board at all. It says what a reader already
knows: a company's own "career site" (as Home already did), "the employer's own site", or just
"companies HeadStart reads". Trends keeps its own label, "job site" (ADR-0248), and so does a
Search scope handed over from Trends with no name. "The index" becomes "HeadStart" ("no longer
listed on HeadStart"), a run becomes an update or a refresh, and the sign-in page's ATS names go.

**Hot's closed count is given bare.** "3 closed (not counted on 1 of 2 boards)" becomes "3
closed", as ADR-0248 did for the Trends sentences, and the note under the list says once that
closed counts can be low because some sites don't show a closure. A row whose closures went
wholly uncounted still says "closures not counted" rather than a zero.

**Short sentences.** A Trends company sentence is two sentences: the answer with its figures,
then what was opened and closed. "growing — 1,247 tech openings; up 4.8% over 15 days (+57
openings, about +27 a week) — about 50 opened since Sep 25; closures not counted" reads "growing —
1,247 tech openings, up 4.8% in 15 days (+57, about +27 a week). About 50 opened since Sep 25;
closures not counted." Every figure stays.

**Short headers.** The table view's columns are Now, Hiring % (Share change under Share),
Hiring, Not hiring, Opened, Closed, At start, Low and High. Each header's `title` says what it
counts. Its caption names the total's row by its label ("“All tech roles” is the company's total;
the categories below add up to it"), not as "the first row", and the closing row reads "Moved
between categories", with "by a counting change" in its `title`.

**Source, not ATS.** Search's "ATS provider" filter and Trends' "All ATS" menu are labelled
"Source" and "All sources". Both filters stay, and their options still name the systems: which
system to filter by is what a reader picks there, and whether to keep the filters at all is the
owner's open question from #759.

## Consequences

- `/hot` still sends `closures_uncounted_boards` and `boards_in_scope`, which the page no longer
  reads; left in place, since this changes copy, not data.
- The door's links to the design decisions and the Actions runs stay; only their words change
  ("and every refresh").
- New user-facing copy should pass the same test: a job seeker reading it needs no word from
  CONTEXT.md.

## Options not taken

- **Use "job site" on every tab.** Hot's "job boards" (aggregators it hides) sits beside it, and
  the two read as one thing. Most sentences outside Trends did not need a word for a Board at all.
- **Keep the Board fraction in a tooltip on each Hot row.** A tooltip is a hover a phone never
  shows, and the fraction is a caveat about counting, which one note under the list states for
  every row.
