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
- **Findability.** A first cut treated names that differ only by a trailing "Technology" or
  "Group" as one name, to fold Micron's two entries (1,949 on Workday, 1,887 on Eightfold). The
  code review measured it over the directory: it joined 120 name pairs, and nearly all were
  different employers (Affinity / Affinity Group, Blackstone / Blackstone Technology Group). It
  was withdrawn. Micron's two entries stay until an alias joins them.
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

Its code review then tightened the round:

- Netting is per line. Under a Company breakdown, a company's own step (a found Board, its
  joining a sum) is divided out of that company's line only, so another company's real change
  in that run survives. A step that falls on a gap in a line lands on its next point.
- The `new` hold applies to every replay of the ledger, so the comparable index chart is held
  too. `new_counted_from` is now per pick. Under `new`, a found Board's marker sits at the run
  it starts counting, not at its arrival.
- Under comparable coverage, a later pick has no Boards in the cohort, so it is not marked.
  Duplicate removal withholds only at a pick with two or more Boards on one of its ATSes.
- The hand-off cap comes from the page (`CFG.max_scoped_boards`). Past it the link says why.
  Source hides only the ATSes the picks are not on, and hidden boxes are never sent. A custom
  date range survives Back. The empty-Comparable advice appears only when the window starts
  before per-Board counting.

### A critic's third round (2026-09-24): 5.5/10, and the line and its number disagreed

The critic used the tab again with a signed-in session. The worst finding was a regression the
second round introduced: the percentages were net of the marked steps but the lines were not.
Google's Total line ended near 117 over a legend reading −0.2%, and on a phone Google's line
was the highest while its tile said "Biggest faller". What changed:

- **The Change plot draws the net levels.** Steps are taken out backwards, the way a price
  history is adjusted for a split: the latest value stays real, and the history before each
  step is scaled by that step's jump. Every percentage reads from the same levels, so a line
  and its number can no longer disagree. Measured on the same data, each company's line ends
  at 100 plus its legend percentage (Amazon 97.1 against −2.9%, Microsoft 107.7 against
  +7.6%). Share and Count plot real levels, which a reader takes off the axis, and break the
  line at each marked step rather than draw the jump as a climb.
- **Steps belong to the picks they can move.** Duplicate removal is taken out only of a pick
  whose Boards it can touch. That is two or more on Taleo Enterprise or Workday, or an
  Eightfold Board beside another (#632 aliases Eightfold mirrors). Google had read −0.2% beside
  Stripe and −0.5% beside Micron, and now reads +0.2% beside either. Under a pick, a counting
  change that cannot move its lines is not marked. An extraction change moves only a Level
  breakdown.
- **Two or more picks compare by default** (Company breakdown). Summed categories under "at 4
  companies" answered a question nobody asked. One pick keeps Category, or Total when small.
- **A sentence per company** above the chart gives its tech openings now and its move in
  percent and in openings, net of the marked steps. Examples: "Google: 1,802 tech openings;
  about flat over 11 days (+0.2%, +3 openings)", then how long HeadStart has counted it. Under
  14 days it adds that this is an early sign, not a trend.
- **Small numbers are counts, not percentages.** A line starting under 20 openings shows its
  change in openings, and no tile names it. Stripe's "Biggest riser +18.2%" was 11 openings
  becoming 13.
- **Nothing is dropped silently.** The Space names `uncounted` picks, and the page says why
  each is missing. Comparable coverage whose window starts before per-Board counting now begins
  at the first per-Board run and says so. Before, Google over 30 days answered nothing. With no
  runs, the caption is empty ("live openings at , or…").
- **A held week is a gap.** Under `new`, a pick's runs before its first week ends are `None`,
  not 0. The zeros drew a week of nothing and then a surge. A summed line starts with its
  earliest pick.
- **Marker tooltips give the size.** Each line's row reads, for example, "1,739 · index 103 ·
  jumped +148 here". The index is named.
- **Notes fit the view.** The dashed whole-company line appears only where lines are parts of
  it, so not on Total or Company. The reassignment caveat is hidden where a category cannot
  move. The step explanation appears only where a line has a step and a percentage, and sits
  in the caption under the chart.
- **Search keeps the hand-off.** It rides in `#search?board=…&label=…&q=…`, so a reload keeps
  it. From a drill, the category's name becomes the semantic query. Search has no category
  filter, so the jobs come ranked by the category rather than narrowed to it.
- **Findability.** Company search matches a name prefix with spaces ignored (5+ letters), so
  "micro soft" finds Microsoft. "jp morgan" needs the pending `Jpmc` alias to reach the served
  directory.
- **Phones.** The legend follows the chart directly, then the tiles, then the filters. A phone
  draws no end labels, so the legend is the only key.

### A critic's fourth round (2026-09-25): 5/10, and a counting change that lands over two runs

The third round's code review tightened a few things first. The mover floor is now held to
the openings a line really started with, not its adjusted head. A step up from zero starts the
line there. Why a pick is missing is read from the data, not from which control is on. "Counted
for N days" comes from `counted_since`, not from the window. The Search hand-off is compared
whole, so Back between two drills of one company keeps its query.

The fourth critic then found the netting itself was incomplete:

- **A counting change can land over two runs.** Amazon's Sep 17 tech-filter change was +308
  openings at its run and −439 at the next (measured in the Board-delta ledger). Leaving out
  only the first run turned Amazon's +1.2% into "down 2.9%". NVIDIA was +94 then −51. The run
  after a counting change is now left out too. It is marked in the tooltip as the change still
  settling, not drawn as its own marker. The cost is one run of ordinary change per counting
  change. Amazon now reads about flat, +1.3%.
- **Found openings and a later pick joining are added back, not scaled.** They were open all
  along, so the history is lifted by their count. Scaling multiplied every earlier move by the
  jump's ratio: NVIDIA with AMD summed to −146 against −99 and +1 apart. A counting change still
  scales, since it re-sorts a share of the line. Now a sum moves by the sum of its parts (−44).
- **The Hot tab leaves counting changes out too.** `hot_boards` now reads `trends_epochs.csv`
  and drops each stock-moving change's run and the next from its 7-day net change. Hot called
  Amazon "+532 net roles" while its "See trend" link read it falling. On the same data Hot now
  says +39.
- **The sentence names what is not hiring.** Google's Count line climbed 1,540 → 1,802 under
  "about flat". The sentence now adds "the chart's other +269 openings came from counting
  changes and boards found later, not hiring".
- **Too new to judge.** A line with under 2 days of measurements names no direction ("too new
  to show a direction yet"), and no tile headlines it. AMD, counted for hours, was "Biggest
  riser".
- **Fewer empty marks.** Under a pick, a counting change is marked only where a drawn line
  moved. A found Board is marked only when it brought at least 5 openings, though it is still
  taken out of the line.
- **The date range says what it can show.** A preset longer than the picks' counted history is
  disabled, with the reason. Before, 30 days, 90 days and All drew one chart.
- **Findability.**
  - Company search takes aliases people use: "aws", "jp morgan", "facebook", "google deepmind",
    "tcs".
  - A typo is never forgiven in the first letter, since "cisco" offered Discovery and Discord.
  - AMD, Deloitte South Asia and Morgan Stanley get display names, each checked on its Board;
    these take effect at the next pipeline run.
  - Micron's two entries, Workday and Eightfold, are left apart on purpose. The Eightfold Board
    mirrors the Workday one, so joining them would double-count until requisition-level
    duplicate removal lands.
- **Smaller.**
  - The Company view's heading asks "How tech hiring compares at N companies".
  - Enter pressed before suggestions arrive picks the top one when they do.
  - A change of picks is its own history entry.
  - On a phone the tooltip sits under the chart, and a result card's "trend" and "hide" links
    grow to 44px targets.
- **Not fixed here:**
  - Missing employers (TCS, Flipkart, Swiggy, Goldman Sachs) need discovery.
  - Share inside a drill stays a share of the company.
  - The requisition-level duplicate removal (another PR) will remove Eightfold rows gradually over
    several days. It will record them in `dedup_evictions.csv` so this chart can add them back
    per company.

Its code review tightened the round:

- Several picks summed (Category or Total) no longer get one combined figure. A company
  joining the sum lands with the other companies' ordinary change of that run inside its step,
  so the figure was near, not equal, to the sum of the companies' moves. The sentence points to
  the Company breakdown instead.
- On a whole-company line under All openings, a found Board lifts the history by its own size
  (`discovered.openings`), so that run's other hiring stays in.
- A run holding a counting change and a found Board together is scaled. A step to or from zero
  starts the line. The "not hiring" figure is the sum of the step sizes.
- "Too new" is under 3 days. The sentence adds a weekly rate ("about +77 a week"). It says
  "HeadStart has counted Google since Sep 13", without day arithmetic.
- A query alias matches only a company's exact name, so "facebook" no longer offers Metabase.
- Hot drops a duplicate-removal run for every Board, while the chart drops it only at
  companies it can touch. A Board list has no company to ask, so the two can differ by one run
  of ordinary change there. A change whose own tick was skipped lands on the next tick.

### A critic's fifth round (2026-09-25): 6/10

The round opened with production still on older code. Each merge synced the Space, and the
next merge restarted the build, so it cycled BUILDING/APP_STARTING. It reached RUNNING on
`1cf75b82`, which carries #643, at 20:04 UTC. The rest of the critique was about the local
build:

- **The category hand-off filters to the category.** Search has no family column, but the
  pipeline's `role_assignments.parquet` (id → family, ADR-0057) is exactly what the Trends
  counts are made of. The Space now loads it (`search.load_family_ids`, about 4 MB). Beside
  `board=`, `family=` becomes an `id IN (…)` clause over those Boards' Jobs in the family,
  capped at 5,000 ids. Google › AI / Machine Learning now opens as Google's AI roles, not all
  1,856 Google jobs ranked by a query. The pill names the category, and the hand-off keeps it in
  `#search?…&family=…&area=…`.
- **Every figure is first-to-last.** The percentages averaged the first and last three runs,
  so a line's end (107.7) and its percentage (+7.6%) differed. A sentence's "+121 hiring" and
  "+477 counting changes" also failed to add up to the chart's +583. Endpoints fix both, and the
  settling run now does the job the averaging did. "Counting changes" is the chart's move less
  the hiring move, so the two always add up.
- **The table says what it shows.** Its columns are "Change, hiring only", "Counting changes"
  and "Start, as counted". Before, 764 → 1,018 sat beside "−0.2%".
- **One flat band, ±1%,** for arrows, tiles and sentences. "About flat" had meant under 2%
  while the arrow went up from 1%, so a −0.6% line was "Biggest faller".
- **Copy.**
  - Under Comparable coverage the date is restored ("counted this company — too short" had
    lost it).
  - A short window is told apart from a young company.
  - "New this week" loses its weekly rate.
  - The early-sign line says when a month of counting arrives ("Oct 13").
  - Titles fit the view: "How tech hiring is moving at Amazon" under Total, "How AI / Machine
    Learning hiring compares at 2 companies" inside a category.
  - Share is off under a Total line, since all tech roles as a share of the company read "96%".
  - A drill gets a sentence too: the category's total for one company, or one per company.
- **A custom date range rides in the link** (`since=`/`until=`, UTC minutes), and no preset
  stays checked over it.
- **Not fixed here:**
  - A Board read unchanged for many runs (Google at 1,690 for 60 runs) looks the same as one not
    read at all. Telling them apart needs per-run scrape evidence the Board-delta ledger does not
    hold.
  - JPMorgan's display name arrives with the next pipeline run.
  - Paytm's two entries stay apart.

Its code review tightened the round:

- **The Space tells the page what the category hand-off can do.** `CFG.family_handoff` is off
  with no assignment snapshot. `CFG.max_family_ids` is 5,000, and a larger request is refused
  rather than widened. Past either, and in a watched-roles view, which counts titles across
  categories, the category's name ranks the jobs instead, and the pill claims no filter.
- **Lookups are cheap.** Assignment ids are held sorted and found per Board by bisection, not
  by scanning a family's list per request.
- **Consistent readings.**
  - "Too new" is judged by each company's own counting date.
  - Arrows and "flat" are judged on the figure as printed.
  - The table's counting column is in openings in every unit, and a hiring percentage carries
    its openings ("+1.0% (+19)").
  - A one-company drill is titled for its category.
- **Also in this round:** any company holding an Eightfold Board has duplicate removal taken out
  of its line (#649 drops an Eightfold-only entry's mirrored rows in one run). On a phone, the
  tooltip under the chart wraps names.
- **Known limits.** Endpoints carry no noise guard beyond the marked steps: one thin last run
  can move a tile. A duplicate-removal epoch names no ATS, so an Eightfold pick also has a
  Workday- or Taleo-only duplicate-removal run taken out, which costs it one run of ordinary
  change.

### A critic's sixth round (2026-09-25): 6/10, measured on the live build

The Space ran #665 (repo and runtime sha 952b419c, built from f35041b6). What changed:

- **Steps come out by their size in openings, never by ratio.** Scaling the history multiplied
  whatever moved before a step. Microsoft's architecture line went 5 → 4, and a filter change
  took it to 58. Scaled ×14.5, the one real opening became "−12 openings". Added back, it is −1,
  and the line is flat. A history pushed below zero is held at zero. The additive rule also
  makes category lines sum to the company's at each counting change.
- **The sentence always states the counting part,** under the mover floor too. Paytm's "−11"
  sat over a line that went 14 → 6.
- **Duplicate removals come out exactly.** The Space reads #649's `dedup_evictions.csv` and
  names each pick's removals per charted run (`evicted`). A whole-company line takes them out at
  their size. A category line does not guess where they fell, since the ledger carries no
  family. This stays dark until the first run after #649 writes the ledger.
- **One NVIDIA, one Micron, one Morgan Stanley.** The Eightfold site of each mirrors its Workday
  Board. Search cards opened "NVIDIA Corporation" and "Nvidia" as two trends of about 2,045
  each. `config/company_names.csv` now gives each pair one name. That is safe only because
  #649's pipeline run, which rebuilds the directory with these names, also drops the Eightfold
  copies.
- **Hot counts tech roles only.** Its "open now" and net change included `non-tech` rows:
  Amazon read 9,755 on Hot against 9,229 on the trend its row links to. Hot still ranks one
  Board per company, so a multi-Board company's Hot figure is one Board's, not the company's.
- **A row opens its levels. The "▸ roles" marker opens roles.** Landing on watched roles
  showed "155" under a row that had just said 243. The Role view's tile is now "Openings in
  tracked roles", and its hand-off can be exact too, bounded by the picks' totals.
- **Levels are named** "Entry level (0–1 yrs)" and so on, not "mid".
- **Under New,** a pick's line starts where its first Board's hold ends, so that start is no
  longer marked as "boards found later".
- **No stale answer under a new heading.** On a refetch the sentences, tiles and notes dim,
  `aria-busy` is set, and the heading waits for the answer.
- **Search under a company hand-off:**
  - It names the company in its result line instead of "across every board".
  - It does not fold that company's rows into "18 more at Google".
  - A full first page says "counting the rest…" until the slow total arrives.
- **Disabled presets look disabled,** and the reason is shown under the controls.

Its code review tightened the round:

- **Whole-company sizes stay off category lines.** A drill's summed line is one category, so a
  company's found openings or duplicate removals are never taken out of it at their size.
- **A step larger than what came before it starts the line after it,** rather than clamping the
  history to a zero base, which broke the percentage and the index.
- **No `evicted` under Comparable coverage,** whose cohort leaves out Boards found later.
  Removals count every row, non-tech included, so they read a few percent larger than the tech
  openings they took.
- **Hot names what it counts.** Its figures are "tech roles on this board", because a row is one
  Board and its "See trend" opens the whole company.
- **The Role view's button says "See all its … roles"**, since it hands over the whole category,
  not only the tracked roles its tile counts.
- **Smaller:**
  - The sentence names duplicate removals among the non-hiring runs, and states the counting
    part even when a company is too new to show a direction.
  - The chart note dims with the rest.
  - Search's "counting the rest…" shows the real page range, and is dropped if the total fails.

### A critic's seventh round (2026-09-25): 4/10, the morning after a refit

The pipeline run carrying #666's data fixes landed. NVIDIA, Micron and Morgan Stanley became one
company each, JPMorgan Chase got its name, and Hot went tech-only, as planned. But a family-rules
refit at 2026-09-24 21:19 had started series version 2001. The Space charted the newest version
only, so every history was reset to 6 hours. ADR-0221 stitches versions into one history. The
rest of the round:

- **A counting change on the window's first run no longer takes out the run after it.** It is
  already in every line's start. Taking the next run out cut Amazon's real −7 and put "−12
  counting changes" in the sentence with no marker on the chart.
- **Under 3 days of measurements, a line shows no direction anywhere.** The legend and table
  read "too new". The legend had shown "↑ +1.5%" beside a sentence saying "too new to show a
  direction".
- **A leap one run puts straight back is a partial read, not hiring.** Newyorklife went
  26 → 104 → 26: "+292.3%" at the leap, and the table's maximum after it. The page now drops such
  a point from company lines and says how many it dropped. The newest run has no next run to
  judge it by, so a leap there stands until the next run. Hot ranks off the same ledger and
  still cannot tell.
- **"See its open roles" lists the company's tech roles** (`tech=1`), leaving out the Jobs the
  assignment calls non-tech. Google read 1,800 in Trends against 1,854 in Search.
- **Copy.**
  - "Point at a marked line" appears only where a line is marked.
  - "1 further row sits…"
  - The "▸ roles" marker sits at the row's end, out of the path of a click on the name. Two row
    clicks landed on it.
  - The active tab scrolls into view on a phone.
  - "chase" and "jp morgan" lead to JPMorgan Chase by its new name.
- **Not fixed here:**
  - A parent company's other Boards (IBM, TCS) are a coverage gap.
  - A "request this company" path.
  - Junk names in the directory ("& 04 Woodward").
  - Hot's "new" is still a Board-level rolling count, not the company's netted figure.

### A critic's eighth round (2026-09-25): 5/10, after the stitch

The history held across the refit: Google's all-roles total was 1,849 on both sides, and every
sentence, line and table agreed. What remained:

- **Under New, a counting change is taken out twice: where it lands and a week later.**
  - The openings a change adds read as new at once, then stop reading as new when they age out
    of the 7-day window.
  - That second drop read as NVIDIA "down 45.1%", and as an unmarked −19,600 across the index,
    at the first run a week after the Sep 17 filter change.
  - Under New, each change now carries an echo marker at its window's end.
- **The sentence names each cause, with its size.** "The chart's other −1,811 openings came from
  outside hiring: −2,041 openings as duplicate postings were removed, +230 openings from changes
  in how HeadStart counts". A run holding several kinds of step gives each known size to its kind
  and the rest to counting changes. The solid-marker note names duplicate removals when they are
  drawn.
- **Families by either name (ADR-0220).**
  - The watchlist moved to the v3 families (`ai-ml-data-science`, …) before their data landed,
    so "AI / Machine Learning" lost its roles drill.
  - The Space now maps each retired family to its successor (`_FAMILY_SUCCESSOR`):
    - Watched roles follow a family by either name, while the new name has no data of its own.
    - A link by either name reads as the one the data holds; for a new name, that is its largest
      predecessor.
    - The page adopts the name the Space resolved.
- **"See its open roles" lists tech roles only.**
  - The role-assignment snapshot leaves the classifier's non-tech Jobs out.
  - Non-tech is therefore the served ids the snapshot does not hold (`search.unassigned_ids`),
    read once at boot.
  - `tech=1` had matched nothing: Google read 1,800 in Trends against 1,854 in Search.
- **The latest run's figure, not the last measured.** The tile, the sentence and the legend read
  a category now at none as 0. Syms' tile read "227" beside a sentence of 144.
- **Smaller.**
  - The legend's "too few to index" is now "started under 5".
  - Hot states its net-change window in hours when short ("the last 6 hours").
  - Titles fit every drill ("Tracked roles in AI / Machine Learning at Google"), and a chart with
    no counted pick still names its picks.
- **Not changed:** an exact company name still ranks first ("Infosys", 4 openings, above its
  larger subsidiaries), as a test states.

Its code review changed the round:

- **"See its open roles" explains the non-tech gap instead of filtering it out.**
  - Filtering needed the served ids, read through `to_lance()`. That needs `pylance`, which the
    Space does not install, so the exclusion was dead there.
  - Filtering on "served but unassigned" would also hide every job indexed since the
    assignment snapshot, which happens during a classifier warm-up (ADR-0220).
  - So Search now says how many of its jobs the trend leaves out as non-tech. That figure is each
    company's whole total less its tech openings, carried in the hand-off (`aside=`).
- **The echo under New is for tech-filter changes only.** A refit or a duplicate removal adds no
  newly-seen postings, so nothing ages out a week later. A change up to a week before the window
  now comes with it, and the index chart marks the echo too.
- **Retired families name their successor in `config/role_families.json`**, the one source,
  not a map in the Space.
  - A retired family reads as its successor wherever the successor has data in scope, so a window
    spanning the switch draws one line.
  - Family sizes are weighed in openings.
  - A watched role's parent resolves to the name the data holds, so the AI roles sit under
    "AI / Machine Learning" only, not under Data Science as well.
- **The causes add up exactly.** A known size is given to its kind only on a whole company's
  line, and counting changes are the total less the named parts.
- **One rule for a line's latest figure** (`latestOf`), shared by the tile, the sentence and the
  legend.

### A critic's ninth round (2026-09-25): 6/10

- **A step comes out by ratio where both sides are substantial, and by openings where either
  side is small.**
  - Taking every step out by openings fixed small bases: Microsoft's architecture line went
    4 → 58, and scaling its one lost opening by ×14.5 had read as −12.
  - But it broke refits that halve a category. Google's software-engineering loss of 33 (−5.3%)
    was measured against the halved base and read "−11.8%". It now reads −6.3%.
  - The rule: a ratio when both sides hold at least 20 openings. A known size (found openings,
    duplicates removed) always comes out by openings.
- **A category a refit empties reads 0, and its drop is booked.**
  - Every charted run measures stock, so a series absent from a run after it first appears is
    at 0 there (`_held_at_zero`).
  - A new version's span never re-emits a key it no longer holds. So Syms' systems engineering
    showed its last count, 46, in the table beside 0 in the legend. Its drop is now a counting
    change of −46.
- **A drill agrees with its category row.** An extraction change re-sorts a category's levels
  but never its total, so the drill's summed sentence no longer takes it out. It had read −6.8%
  against the row's −6.3%.
- **One company, one entry.** Paytm's three Boards, Razorpay's two and Infosys's two were each
  split. Each pair was checked to share no posting title, so none of them double-counts, and
  each group is now joined in `config/company_names.csv`. "youtube" and "deepmind" find Google,
  whose Board lists their jobs.
- **Hot's "See trend" opens Hot's own window** (`since=`), so its "+25" and the trend describe
  the same span.
- **A shorter sentence.** "; not hiring: −2,041 from duplicate postings removed, +230 from
  changes in how HeadStart counts". A duplicate-removal change is named as duplicates under
  either measure.
- **Smaller:**
  - Company sentences past two fold under "N more companies".
  - The table's Latest column is the latest run's figure.
  - A share's change no longer carries an openings count.
  - "Too new" wins over "started under 5".
  - An empty drill offers no roles hand-off.
  - A phone's table shows that it scrolls.

Its code review tightened the round:

- **The causes are peeled one kind at a time.** Duplicates are taken out first, then found
  Boards, then everything else. Each kind's figure is how much it moves the line's change, so
  the parts add up exactly even when ratio steps sit between them. Read off each step's size,
  a removal of 2,041 before a ×1.2 step pushed the difference into "counting changes".
- **The ratio floor (`RATIO_FLOOR`, 20) is judged in openings, whatever the unit drawn.** Under
  Share every level sits under 20, so every step had come out by openings.
- **A duplicate-removal change is named as duplicates under New only.** Under All openings,
  the Space's sized removals keep their exact figure beside it, and its settling run carries
  the same name.
- **A tracked role hands over to Search** (`role=`). Search filters by the same title patterns
  `role_trends` counts the role by (`regexp_like`). Google's LLM / GenAI jobs opened as 81
  against Trends' 84, a difference of one data run. The roles view gives each row a "jobs"
  link.
- **Hot's window carries its base**, the run its first change is measured from. A trend opened
  from a Hot row now covers Hot's figure.
- **Smaller:**
  - A category a run leaves out reads 0 only under a pick. On the index chart it is a taxonomy
    change the chart marks.
  - The reference line leaves extraction steps in, as the sentence does.
  - An opened fold stays open across redraws.
  - The range note says "at the earliest" for picks counted from different dates.
  - "google deepmind" finds Google.
