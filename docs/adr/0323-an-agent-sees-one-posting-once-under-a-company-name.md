# ADR-0323: An agent sees one posting once, under a company's name, and a company's places by country

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md) (per-page copies),
[ADR-0275](0275-an-agent-looks-a-company-up-and-reads-its-hiring-profile.md) (`company_profile`, `/companies/locations`),
[ADR-0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md) (`get_job`),
[ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) (the gazetteer),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (the Company directory)
· **Amended by:** [ADR-0331](0331-a-copy-needs-one-companys-words-and-a-missing-id-gets-one-account.md) —
the copy rule, the naming fallback, the missing-id account and the city roll-up, after the code
review of #897

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

**A copy is the same title under one company, or the same title and first place under two
spellings of it** (`jobs.requisition_copies`, moved and renamed by ADR-0331). ADR-0274's rule stays: the same company and title,
brackets aside. A second rule adds rows whose company names, with their legal suffixes dropped
("Inc", "LLC", "Corporation"), are one the other's first words ("EVERSOURCE" and "Eversource
Energy"). That match is loose, so the rule also needs the same title stem and the same first
place: the words before the first comma of the first place a location names. Two Boards that name
no company are never taken as one company. As before, a copy is listed under the row it repeats,
and every id, link and row number stays.

Measured on 2026-09-29:

- **On 16 live pages of 40 rows** (640 rows, `/search`), the second rule added 2 pairs, both true
  copies: Eversource's Radancy front and Workday Board, and L3Harris's SuccessFactors Board and
  Radancy front. It removed none of the old rule's 74 pairs.
- **Over the whole served table**, 3,285 row pairs share a title stem and first place under two
  spellings of one company, across 150 spelling pairs ("Booz Allen" and "Booz Allen Hamilton",
  "L3Harris" and "L3Harris Technologies", "Staples Inc." and "Staples, Inc."). Reading all 150,
  one is wrong: a GE Vernova row served as "GE" beside "GE HealthCare". One more is unclear
  ("Flow" and "Flow Traders").

**A company named only by its Board is shown by the Company directory's name**
(`space_mcp.company_names`). A served name counts as naming no company when it is empty, or when
it is lowercase, holds a `.`, `/`, `:` or `@`, and `company_name.echoes_board` reads it as the
Board's key, host, URL or path. That rule already decides the directory's names
(`board_naming.stated_name`). "Checkout.com" on `ashby:checkout.com` is cased, so it stays a name.
For such rows `search_jobs` and `get_job` make one `/companies/lookup` call over their distinct
Boards, at most ten. One Board the directory lacks refuses the whole lookup, so the Boards are then
asked one at a time. The row shows the label, marked `(directory name)`; a Board the directory
does not name either reads "no company name", never its host. The lookup is a courtesy: when the
Space cannot answer it, the served names stay.

**An id not in the index now "has closed, or was never an id".** `get_job` reads each missing id's
Board (all but the id's last colon-separated part) and asks `/facets` for that Board's total
alone. If the Board serves no job, the answer says the id is not a HeadStart id and names the
Board. If the count fails, only the plain sentence is said. `/search?like=` on an id the table
lacks now says the same (`JobSearch._stored_vector`).

**A cut description names the limit that cut it.** When the shared budget cut it, the answer
says "ask for this id alone". When `max_chars_per_job` did, it says how far to raise it, and that
figure never goes past the shared budget.

**A company profile counts places by country, and levels by band.** Both are served additions,
so the agent contract goes to 6:

- `/companies/locations` adds `countries`: each country's jobs and its three commonest places as
  written, from the `country` filter's own gazetteer. So "Ireland 23" is what `country=IE` would
  count. It also adds `no_country`, the jobs whose place names none, with their places, and
  `places_unread`. A job naming two countries counts in both. The gazetteer costs about 1.6 ms a
  distinct place (Amazon's 1,257 took 2.0 s), so each place is read once per process, through an
  LRU cache, and at most the 2,000 commonest places of a scan are read.
- A new public read route, `/companies/levels?board=…`, counts each served job once in the Trends
  Level view's band (`role_taxonomy.band`: internships, then 0–1, 2–4, 5–7 and 8+ years, then not
  stated). It is one scan of three columns, as the locations scan is. It is its own route and
  module (`serving/level_counts.py`), because a route named for locations should not answer
  levels.
- The profile omits a facet line whose every count is 0, and says it does.

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

- A page can group two different employers when one's name begins the other's, they post the same
  title, and their locations start with the same city. One such pair was found in the whole table.
  The grouping hides no row, since every id stays and differences are printed.
- A search page with a Board that names no company costs one more read, or up to eleven when the
  directory lacks one of up to ten such Boards. A page where every row names its company costs
  none.
- `get_job` spends one `/facets` total per distinct Board among its missing ids, at most five.
- Agent contract 6: a server built from this change refuses a Space still serving 5 until the Space
  deploys. A merge that changes `deploy/hf-space/app.py` deploys the Space (ADR-0290).
