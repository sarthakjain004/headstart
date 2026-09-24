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
  query offers "Add the companies you follow" rather than a list the page cannot name. It is
  uncounted: two followed Boards of one employer are one company, which only the Space can see.
- **No cap on picks.** Past eight, a Company split folds the rest into "Other", as categories do.

Picks live in the hash (`#trends?company=…&by=…`), so a view can be shared and the "See trend"
links on Hot-tab rows and search results land already picked. A search result's Board is
`boardOf`'s guess from the job id (ADR-0049). 98.1% of the 514,163 served rows resolve exactly
to a directory Board. The rest, and any other key the Space refuses, are dropped with a sentence
under the picker rather than failing the chart.

## Amendment (2026-09-24): what using the shipped tab showed

A critique drove the merged tab against the 2026-09-24 state (33,966 companies) and found the
chart misleading or empty for the common company. Each fix below answers a measurement.

- **Most companies drew nothing.** 22,863 of 33,966 hold fewer than 5 tech openings (median
  2), below Change's indexing floor, so the default view was one "not indexed" row over a blank
  plot. Change is now withdrawn, with its reason, wherever no line can be indexed, and counts are
  drawn. The reader's unit returns once a view can show it. A perfectly flat Total also drew
  every y as NaN, and now gets an axis with height.
- **Found Boards read as hiring.** 254 of 852 multi-Board companies gained a Board after their
  line began, and each lands its whole backlog at once (Hyatt +1,048 over 83 Boards). This is the
  reason the Hot tab leaves new Boards out. `/trends` now returns `discovered` under a pick
  (`{ts, company, boards, openings}`, from each Board's first tick), and the chart marks each with
  a solid ink line. A Board that brought no tech openings, or that lands on the point where its
  company's line begins, is not marked. Across a found Board, or across duplicate removal at a
  pick holding several Taleo Enterprise or Workday Boards (the only Boards it parks), no riser or
  faller is named. Tech-filter and extraction changes still allow one, as they do on the index
  chart. Epochs now carry `fields` (the version columns that moved) beside their labels, so the
  tab keys on `dedup_version`, not on prose.
- **The history note was wrong for late starters.** 9,981 companies were first counted after
  2026-09-13, but the note named 2026-09-13. `/trends` now returns `counted_since`, each pick's
  own first counted tick, in place of `history_start`, and the note names it per company. It
  shows whenever a company is picked, including on a one-run line.
- **Unfindable and fragmented names.** "jpmorgan" finds nothing (the Board is named "Jpmc"), and
  Siemens, Goldman Sachs, Flipkart, Shopify, Swiggy and TCS have no Board at all. The no-match
  line now says a Board may be unread or named otherwise. The picker also stays open on its
  query after a pick, so an employer split across entries (Atlassian's three iCIMS Tenants) takes
  a few Enters, with the next entry already active. Curated aliases for well-known employers
  remain open work.
- **No way from a trend to the jobs.** One pick now offers "See its open roles". It hands over
  the company's Board keys, not its name: `/search` and `/facets` take `board=` (repeatable, at
  most 200), kept out of `SearchFilters` like follow/hide so a Saved Set never freezes it. The
  name's substring match found 0 of Booz Allen Hamilton's 1,388 rows and 0 of Six Group's 30,
  and returned 2,057 rows for one Citi Board that holds 927 (2026-09-15 table). The Hot tab's
  "See roles" had the same defect and now hands over its Board too.
- **The picker sat under the chart on a phone.** Companies now have their own row above the
  filters, and the heading names the pick.

## Amendment (2026-09-24): a critic's first round

A critique agent used the fixed tab (about 25 journeys, 60 suggestion queries) and scored it
5/10. Its measured findings, and what changed:

- **"New this week" read a found Board's backlog as hiring** (Razorpay: 19 new of 19 open). Under
  a pick, a Board found after the ledger's first tick now counts toward `new` only once the
  flow window (7 days) has passed since it was found, the Hot tab's rule. Until then the tab
  says why nothing is new yet.
- **Movers named across steps that are not hiring.** Wipro's "+74.7%" held about +25% from the
  Sep 17 tech-filter step. NVIDIA with AMD (counted from Sep 24) named
  "engineering-management +442.9%". Under a pick, no riser or faller is named across a
  line-moving counting change (a taxonomy refit, a family-map edit or a tech-filter change),
  across duplicate removal at a pick it can touch, across a found Board, or across a later
  pick joining a summed view. The last of these is also marked. The crosshair's tooltip and
  readout now name the step at its stamp, on every chart.
- **Misnamed and unfindable companies.** The ATS's own site title ("Oracle Taleo" for Scripps,
  PMG and PruittHealth; "Successfactors" for TTTech) is no longer taken as the company. Curated
  aliases name JPMorgan Chase's `jpmc` Board and join Atlassian's three iCIMS Tenants into one
  company; both take effect at the next pipeline run. Test tenants with no openings ("Jpmc
  Dev1", "Nvidia Sandbox2") are not suggested. The critic's claim that multi-word queries fail
  was checked and is wrong: "morgan stanley" and "bosch group" match. Those employers are
  unindexed, not unmatched.
- **Controls lost state.** The drill, unit, measure, window and coverage now ride the hash, and a
  drill is a history entry, so Back leaves it. A cold link keeps a roles drill. An empty
  Comparable window says per-Board counting began on `ledger_start` rather than "widen the
  dates". An empty answer no longer decides the auto breakdown, and Source lists only the
  picks' ATSes. A refusal note clears on the reader's next pick. Enter takes the top
  suggestion. No "−0.0%" mover is named.

## Amendment (2026-09-24): a critic's second round

A fresh critique agent scored the next build 5/10. Its measured findings, and what changed:

- **"New this week" invented collapses at the biggest employers.** Amazon's `new` held at
  ~8,600 for exactly seven days from the ledger's first tick, then fell to 1,371, and Google's
  went from 1,690 to 489. The ledger's first week reads every Board's backlog as new, not only a
  found Board's. The hold now covers every Board from its first tick, the baseline included.
  `new_counted_from` gives the first run a pick's `new` can count, and the tab says so.
- **Percentages still carried the marked steps.** A withheld riser tile did not help while the
  legend printed the same number. Legend, table and tile percentages are now net of the marked
  steps: each later value is scaled back by the jump the step made. Wipro's Software
  Engineering reads +27.7% instead of +74.7%, and Google with BAE Systems no longer shows
  "+1170.2%". Since the figures are net, movers are named again. The plotted lines keep their
  steps, marked.
- **Findability.** Suggestions treat names that differ only by a trailing "Technology",
  "Technologies", "Group" or "Holdings" as one name when keeping the largest (Micron, 1,949 on
  Workday and 1,887 on Eightfold). Matching is unchanged, so "micron tech" still finds the entry
  that says it.
- **Friction.** A pick closes and clears the list, which covered the filters (both critics).
  "See their open roles" hands every pick's Boards over at once. A tap reads the chart on a
  touch screen. Tables name their first column for the view (Category, Line, Company, Level,
  Role). A count axis has whole-number ticks, and "too few to index" replaces "not indexed".
- **Not fixed here, and why:**
  - AI labs' Software Engineer titles land in `ai-ml`: Anthropic reads software-engineering 4
    against 91 such titles. That is the role taxonomy (ADR-0040), not this tab.
  - Shopify, Goldman Sachs, Flipkart, Swiggy and TCS have no Board. That is discovery work.
  - The Hot tab's "open now" counts a different stock from the trend's tech openings.
  - Search has no role-family filter to carry a drilled category over.
