# ADR-0270: The index view takes a counting change out too

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0221](0221-a-refit-is-a-step-in-one-trends-history.md) (its consequence "the index view
(no pick) shows the step too … its figures are not netted, as before") · **Relates to:**
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (counting changes),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (netting under a
pick), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(turnover), [ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md)
(line readings), [ADR-0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md)
(mostly re-counted), [ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md)
(the Dormant-Board rule), [ADR-0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md)
(the MCP server reads hiring as turnover, and sized this change's premise) · **Issue:** #833

## Context

With no company picked, no **Counting change** was taken out of any Trends line. `netting.py`
said so ("With no pick nothing is taken out"), following ADR-0221's consequence, which gives no
reason beyond "as before". Yet the reading, the page's table ("Hiring %", "Hiring") and the MCP
server's `read_trends` all call a line's raw change "hiring".

Reproduced on the HF trends state for a 7-day window ending 2026-09-28 21:40Z:

- **Software Engineering** read 105,785 → 69,359, "hiring" −36,426 (−34.4%), with no "Not
  hiring", while 2,802 of its jobs opened and 3,045 closed (turnover is counted from Sep 25
  18:16). Three counting changes made the drop: −20,830 on Sep 24 21:19 (duplicate removal and
  the family classifier), −9,387 on Sep 25 17:07 (the category list), and −10,449 on Sep 28
  10:25.
- **Embedded & Firmware** read −18,295 over 7 days and −11,620 over 21, against 147 opened and
  161 closed since Sep 25 18:16. Its −16,082 on Sep 25 17:07 is the category list change, which
  split Hardware & Silicon out of it.
- **Every Marked change** had `sizes: {}`, so no list said how big a change was.
- **Hardware & Silicon** withheld its percentage for "a window under 3 days" on a 21-day window:
  its line began at Sep 25 17:07, so its own span was under 3 days, and MCP printed that reason
  verbatim.
- **The Sep 28 drop was named after the wrong change.** Sep 28 08:26 raised the tech filter to
  version 6 (ADR-0250's Dormant Boards), and those Boards' rows were evicted on the next run, as
  ADR-0250 says. That run, 10:25, dropped the tech total by 41,524 (the served total by 45,664).
  Its own methodology change was an extraction change, "read experience and salary from job
  posts more accurately", so the marker at the drop named a change that moves no category's
  total.

## Decision

1. **With no pick a counting change is taken out exactly as under a pick**, by the same code
   (`netting._notes`): a line-moving change with its run and the run after it; an extraction
   change on a Level breakdown; the switch of New to the Opened inflow; and under New a
   tech-filter change's week-later echo. The size is in openings, adjusted backwards as a pick's
   is, with the erase guard and the closing row as they are.
2. **A duplicate-removal change alone is taken out too.** Under a pick it is taken out only where
   the pick holds a Board it can move; the index holds every such Board. The index's level cannot
   tell those Boards' step from the rest of that run, so it takes the whole run out. Its opened and
   closed stay the sum of every company's (ADR-0227), so they still count the other Boards on that
   run.
3. **Every other change is still marked, sized on nothing.** An extraction change on a category
   view is one. The settling run of a change belongs to that change, so the Sep 28 10:25 drop is
   now the 08:26 filter change's (−41,286 on All tech roles), and the 10:25 marker has no size.
4. **Marked changes are sized on the first row**, named as the page names it: "All tech roles",
   or "All of <category>" on a Level breakdown. On tracked roles each role line is sized, as under
   a pick. The checker's invariant 2 (a company line's Not hiring is its Marked changes) now holds
   on the index too, and invariant 6 ("with no pick nothing is taken out") is retired, in
   `check_reading` and the page's `checkReading` alike.
5. **The index's categories are a breakdown, as a company's are.** They start where the first row
   does. A category first counted inside the window starts at 0, and what it arrived with is the
   counting change that sorted it in, or hiring. Where the categories do not reach the first row,
   the closing row says so.
6. **A line first counted after its view's first row began says so.** Where its percentage is
   withheld for too few days or for no start, the reason is "first counted on Sep 25", not "a
   window under 3 days" or "under 20 openings at the start". The page reads an index category
   first seen inside the window as "new since Sep 25" or "sorted in by a counting change, Sep 25",
   as it already did under a pick, and it now says under the index chart that lines and
   percentages skip the marked jumps. The MCP server no longer prints the reason: since ADR-0272
   it says a short line's own span ("counted for its last 3.2 of the window's 21.0 days").
7. **Found Boards and duplicate removals stay in the index's lines.** Both are sized per company
   (`discovered` and `evicted` exist only for picks). Over the 7-day window, 9,253 Boards were
   first counted, with 68,535 openings. The MCP server's `read_trends` (ADR-0272) leads with
   turnover and names found Boards and duplicate removals among the change it could not size. Its
   wording does not change: its unsized rest shrinks by the counting changes this ADR sizes, as
   ADR-0272's consequences expected.

## Measured

On the HF trends state (newest tick 2026-09-28 21:46), windows ending 2026-09-28 21:40Z:

| Line | Window | Before: hiring | After: hiring | After: not hiring |
|---|---|---|---|---|
| Software Engineering | 7 days | −36,426 (−34.4%) | +3,551 (+5.4%) | −39,977 |
| Software Engineering | 21 days | −11,344 (−14.1%) | +25,933 (+59.7%) | −37,277 |
| Embedded & Firmware | 7 days | −18,295 (−78.4%) | +596, mostly re-counted | −18,891 |
| Embedded & Firmware | 21 days | −11,620 (−69.8%) | +3,860, mostly re-counted | −15,480 |
| Hardware & Silicon | 7 and 21 days | +1,055 (+8.5%) from 12,349 | 0 → 13,404: +1,318, first counted on Sep 25 | +12,086 |
| All tech roles | 7 days | −12,263 (−3.1%) | +42,612 (+12.7%) | −54,875 |
| All tech roles | 21 days | +104,497 (+38.3%) | +127,190 (+50.9%) | −22,693 |
| All tech roles, comparable coverage | 7 days | −71,389 (−19.8%) | −6,517 (−2.2%) | −64,872 |

Comparable coverage holds one set of Boards, so it has no found Board. It shows what taking the
counting changes out does on its own. Under All coverage, what is left of the first row's rise is
mostly found Boards (decision 7).

Across 160 index views (both Measures, comparable coverage, a Workday-only view, every category's
levels and three tracked-role drills, each over 1, 3, 7 and 21 days and All), no reading fails
the checker. 125 lines are now mostly re-counted. Hot's payload, 300 rows over its trailing week,
is identical before and after. Over 7 days, the 150 largest companies' readings changed in one
field only. 752 category rows moved from "under 20 openings at the start" to "first counted on
…", and 32 lines from "a window under 3 days". Reading the 160 views took 20 s, up from 18 s.

## Alternatives considered

- **Keep the index unnetted and stop calling its change "hiring".** The page and the tiles would
  need a second word for one figure, and every reader would still read a refit as a hiring
  collapse.
- **Read the index's hiring as its turnover, opened less closed**, as the MCP server does since
  ADR-0272, which suggests the page adopt it too. Turnover begins only on 2026-09-25 18:16
  (ADR-0227), so on the page it cannot read a 7-day window yet, let alone a 21-day one, and it
  would differ from what every other line in the reading means by hiring. That is the owner's
  call, separate from this one.
- **Net found Boards on the index in the same change.** A found Board's backlog is known per Board
  in total, not per category. Netting it the way a pick's category is netted, by taking the whole
  run out, would take out almost every run, because Boards are first counted on nearly every run.
  Sizing it per category needs the delta ledger's arrival rows by family, which is a change of its
  own.

## Consequences

- Every index line's hiring moves wherever a window holds a counting change: 1,299 of the 1,310
  lines read across the 160 views.
- The index's first row is now served as its company line, and the index answer carries a
  breakdown, usually with no closing row.
- ADR-0221's consequence and `netting.py`'s "with no pick nothing is taken out" no longer hold.
  ADR-0227's "the index's lines keep a counting change's jump" does not either, but its turnover
  rule is unchanged.
- The all-tech first row still carries found Boards' backlogs under All coverage. Netting them
  needs a per-category size (Alternatives).
