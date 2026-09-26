# ADR-0238: A mostly re-counted category gives no percentage, and Hot hides only staffing firms and job boards

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (what a line
reading withholds), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md)
(what the Hot tab hides)

## Context

The round-17 live critic of company trends (7.5/10) found two things the owner decided on.

* **A percentage read off a start the steps mostly took away.** NVIDIA's Embedded & Firmware
  started the window at 2,706 openings. Duplicate removal took about 1,422 of them and counting
  changes 1,188 of the 1,284 left, so its netted start was 96, and it tiled "Biggest faller
  −14.6%". With Micron's beside it, 6,474 netted to 97, drew an index of 121 → 74 → 125 and
  tiled "Biggest riser +25.8%". Under Share, Micron's line read "+1829.1%", because the Share
  floor was tested against the raw start. The index floor (a netted start of 5 openings) let all
  of these through.
* **Hot hid Wipro.** The list kept only `operator === "employer"`, and `services` lumped IT
  services employers (Wipro, Infosys, TCS) with staffing agencies. Meanwhile Mindlance, Deegit,
  Sonoma Consulting, US IT Solutions and E*Pro, each a staffing agency, were on no list and
  showed in Growing's top 20.

## Decision

1. **A category, level or role line is mostly re-counted when its counting changes took openings
   out, and either took out more than half of its start once its other steps are counted, or,
   for a line that started with 20 openings or more, left under 5.** Its counting changes are its causes of kind `counting` or
   `growth_scaled_by_a_change`. Its other steps are duplicates removed, Boards found and a pick
   joining, so its start once they are counted is its netted start less its counting changes.
   More than half of that is the changes taking out more than was left: a start of 100, a found
   Board of +500 and changes of −400 leave 200 of 600, and the line is mostly re-counted though
   200 is above its raw start. A duplicate removal alone never makes a line one: it corrects the
   count by a known size and estimates nothing. A line that started under 20 with little left
   keeps its plain "too few openings" reason (the owner's call on #731): "2 → 1 after −1" is not
   mostly re-counted, and the under-5 clause alone labelled 2,284 such lines. The rule is
   `line_reading._mostly_recounted`.
2. **A company's own line is never mostly re-counted, and keeps its percentage** (the owner's
   call on #731). That covers every company headline, a company under the Company breakdown,
   the first row with one or several picks, and Hot's rows; the reading marks each such line
   `whole_company`. The company line is the one the netting keeps sound: a counting change
   mostly moves jobs between a company's categories, so it barely moves the company's total
   (ADR-0233's measurement on 3,108 companies), and Hot ranks by that total. A category has no
   such anchor: its netted start is what the erase guard and the shifts left of its history. A
   whole company's line still gives no percentage off a netted start under 5. Nor is the
   breakdown's closing row ever one: it has no start.
3. **A mostly re-counted line gives no percentage in any unit, no index line and no tile, and
   says so first.** Its `percent_withheld` is `"mostly_recounted"` even where a window under 3
   days or a start under 20 openings would also withhold the percentage, so the page always adds
   "mostly re-counted in this window" beside its hiring in openings. Its share's change and its
   `index_base` are None.
4. **A share's change is withheld wherever the line's own percentage is.** It no longer has
   floors of its own: the critic expected Share to withhold wherever Change does.
5. **The Operator has a fourth value, `staffing`, and Hot hides `staffing` and `aggregator`
   only.** `board_operator.SERVICES` keeps IT services, consultancies and BPO firms, which
   employ the people they post for. It shows those rows, labelled "IT services". `STAFFING`
   takes the staffing and placement firms and the talent marketplaces that were in `SERVICES`,
   and each firm adjudicated from Hot's top 50 on 2026-09-26 by a sample of its own live
   postings. A company with a staffing Board and a services Board is `staffing`. The "hidden"
   note names each kind with its count and its first companies.
6. **The Space decides each company's Operator as it loads the directory.** What Hot hides must
   not depend on when the directory was last written: the file then on HF had no `staffing`,
   so Randstad, Collabera and Sonsoft would have shown as IT services until the next run.
   `board_operator` moves from `ingest/` to `boards/` (ADR-0232: the Space never imports
   `ingest`), and `trend_history`'s directory reader applies its `company_operator` to every
   entry. The `company_directory` stage still writes the same label.
7. **A list entry is a form no other company carries.** Every entry was checked against the
   directory and the liveness ledgers on 2026-09-26. Single words another company's name can
   carry, with no form of their own to narrow to, are left off (Info-Ways, LinkTag, Raydar, AG
   Technologies, Quantix); `maarut` and `simera` are narrowed to `maarutinc` and
   `simeratalent`; and three collisions become exceptions (Turing Machines, Atos Medical, a
   Sutherland trade-union club).
8. **A closed count over only some of a company's Boards says so.** Where every Board had a run
   whose closures went uncounted, no closed count is given; where some did, Hot and the trend
   sentence give it as "3 closed (not counted on 1 of 2 boards)".

## Consequences

* Over All, on the 2026-09-26 state (3,008 companies with 20 or more openings), 7,411 of 45,547
  category lines are mostly re-counted, 8,024 over 7 days, and no company line is. Micron's
  headline keeps its percentage (3,933 → 931 netted, +9.1%); its Embedded & Firmware does not.
  No reading fails the checker.
* `check_reading` and the page's `checkReading` state rules 1 to 3 as their invariant 7.
* A new tag in `board_operator` reaches Hot at the Space's next boot, not the next run.
