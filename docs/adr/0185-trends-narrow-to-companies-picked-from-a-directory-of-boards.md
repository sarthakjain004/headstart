# ADR-0185: Trends narrow to companies picked from a directory of Boards

**Status:** accepted · **Date:** 2026-09-24 · **Extends:** [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md), [ADR-0075](0075-ats-becomes-a-trends-ledger-dimension.md), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md)

## Context

A user wants to type company names into the Trends tab, one or several, and see what those
companies are hiring for and how that is changing.

No new collection is needed. ADR-0143's Board-delta ledger already holds every Board's counts
by `(metric, family, band, ats)` on every run, and the Space already loads it and replays it for
a chosen set of Boards (`_comparable_rows`, since generalised to `_replay_rows`). A company filter is that replay over a different
set. What is missing is the join from what a person types, a company name, to what the ledger
is keyed by, a **board_key**.

Measured 2026-09-24 against the ledgers on HF (36,657 Boards in the snapshot, 285 delta ticks,
712,549 delta rows, 4.4 MB) and the served table as of 2026-09-23 (514,163 rows):

- **Names are partial.** 60.3% of Boards, carrying 57.1% of tech openings, have a real
  company name. The rest show a tidied slug: Southwest Airlines is "Swa" without an alias.
- **A Board is not a company.** Workday splits one Tenant into sites, Taleo Enterprise into
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

**A pipeline stage writes the directory** (`ingest/company_directory`, after `role_trends`):
`data/state/company_directory.json`, `{"companies": [{"name", "boards"}]}`. It runs in the
pipeline because the naming rules live in `ingest` and the Space never imports from there.

- **It lists every Board the delta ledger has counted** with tech `stock` at the live centroid
  version, not only the Boards hiring now. A company's line is the sum of its Boards' lines, so
  a Board that closed its last opening still carries history. On 2026-09-24, 1,374 of the
  ledger's 34,203 Boards had no tech opening left, 79 of them under a company still hiring, and
  listing only current Boards would have dropped their history. It also means a company that
  has stopped hiring can still be picked. A closed Board has no rows left in the served table to
  name it (913 of the 1,374), so its name carries forward from the previous directory.
- **It carries names and Boards, no counts.** A Board's openings are derivable from the delta
  ledger the Space already loads, and its ATS is the board_key's prefix, so neither is stored
  twice. The Space does not derive per-Board openings yet: PR 2 must add that, and add this file
  to the Space's download patterns. The file is not stable run to run. 258 of the ledger's 284
  post-baseline ticks added a Board never seen before (median 11), so the ~2 MB file (~400 KB
  gzipped) is rewritten most runs, like the trends ledger, and `reclaim_storage` collects the
  superseded copies.

**Two Boards are one company only when they share a Tenant or a curated alias.** **Tenant** is
re-defined in CONTEXT.md for this: the customer an ATS hosts, which can hold several Boards. It
had been retired as a name for the `(ATS, slug)` pair, and "account" was rejected because
**Account** already means a signed-in person. `board_operator.tenant` reads it, now public and
shared with the Hot tab's operator labels. Measured over the ledger's 34,203 Boards:
- Workday splits a Tenant into sites (720 Tenants).
- Taleo Enterprise splits a host into career sections (40 hosts).
- Taleo Business Edition splits an `org` into `cws` sites (15 orgs). Its host is a pod many orgs
  share, and correcting that moved no operator label.
- Casing duplicates (`smartrecruiters:AbhiBus` / `abhibus`) fold in because the comparison
  ignores case (267 pairs).

The only cross-ATS identity is `board_naming.DISPLAY_ALIASES`. Lockheed Martin on Eightfold
and SuccessFactors was the first such pair, since split (see the amendment below).

**A Tenant is usually one employer, and a holding group is one entry.** `workday:volarisgroup`'s
26 Boards are its portfolio companies' sites, and they appear as the group. An entry is named by
a curated alias, else by a name at least half its Boards state, else by its Tenant. The first
build named it by the first stated name, which made all 14 of `workday:luminegrp`'s Boards
"Motive" after one site. Half rather than a strict majority, so a Board and its unnamed stale
casing duplicate keep the real spelling ("NVIDIA", "AbhiBus").

**A matching name never merges, stated or not.** The first draft merged cased names across ATSes
and any names within one ATS. Read against the data, that made one "Pearl" of four ATSes'
Boards, one "Arlo" of a New York startup and Netgear's spin-off, and one "Clarity" of two
unrelated Ashby Tenants. So an entry is a **Company** only as far as the data proves it. An
employer on two ATSes with no alias (Schonfeld on Greenhouse and SmartRecruiters) appears twice
under one name, and the user picks both. That duplication is visible to the user. A wrong merge
would add another employer's openings to their chart without telling them.

**Naming moved out of `hot_boards` into `ingest/board_naming`, and was fixed where the directory
exposed it.** Two stages now name Boards and must agree, so the naming code (`display_name`,
`stated_name`, the aliases, `board_names`) has its own module. Naming every Board rather than a
Hot-tab head surfaced misnamed Boards: 179 of the 32,829 hiring Boards change name. That
includes the Hot tab, whose names change too:

- **URL slugs that tidied to "Https:"** (81 Boards: 74 Taleo Enterprise, 7 Taleo Business
  Edition).
- **SuccessFactors rows whose company is a board word of their own host** (42): "Www", "Apply",
  "Join". A host label that names the company stays: "sap" on `jobs.sap.com`, "six-group".
- **Workday site names in the company column** (37): "EXTERNAL_CAREERS" for Boeing,
  "CorporateCareers" for Mastercard. A site is rejected as a name only when it is worded like a
  site, with a board word such as "Careers" or "External". A bare site like `jiostar/JioStar`
  is often the best name available.
- **Taleo Business Edition's ledger spelling and pod** (17): `GATEWAYVENT:77@phg…` as a company,
  and pod names like "Phh" standing for unrelated employers. It is now named by its `org`.

A few get worse, where a site said more than its Tenant: "Samsung_Careers" becomes "Sec", and
"Maxis-Career" becomes "Maxine".

## Rejected alternatives

- **Auto-resolving typed names.** Every measured collision above would reach a chart
  unannounced.
- **Building the directory in the Space at boot.** No pipeline change, but it duplicates the
  naming rules outside `ingest` and spends boot time on every restart.
- **Extending `hot_boards` to write it.** No new stage, but the module would then do more than
  its name says. For the same reason, the naming code that both stages share moved out of it.
- **Merging on a stated name across ATSes.** This was the first draft. It is refuted by the
  collisions above.
- **Listing only the Boards hiring now.** This was the first build. It drops closed Boards'
  history from their company's line.

## Consequences

- **Company totals overcount wherever one company's Boards list the same requisitions, until
  the index removes the copies.** The delta ledger holds counts, not ids, so the Space cannot
  remove them at read time. Measured on the 2026-09-23 served table, there are three shapes:
  - Taleo Enterprise career sections each serve the Tenant's whole set: 22,929 rows for 2,475
    distinct (Tenant, requisition) pairs. HDR alone has 8,235 rows for 549.
  - Workday sites overlap: 7,146 of 104,849 rows are copies.
  - An aliased cross-ATS pair can mirror itself: Lockheed's Eightfold Board shares 1,248 of its
    1,249 distinct titles with its SuccessFactors Board. The amendment below un-aliases it.

  A deduplication pass has since been decided in a separate thread. It parks Taleo sections
  whose requisitions another section already holds, keeps one row per (Workday Tenant,
  requisition) in `sync` and `prune`, and later parks the Eightfold side of cross-ATS pairs.
  None of it re-keys a Board, and #601 proposes an epoch marker (ADR-0188) for the tick its
  removals land. Until it lands, PR 2 must not present an entry's sum as the
  company's openings without saying so. When it lands, parked Boards' negative deltas will read
  as a drop in their company's line at that tick, which is a methodology change, not hiring.
- A company's series begins at its Boards' first delta, on or after 2026-09-13, and the tab
  must say so rather than let a ten-day line read as a whole history.
- On 2026-09-24 the directory holds 32,597 companies over 34,203 Boards, 830 of them owning more
  than one Board, in 2,073,102 bytes.

## Amendment (2026-09-24): the Space serves picks

The second of the three PRs puts the directory behind two routes in the Space.

- **`/companies/suggest?q=&limit=`** ranks directory entries with `headstart.company_match`,
  the tiers the Decision names. Each suggestion carries its current tech openings, which the
  Space sums from the delta ledger it already loads, and its Board count and ATSes. Two picked
  entries with the same name are labelled with their ATSes.
- **`/trends?company=`** (repeatable) takes **any** board_key of an entry, so a Hot-tab row or
  a search result links to its company by the key it already holds. A pick replays only its
  Boards' deltas, and it combines with comparable coverage. `split=company` draws one line per
  pick, within a family when one is given. Four details came out of review:
  - `company_totals` carries each pick's own denominator, so a line split by company can be a
    share of that company rather than of every pick combined.
  - Under a pick, whether a run measured `new` is read from the whole ledger. One company can
    go a run with nothing new, which is a 0, and every pick with rows in scope keeps a line.
  - `history_start` names the first run a pick is charted from.
  - Two picks sharing a name are labelled by ATS, and by key when they share the ATS too (220
    name pairs do). The matcher strips only a *trailing* legal form, so "SA Power Networks"
    keeps its "SA".

**Within-Tenant duplicates are gone** from the first pipeline run after #602 (Taleo Enterprise
sections, `DEDUP_VERSION` 2) and #603 (Workday sites, 3). That tick carries ADR-0188's
duplicate-removal epoch marker. From then on, a sum over one Tenant's Boards no longer double
counts. The ledger's points before that tick still carry the copies, and so do the openings a
suggestion shows until the tick lands. The marker is the only signal of this on the chart.

**Cross-ATS mirrors remain** (9,872 rows; not yet scheduled), and one directory entry was built
across them: Lockheed Martin's alias spanned both its SuccessFactors Board and its Eightfold
Board, which mirrors it. The alias now covers only the SuccessFactors Board (user decision), so
that entry counts once. The Eightfold Board is its own entry, still named "Lockheed Martin"
because it states that itself, and it is labelled by ATS if both are picked. Re-alias it once
cross-ATS mirrors are parked.

## Amendment (2026-09-24): the Trends tab picks, and suggests one entry per name

The third PR is the UI. Four user decisions shape it, and one changes the matcher.

- **One suggestion per name.** Of the directory entries whose names normalize alike, the picker
  offers only the one with the most openings (user decision, over the Decision's "the user picks
  both"). Most such twins are cross-ATS mirrors: on 2026-09-24 "NVIDIA Corporation" on Eightfold
  (2,048 openings) and "Nvidia" on Workday (2,043) were one employer listed twice. The cost is a
  real second Board or a different employer of the same name that the list no longer offers:
  Schonfeld's SmartRecruiters Board and the smaller Workday "Citi" drop out. A link by Board key
  still reaches any entry, and the directory itself is unchanged.
- **Small picks open on one Total line.** "Break down by" gains Category, Total and, with two or
  more picks, Company. Until the reader chooses, a set of picks with fewer than two categories at
  the chart's indexing floor (5 openings) opens on Total, the category lines summed in the
  browser, since those partition a pick's tech openings exactly. Share is withdrawn under a
  top-level Company split, where each line is a whole company and reads 100%.
- **The follow list is one option.** Followed companies are stored as Board keys, so an empty
  query offers "Add the N companies you follow" rather than a list the page cannot name.
- **No cap on picks.** Past eight, a Company split folds the rest into "Other", as categories do.

Picks live in the hash (`#trends?company=…&by=…`), so a view can be shared and the "See trend"
links on Hot-tab rows and search results land already picked. A search result's Board is
`boardOf`'s guess from the job id (ADR-0049). 98.1% of the 514,163 served rows resolve exactly
to a directory Board. The rest, and any other key the Space refuses, are dropped with a sentence
under the picker rather than failing the chart.
