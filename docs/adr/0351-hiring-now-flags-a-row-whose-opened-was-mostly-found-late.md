# ADR-0351: Hiring now flags a row whose opened was mostly found late

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(what Opened counts),
[ADR-0321](0321-an-agent-reads-hiring-now-by-opened-less-closed-and-every-trend-says-its-turnover-span.md)
(flagged `hiring_now` rows go last),
[ADR-0330](0330-trends-are-recomputed-from-recorded-job-facts-whenever-a-rule-changes.md)
(the restated Trends, where Opened can be redefined),
[ADR-0335](0335-an-agent-leaves-out-staffing-firms-and-job-boards-and-an-unchecked-agency-name-is-flagged.md)
(the flag every Lens already carries)

## Context

The round-4 critique of the MCP server (2026-09-29, 7.9/10) found gap **P1-1**: `hiring_now`
ranked Starbucks #3 on its default Lens, "opened 50 · closed 0", but only 12 of those postings
had been posted in the last 7 days. The rest were posted from 2026-05-05 to 2026-09-21 and first
seen on 2026-09-29 (`p5_02`, `p5_04`, `p5_05`). Nothing said so.

**Opened** (ADR-0227) is an id new to a tick's snapshot, first seen after the previous tick, on a
Board that tick already counted. A Found Board's backlog is Recounted, but a posting found late
on a Board already counted is not. That happens when a Board is read again after a gap, when
postings are listed again under new ids, or when a scraper or filter change lets old postings in.

### Measured (2026-09-29)

The history was 407 Board-delta ticks from HF. The served table was version 326, 499,841 rows,
read straight off HF. The Space's ranking was rebuilt at five ticks, 12 hours apart. For each
ranked company, its served postings first seen since turnover began (2026-09-25 18:16) were
split by the gap between their posted date and their first sight.

- **The gap is bimodal, so 14 days separates found late from newly posted.** Take the postings
  first seen since 2026-09-01 on a Board already served 3 days before:

  | ATS | within 2 days | within 14 | over 30 |
  |---|---|---|---|
  | Workday (n=52,875) | 74% | 81% | 13% |
  | SuccessFactors (n=18,586) | 74% | 90% | 3% |
  | Greenhouse (n=17,448) | 65% | 69% | 28% |
  | Eightfold (n=4,206) | 81% | 87% | 9% |

  Little mass lies between 2 and 14 days. So a posting more than 14 days old at first sight
  was found late, not read slowly.
- **Share of the top 20 opened that could be newly posted.** Of each Lens's top 20, with the
  hidden Operators out, this is the share whose served postings were posted within 14 days of
  first sight (undated counted as fresh):

  | tick | Opened less closed | Expansion | Volume | Rate |
  |---|---|---|---|---|
  | 09-29 16:13 | ≤92% | ≤96% | ≤81% | ≤92% |
  | 09-29 04:04 | ≤91% | ≤92% | ≤77% | ≤88% |
  | 09-28 15:10 | ≤89% | ≤93% | ≤68% | ≤81% |
  | 09-28 03:36 | ≤95% | ≤97% | ≤65% | ≤96% |
  | 09-27 15:43 | ≤96% | ≤98% | ≤67% | ≤93% |

  Volume runs lowest because New York Life's 575 opened left no served posting at all. Those are
  re-listed ids, and its closures-uncounted flag already disowns it.
- **Found late concentrates in a few rows.** Under the rule below, the Lens top 20s were flagged:
  - 09-29 16:13: Starbucks (22 fresh, 28 found late, 50 opened) on three Lenses; Box (3, 8, 11)
    on the default Lens; Third-Party Job Posts (2, 8, 10) on Rate.
  - 09-29 04:04: Starbucks, and Box.
  - 09-28 15:10: Meta (31, 131, 63).
  - The two earlier ticks: none.

  Across every Lens row with 10 or more opened, 7 of 161 were flagged at the newest tick, and
  0 to 4 at the others.
- **The dates are the employers' own.** Box's 11 postings first seen in the window were checked
  against Greenhouse's live board. Each posted date HeadStart held matched `first_published`. The
  8 found late were first published on 2026-07-14, 2026-08-19 (6) and 2026-08-21. Greenhouse had
  updated each of them on 2026-09-28, the day HeadStart first saw it.
- **How often a real spike would be flagged.** A flag could be wrong only when postings opened
  in the window and closed before the read would, if all fresh, lift the fresh count to half of
  opened. Across every Lens row at each tick, that bound was 1 of 7 flagged at the newest tick
  and 0 of 4, 3 and 1 at the next three. The one was Microland: 26 opened, 23 of them still
  served, 10 fresh and 13 found late. Had its 3 postings no longer served been fresh, half would
  have been fresh. On the Lens top 20s the bound was 0 at every tick.

## Options

1. **Redefine Opened.** Count a posting as opened only when its posted date is within 14 days
   of first sight, and book the rest as a new "found" step, in `job_turnover` and the history.
   This is the clean fix, and it changes every Trends and Hiring now number the site shows. But
   the history keeps only per-Board counts, not ids or posted dates, so past ticks cannot be
   rebooked. The restatement ADR-0330 builds (draft PR #934, step 5: "a found Board's backlog is
   placed at each Job's `posted_at` on the ATSes where that date is reliable") is where Opened
   can be redefined over the whole history at once.
2. **Flag it now, from the served table.** Give each `/hot` row the fresh and found-late
   counts of its served postings first seen since turnover began. `hiring_now` flags a row
   where most of its opened was found late. **Chosen.**
3. **Flag on found late alone.** A row is flagged if most of its served first-seen postings were
   posted long before. This double-counts: rows first seen in a counting-change run are left out
   of Opened, but not out of these counts. Accenture Federal Services had 234 of 253 served
   postings found late, but only 38 opened, and 19 of its served postings were fresh. It would
   be flagged on postings its opened never counted.

## Decision

- **`/hot` rows carry `opened_fresh` and `opened_found_late`.** The Space reads its served
  postings first seen after the window's `turnover_from` (three columns; 91,772 rows were first
  seen after 2026-09-25 began). `hot_ranking.rank` counts each company's postings: posted within
  `FOUND_LATE_DAYS` (14) of first sight or undated, against posted longer before. Each id is
  matched to its Board among the directory's keys. Both counts are None where the postings went
  unread or the company's turnover was not counted. `counts.found_late_days` carries the 14.
  The agent contract rises to 19.
- **`hiring_now` flags "opened mostly found late, not newly posted" on every Lens.** A row is
  flagged when all three hold:
  - it has 10 or more opened;
  - its fresh postings are fewer than half its opened;
  - its found-late postings are at least half its opened.

  The last two are both needed because the counts are of postings still served. The fresh test
  alone would flag New York Life, whose opened left nothing served. The found-late test alone
  would flag Accenture Federal Services, whose found-late postings mostly never reached opened. A flagged
  row goes after the unflagged ones before the cut, as every flag does (ADR-0321). It states its
  two counts.
- **The eval's `hot_top` re-derives the flag** from `/hot`'s own fields, in `_disowned`, on every
  Lens. It fails an answer that names a listed found-late row without saying it was found late,
  posted earlier, backfilled or listed again.

### Before and after, default Lens, 2026-09-29 16:13

Before: Wipro, Starbucks, Accenture Federal Services, AMD, LTM, and so on, with Box among the top
20. After: Starbucks and Box are listed after every unflagged row, each saying, for example, "FLAG
opened mostly found late, not newly posted: of its postings first seen since turnover began, 28
were posted more than 14 days before HeadStart saw them and 22 since". Opened, closed and every
other figure are unchanged, on the page and in the tool.

## Consequences

- **The site's Hiring now tab does not show the flag.** Its figures and order are unchanged, as
  with `operator_unverified` (ADR-0335). The page reads `/hot`, so it can show the two counts
  later without a new route.
- **The follow-up is to redefine Opened by posted date inside the restatement (ADR-0330 step 5,
  PR #934).** There, a posting first seen more than 14 days after its posted date, on an ATS
  whose date is reliable, is booked as found rather than opened. Trends and Hiring now then read
  the same redefined figure. When that lands, this flag can be retired, and a future ADR amending
  this one records the before and after of the site's numbers.
- **Not caught: postings re-posted with fresh dates.** AgileEngine's 529 postings first seen in the
  window were 486 fresh and 43 found late. They are 61 titles, one per city ("Data Engineer
  ID89384" 85 times). Those are duplicates by requisition, not found late, and its expansion row
  is already flagged as re-counting.
- **The counts are of postings still served.** A posting opened and closed inside the window is
  in neither count, so a flag can err toward "found late" only through closed fresh postings.
  That bound was measured above: 0 on the Lens top 20s.
