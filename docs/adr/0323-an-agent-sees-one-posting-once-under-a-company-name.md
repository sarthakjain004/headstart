# ADR-0323: An agent sees one posting once, under a company's name, and a company's places by country

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md) (per-page copies),
[ADR-0275](0275-an-agent-looks-a-company-up-and-reads-its-hiring-profile.md) (`company_profile`, `/companies/locations`),
[ADR-0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md) (`get_job`),
[ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) (the gazetteer),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (the Company directory)
· **Amended** 2026-09-29 by the code review of #897: the copy rule, the naming fallback, the
missing-id account and the city roll-up below are as that review left them.

## Context

The second critique of the Space MCP server (2026-09-29, 6.0/10) found five output faults in
`search_jobs`, `get_job` and `company_profile`:

1. **One posting twice on a page.** "IT Associate Software Engineer (Hybrid)" at Eversource came
   back as rows 1 and 2 (`st05b`), once from its Radancy front ("EVERSOURCE") and once from its
   Workday Board ("Eversource Energy"). ADR-0274's grouping keys on the exact company, so it missed
   them.
2. **A host for a company.** `get_job` printed "aah.wd5.myworkdayjobs.com/external" as a company
   (`js08b`). A Board that states no company name is served under its own key, host or URL, or
   under nothing. Measured on the served table (v475, 498,460 rows): 730 rows on 72 Boards. 498
   carry an empty company (491 of them Oracle), and 232 carry their Board's key, host or URL
   (Workday keys, Oracle pods, Taleo URLs, Eightfold hosts).
3. **A bogus id "most likely closed".** `greenhouse:stripe:0000` and an id on a Board HeadStart
   never held both read as closed postings (`js08b`, `js10b`).
4. **A wrong hint.** Asked 12,000 characters for two ids, `get_job` showed 8,997 of each and said
   "raise max_chars_per_job up to 12,000" (`inj03`). The shared 18,000-character budget had cut it,
   so raising the cap could not help.
5. **A profile that could not be read.** Stripe's places listed "N/A" 23, "Dublin" 15 and "Dublin,
   Ireland" 4 apart. Its levels were the Search rail's ceilings ("open to someone with at most 0, 2,
   5, 10 years"), where a job stating no experience counts at every one. "employment type:
   full-time 0 · part-time 0 …" on a Greenhouse Board read as no full-time jobs (`rc02b`).

## Decision

**A copy is the same title under one company, or the same title, first city and countries under
two spellings of it** (`space_mcp.posting_copies`). ADR-0274's rule stays: the same company and
title, brackets aside, wherever it is placed. Rows naming no company are copies only on one Board,
as an Oracle pod's per-country copies are; two unnamed Boards are not one company. A second rule
adds rows whose company names are the same words once legal and a few generic words drop ("Inc",
"LLC", "Corporation", "Group", "Technologies", "Energy"): "EVERSOURCE" and "Eversource Energy".
Because two spellings are looser than one, it also needs the same title stem, the same first
city (the words before the first comma of the first place a location names) and the same
countries, as the `country` filter's gazetteer reads the whole location. As before, a copy is
listed under the row it repeats, and every id, link and row number stays.

The first version took one name's words as a prefix of the other's, and compared the first city
only. The review of #897 found it grouped "GE" with "GE HealthCare" at Berlin, CT beside Berlin,
Germany, and "Meta" with "Meta Financial Group", so it was tightened to the rule above. Measured
on 2026-09-29, before and after:

- **On 16 live pages of 40 rows** (640 rows, `/search`), both versions added the same 2 pairs,
  both true copies: Eversource's Radancy front and Workday Board, and L3Harris's SuccessFactors
  Board and Radancy front. Neither removed any of the old rule's pairs (74 on the first run, 78
  on the second: the pages moved between them).
- **Over the whole served table** (v475), the prefix rule grouped 3,285 row pairs across 150
  spelling pairs, one of them wrong (a GE Vernova row served as "GE" beside "GE HealthCare") and
  one unclear ("Flow" and "Flow Traders"). The tightened rule groups 1,164 pairs across 39 spelling
  pairs, all read as one employer ("L3Harris" and "L3Harris Technologies", "Rakuten" and "Rakuten
  Group", "Staples Inc." and "Staples, Inc."). The other 111 spelling pairs (2,121 row pairs,
  "Booz Allen" and "Booz Allen Hamilton" the largest) are listed as separate rows again.

**A company named only by its Board is shown by the Company directory's name**
(`space_mcp.shown_company`, named for what it decides: the review of #897 found `company_names`
a near-homograph of the `company_names` cache and `boards/company_name.py`). A served name counts
as naming no company when it is empty, or when it is lowercase, holds a `.`, `/`, `:` or `@`, and
`company_name.echoes_board` reads it as the Board's key, host, URL or path. That rule already
decides the directory's names (`board_naming.stated_name`). "Checkout.com" on `ashby:checkout.com`
is cased, so it stays a name. For such rows `search_jobs` and `get_job` make one
`/companies/lookup` call over their distinct Boards, at most ten. One Board the directory lacks
refuses the whole lookup, so the Boards are then asked one at a time. The row shows the label,
marked `(directory name)`; a Board the directory holds and names no company for reads "no
company name", never its host. The lookup is a courtesy: a Board the Space could not be asked
about, or one past the tenth, keeps its served name.

**An id not in the index now is given one account of why, and an id that was never one is named
so.** The first version said "has closed, or was never an id". It left out the ways a row leaves
with no grace period: `index prune` drops a duplicate of another row, which stays served under its
own id, and a row whose Board is no longer read (ADR-0023); a Dormant Board's rows go (ADR-0250);
and the tech filter can stop counting a row as tech (ADR-0243). That account is now one sentence,
`serving/job_absence.WHY_NOT_SERVED`, which `/search?like=` and `get_job` both say. `get_job`
calls an id no HeadStart id when it is not shaped `ats:board:posting`, or when HeadStart holds
no Board it names: the Company directory does not hold it (`/companies/lookup`) and the index
serves no job on it (`/facets` with that Board, total alone). The first version asked the index
alone, so a held Board whose last posting had closed read as never held. When the Space cannot
say, only the shared sentence is given.

**A cut description names the limit that cut it.** When the shared budget cut it, the answer
says "ask for this id alone". When `max_chars_per_job` did, it says how far to raise it, and that
figure never goes past the shared budget. A link is never cut, so each character a job's link
runs past an id's 300 comes out of the descriptions' budget: five full descriptions with
2,000-character links reached 35,110 characters against the tool's 30,000. The count of
characters shown leaves out the ellipsis the answer adds.

**A company profile counts places by country, and levels by band.** Both are served additions,
so the agent contract goes to 6:

- `/companies/locations` adds `countries`: each country's jobs and its three commonest cities,
  from the `country` filter's own gazetteer. So "Ireland 23" is what `country=IE` would count. A
  place counts under its first city (before its first comma) when that city names no other
  country, spelled as its commonest place writes it, so "Dublin" 15 and "Dublin, Ireland" 4 are
  "Dublin" 19; "London, Dublin" stays whole under Ireland (agent contract 8, the review of #897). It also adds `no_country`, the jobs whose place names none, with their places, and
  `places_unread`. A job naming two countries counts in both. The gazetteer costs about 1.6 ms a
  distinct place (Amazon's 1,257 took 2.0 s), so each place is read once per process, through an
  LRU cache, and at most the 2,000 commonest places of a scan are read.
- A new public read route, `/companies/levels?board=…`, counts each served job once in the Trends
  Level view's band (`role_taxonomy.band`: internships, then 0–1, 2–4, 5–7 and 8+ years, then not
  stated). The critique named the last five; internships are kept because it asked for the Trends
  level bands, and Trends has that band. It is one scan of three columns, the one the locations
  scan makes (`location_counts.scoped_rows`). It is its own route and module
  (`serving/level_counts.py`), because a route named for locations should not answer levels.
- The profile omits a line whose every count is 0, a one-count line such as "remote" too, and
  says it does.

## Alternatives

- **Group copies by the directory company** (both rows' Boards resolving to one directory key). It
  needs a lookup for every row on every page, and it would still miss Eversource: the directory
  holds `radancy:jobs.eversource.com` and `workday:eversource/…` as two companies.
- **Match company names by edit distance or shared words.** It is looser than a prefix of words,
  and "Meta" and "Metaview", or "Bosch" and "Boschung", share letters, not a company.
- **Compare every place in a location, not the first.** A Radancy front and a Workday Board order
  and spell their places differently ("Westwood, Massachusetts" and "Westwood, MA"). The first
  place is the one both put first.
- **Roll places up in the MCP from the top 50 places.** Amazon names 1,257 distinct places and its
  top 300 hold 87% of its jobs, so the tail would drop out of every country's figure.
- **Levels from the Search rail's facets.** The `max_years` options are ceilings, and every one
  includes the jobs that state no experience. Unstated experience cannot be split out of them
  without another count.
- **A served `company_label` on `/search` and `/job` rows.** It would fix the website too, but it
  is a change to the row contract. The MCP-side lookup changes nothing served.

## Consequences

- A page can still group two different employers that share a name's words once generic words
  drop, post the same title, and are placed in the same city and countries. None was found in the
  whole table. The grouping hides no row, since every id stays and differences are printed.
- A search page with a Board that names no company costs one more read, or up to eleven when the
  directory lacks one of up to ten such Boards. A page where every row names its company costs
  none.
- `get_job` spends one `/companies/lookup` per distinct Board among its missing ids, at most five,
  and one `/facets` total for each the directory lacks.
- Agent contract 6, then 8 for the city roll-up: a server built from either refuses a Space still
  serving less until the Space deploys. A merge that changes `deploy/hf-space/app.py` deploys the
  Space (ADR-0290).
