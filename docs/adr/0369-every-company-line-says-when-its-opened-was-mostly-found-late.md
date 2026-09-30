# ADR-0369: Every company line says when its opened was mostly found late

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0351](0351-hiring-now-flags-a-row-whose-opened-was-mostly-found-late.md) (the split moves
out of the Hot ranking into one shared module) · **Relates to:**
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(what Opened counts),
[ADR-0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md) (agents report opened
and closed as hiring),
[ADR-0330](0330-trends-are-recomputed-from-recorded-job-facts-whenever-a-rule-changes.md)
(the restated Trends, where Opened can be redefined),
[ADR-0354](0354-an-eval-task-may-accept-but-never-require-the-path-its-tool-steers-away-from.md)
(verifiers judge truth, not a path)

## Context

The round-5 critique of the MCP server (2026-09-30, 8.1/10) found gap **R5-P1-1**. ADR-0351
flagged a company whose postings opened were mostly found late, but only in `hiring_now`. The
other readers of the same figure said nothing:

- `read_trends {"companies":["avature:deloitteus"],"days":7}` (critic call p4j): "Hiring, as
  postings opened and closed: 509 opened, 14 closed, net +495 … HeadStart sized none of it as
  re-counting."
- `hiring_now` over the same week (p4h): "FLAG opened mostly found late, not newly posted: … 410
  were posted more than 14 days before HeadStart saw them and 112 since". The Avature scraper had
  landed on 2026-09-26, and #989 then read Deloitte's Board whole, so this was a Board being read
  properly for the first time, not hiring.
- `company_profile` over three Deloitte keys (p4i): "Recent hiring … 612 postings opened and 172
  closed", with no caveat.

The server's instructions say to report postings opened and closed as hiring, so a journalist
asking "is Deloitte hiring?" was told +495.

The split lived inside `hot_ranking.rank`, counted from rows the Space read for the trailing
week only. Nothing else could reach it.

### Measured (2026-09-30)

The history was 1,041 ticks from HF, the newest 2026-09-30 12:40. The served table was version 95,
497,094 rows, read straight off HF (`id`, `first_seen`, `posted_at`). Each figure is over the
postings first seen after the window's first tick and turnover's first tick (2026-09-25 18:16).

- **Companies.** Deloitte US over 7 days: 509 opened, 14 closed; 112 fresh, 410 found late.
  Amgen (`radancy:careers.amgen.com`): 279 opened; 74 fresh, 303 found late. Deloitte South
  Asia: 94 opened; 89 fresh, 8 found late. The three-key Deloitte roll-up over 30 days: 613
  opened; 210 fresh, 418 found late. ADR-0351's rule flags the first two and the roll-up, and
  not South Asia.
- **The whole index.** Over 7 days it opened 32,946. Its served postings first seen in the same
  runs number 86,570 (49,274 fresh, 37,296 found late), 2.6 times opened. Of those, 48,649
  (17,322 fresh, 31,327 found late) are on Boards first counted after turnover began. Their
  backlogs are booked as Boards found, not opened. On Boards counted before turnover began there
  are 37,921: 31,952 fresh and 5,969 found late (16%).
- **Categories.** The naive split, which includes the found Boards, flags none of the 25
  categories. Every one has more fresh postings than half its opened: Software Engineering
  opened 5,911, with 6,663 fresh and 6,199 found late. On Boards counted before turnover began,
  the found-late share per category is 10% to 29%, with Product Management highest at 29% and
  Mobile at 24%. None comes near half.

So found late is concentrated in a few companies whose Boards were first read whole. It does not
reach the index or a category.

## Options

1. **Tool-side only.** `read_trends` calls `/hot` and copies a row's counts onto its company.
   This is cheap, but `/hot` holds only the ranked companies, over the trailing week, and not
   any window a user asks for. A company below 25 openings, or outside the week, gets nothing.
2. **One split, counted once at boot, and carried on `/trends` company lines.** **Chosen.** The
   Space reads the served postings first seen since turnover began, once, and counts them per
   Board and first-seen stamp. `/hot` and `/trends` both sum spans of it. `/trends` gives each
   company line's turnover the counts over exactly the runs its opened was summed over.
3. **Also split the index and every category line.** Per the measurement above, the served
   postings there are 2.6 times opened. The excess is the found Boards' backlogs, which Opened
   never counted. A split next to Opened would compare two different populations. Taking the
   found Boards out would mean rebuilding Opened from Job facts. That is ADR-0330's restatement
   (step 5 places a found Board's backlog at each Job's `posted_at`). #934 owns that redefinition.
   Not done.

## Decision

- **`headstart.trends.found_late` is the one home** of the rule and the counts:
  - `FOUND_LATE_DAYS` (14) and `MIN_OPENED` (10);
  - `posting_found_late`;
  - `mostly_found_late(opened, fresh, late)`, ADR-0351's rule unchanged;
  - `FirstSeenPostings`, cumulative counts per Board and first-seen stamp, so any span of any
    Boards is two bisections;
  - `attach`, which gives a `/trends` payload's company lines their split;
  - `clause` and `sentence`, the words every agent tool says it in.

  `hot_ranking` and `hiring_now` import it, so there is no second copy of the rule. The eval's
  `hot_top` still re-derives the rule on its own, taking only `MIN_OPENED` from here.
- **Boot counts once.** `_first_seen_postings` reads the served postings first seen since
  `TrendHistory.turnover_since`, a new property, and not since the trailing week's start. These
  are three short columns, 86,570 rows on 2026-09-30, and at most the whole table. The Hot
  ranking takes the result in place of the raw rows. If the read fails, both leave their figures
  out, as before.
- **`/trends` company lines carry `opened_fresh` and `opened_found_late`** on their turnover.
  This applies to the first row when companies are picked, and to each company's line under
  `split=company`. Each pick counts from the latest of three ticks: the window's first, the
  first with turnover, and its own first count. The count runs to the window's last tick.
  - They are not given under a category, an ATS filter, comparable coverage or measure new.
    There, Opened counts a part of the picks' Boards that the postings do not match.
  - They are not given on the index, per option 3.
  - The agent contract rises to 25.
- **The agent tools say it, by the same rule as the flag.** `read_trends`'s first row adds a
  sentence after the hiring line. Each company line under the company breakdown adds the clause.
  `company_profile`'s "Recent hiring" line adds the sentence:

  > Most of the 509 postings opened were found, not newly posted: of the postings HeadStart first
  > saw in these runs and still lists, 410 were posted over 14 days before HeadStart first saw
  > them and 112 within 14 days or with no date. So most of this opened is not hiring.

  The wording avoids "M of N opened". The counts are of postings still listed, and they can
  outnumber opened: Amgen has 303 found late on 279 opened, because a posting first seen in a
  run whose counting changed is listed but not opened. `read_trends`' description and its
  instructions line tell the agent to report that sentence rather than the net.
- **Eval task t43**, "How many tech people did Deloitte US hire this week?". It uses a new
  `found_late_share` verifier:
  - It reads `/trends` for the company itself and re-derives the rule from the first row's own
    fields.
  - It needs the answer to say the postings were found late and to name how many. That can be
    the count, or its share of opened or of the dated postings, within 3 points.
  - It never checks which tool was called (ADR-0354).
  - The task `requires` a `found_late_burst` (ADR-0366's mechanism). While today's data no
    longer shows opened mostly found late, the task is retired rather than run. If the burst
    goes between that check and the verdict, the verifier raises `NotJudged`, and the run is not
    judged. It is never failed.

## Consequences

- **What the site shows is unchanged.** The Trends tab ignores the new fields, as the Hiring
  now tab ignores `/hot`'s (ADR-0351).
- **A category-filtered company question gets no split.** An example is Deloitte US's Security.
  Its opened counts one category, and the postings are not read by category at boot. The
  company's own first row, asked without the category, carries it. Category lines under a
  picked company are covered by the first row's sentence.
- **How this fits ADR-0330.** This ADR does not redefine Opened. It discloses, beside every
  company's Opened, the share that the restatement's step 5 would move out of Opened. When #934
  serves restated Trends in which a posting found more than 14 days after its `posted_at` is
  booked as found, the flag and these sentences read near zero on their own and can be retired.
  An ADR amending this one records that, as ADR-0351 foresaw for its flag.
- **A Board found inside a window lands its backlog in the split but not in opened.** The
  fresh-half test is what keeps such a company from being flagged on postings its opened never
  counted, as with Accenture Federal Services in ADR-0351. The Deloitte case above is not this
  one: its 522 postings were first seen at 2026-09-26 20:07, after the Board's first count at
  18:30.
