# ADR-0185: Trends narrow to companies picked from a directory of Boards

**Status:** accepted · **Date:** 2026-09-24 · **Extends:** [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md), [ADR-0075](0075-ats-becomes-a-trends-ledger-dimension.md), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md)

## Context

A user wants to type company names into the Trends tab, one or several, and see what those
companies are hiring for and how that is changing.

No new collection is needed. ADR-0143's Board-delta ledger already holds every Board's counts
by `(metric, family, band, ats)` on every run, and the Space already loads it and replays it for
a chosen set of Boards (`_comparable_rows`). A company filter is that replay over a different
set. What is missing is the join from what a person types, a company name, to what the ledger
is keyed by, a **board_key**.

Measured 2026-09-24 against the ledgers on HF (36,657 Boards in the snapshot, 285 delta ticks,
712,549 delta rows, 4.4 MB) and the served table as of 2026-09-23 (514,163 rows):

- **Names are partial.** 60.3% of Boards, carrying 57.1% of tech openings, have a real
  company name. The rest show a tidied slug: Southwest Airlines is "Swa" without an alias.
- **A Board is not a company.** Workday splits one tenant into sites, Taleo Enterprise into
  career sections, Taleo Business Edition into `cws` sites. Going the other way, 119 Boards
  carry several legal entities (Autoliv's Teamtailor Board has 16).
- **Names collide.** `trakstar:amazon` is one Salesforce-admin posting, and
  `workday:google/GOCJobs` is Google Operations Center.
- **History is short.** The delta ledger began 2026-09-13, and the aggregate ledger before it
  has no Board dimension, so a company's history can never start earlier.
- **Most companies are small.** The median Board has 3 tech openings; 6,016 have ten or more
  and 531 have a hundred or more.

## Decision

**Companies are picked, never matched.** The user types into a search box and picks each
company from its suggestions, so a typed string is never resolved to a company without a
person confirming it. Suggestions match loosely because the user checks each one: the name
normalized (case, accents, punctuation, legal suffixes), ranked exact, then name prefix, then
word prefix, then one typo on words of five or more letters. Each suggestion shows its openings
and Board count, so "Amazon · 9,214" is never mistaken for a one-posting slug collision.
ADR-0171's ban on prefix matching governs unattended labelling, where nobody checks the result.
It does not apply here.

**Picked companies scope the chart, and can be compared.** By default the lines stay role
families, summed over the picked companies' Boards. With two or more picked, "Break down by"
gains a Company option that draws one line per company.

**Entry points are where people already meet companies.** The picker leads the Trends filter
row. Hot-tab rows and search-result company names link into Trends already filtered, and the
picker offers the Account's followed companies in one click.

**A pipeline stage writes the directory** (`ingest/company_directory`, after `role_trends`),
naming every Board with tech openings and grouping them into companies:
`data/state/company_directory.json`. It runs in the pipeline because the naming rules live in
`hot_boards` and the Space never imports from `ingest`. It carries **names and no counts**: the
Space already derives each Board's openings from the delta ledger, and a file without counts or
a timestamp is byte-identical run to run, so republishing it with `data/state` costs no storage.

**Two Boards are one company only when they are one tenant or share a curated alias.** Tenant is
structural: a Workday tenant's sites (684 tenants), a Taleo Enterprise host's career sections
(36 hosts), a Taleo Business Edition `org`'s `cws` sites (12 orgs), and casing duplicates
(`smartrecruiters:AbhiBus` / `abhibus`, 54 pairs). The only cross-ATS identity is
`hot_boards.DISPLAY_ALIASES`, such as Lockheed Martin on Eightfold and SuccessFactors.

**A matching name never merges, stated or not.** The first draft merged cased names across ATSes
and any names within one ATS. Read against the data, that made one "Pearl" of four ATSes'
Boards, one "Arlo" of a New York startup and Netgear's spin-off, and one "Clarity" of two
unrelated Ashby tenants. The cost of refusing is that a real company on two ATSes (Schonfeld on
Greenhouse and SmartRecruiters) appears twice and the user picks both. That duplication is
visible to the user. A wrong merge would add another employer's openings to their chart without
telling them.

**The shared naming helper was fixed where the directory exposed it.** Naming every Board
rather than a Hot-tab head surfaced 245 misnamed Boards: Taleo Enterprise's URL slugs tidied to
"Https:" (81), SuccessFactors rows whose company is their host's first label ("Www", "Apply",
"Join"), and Workday site names in the company column ("EXTERNAL_CAREERS" for Boeing,
"CorporateCareers" for Mastercard). A site segment is now rejected as a name only when it is
worded like a site, with a board word such as "Careers" or "External". A bare site like
`jiostar/JioStar` is often the best name available.

## Rejected alternatives

- **Auto-resolving typed names.** Every measured collision above would reach a chart
  unannounced.
- **Building the directory in the Space at boot.** No pipeline change, but it duplicates the
  naming rules outside `ingest` and spends boot time on every restart.
- **Extending `hot_boards` to write it.** No new stage, but the module would then do more than
  its name says.
- **Merging on a stated name across ATSes.** This was the first draft. It is refuted by the
  collisions above.

## Consequences

- **Company totals overcount multi-Board tenants until the index deduplicates across Boards.**
  Taleo Enterprise career sections each serve the tenant's whole requisition set, so the served
  table held 22,929 Taleo Enterprise rows for 2,475 distinct (tenant, requisition) pairs (HDR:
  8,235 for 549). Workday sites overlap too, with 7,146 of 104,849 rows duplicated. The delta
  ledger holds counts, not ids, so the Space cannot remove the copies at read time. The
  directory records membership only. Adding its Boards together is correct only after the index
  holds one row per requisition, which is being assessed separately and is not scheduled.
- A company's series begins at its Boards' first delta, on or after 2026-09-13, and the tab
  must say so rather than let a ten-day line read as a whole history.
- Boards that lose their last tech opening drop out of the directory, so a company that has
  stopped hiring cannot be picked. That is acceptable for a first version and worth revisiting.
- On the 2026-09-24 snapshot the directory holds 31,314 companies over 32,829 Boards, 787 of
  them owning more than one Board, in 1,986,180 bytes. Two consecutive builds were
  byte-identical.
