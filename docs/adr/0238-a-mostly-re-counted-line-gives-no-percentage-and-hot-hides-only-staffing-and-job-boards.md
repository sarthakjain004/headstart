# ADR-0238: A mostly re-counted line gives no percentage, and Hot hides only staffing firms and job boards

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

1. **A line is mostly re-counted when the steps took openings out of it and either its counting
   changes took out more than was left, or under 5 openings were left.** Its counting changes
   are its causes of kind `counting` or `growth_scaled_by_a_change`. Taking out more than was
   left is the same as taking out more than half of the start once the line's other steps
   (duplicates removed, Boards found, a pick joining) are counted. A duplicate removal alone
   never makes a line one: it corrects the count by a known size and estimates nothing. The
   rule lives in `line_reading._mostly_recounted`.
2. **Such a line gives no percentage in any unit, no index line and no tile.** Its
   `percent_withheld` is `"mostly_recounted"`, its share's change is None, and its
   `index_base` is None. It still gives its hiring in openings, and the page adds "mostly
   re-counted in this window". A line that started under 20 openings keeps "under 20" as its
   reason: 8,097 of 45,547 category lines are that small and kept under 5 of it.
3. **A share's change is withheld wherever the line's own percentage is.** It no longer has
   floors of its own.
4. **The Operator has a fourth value, `staffing`, and Hot hides `staffing` and `aggregator`
   only.** `board_operator.SERVICES` keeps IT services, consultancies and BPO firms, which
   employ the people they post for. It shows those rows, labelled "IT services". `STAFFING`
   takes the staffing and placement firms and the talent marketplaces that were in `SERVICES`,
   and each firm adjudicated from Hot's top 50 on 2026-09-26 by a sample of its own live
   postings. A company with a staffing Board and a services Board is `staffing`. The "hidden"
   note names each kind with its count and its first companies.

## Consequences

* Over All, on the 2026-09-26 state (3,008 companies with 20 or more openings), 662 category
  lines of 20 or more openings and 32 whole-company lines are mostly re-counted. Micron
  (3,933 → 931 netted, counting changes −1,221), HPE and Boeing are among the companies whose
  headline now gives its hiring in openings and no percentage. No reading fails the checker.
* `check_reading` and the page's `checkReading` state rule 1 as their invariant 7.
* **A new staffing tag reaches Hot only when the next pipeline run rewrites
  `company_directory.json`.** The Space reads each company's Operator from that file and never
  imports `ingest`. Until the run, a staffing firm the old file labels `services` shows on Hot,
  labelled "IT services", and a newly tagged one shows as an employer.
