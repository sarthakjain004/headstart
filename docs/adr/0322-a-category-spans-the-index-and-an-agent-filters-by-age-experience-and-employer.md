# ADR-0322: A category spans the whole index, and an agent filters by age, required experience and employer

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(the category hand-off, capped at 5,000 ids),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (the Board hand-off it rides),
[ADR-0268](0268-a-served-posting-date-is-never-later-than-first-seen.md) (the posted date the age
filter reads), [ADR-0018](0018-experience-seniority-fallback.md) (required experience, stated or
estimated from seniority), [ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md)
(the gazetteer), [ADR-0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md)
(`similar_to`), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract)

## Context

The round-2 critique of the MCP server (2026-09-29, 6.0/10) left these search gaps:

- **P1-5, no category across the index.** `search_jobs` refused `category` without `company`, so
  "ML jobs in Germany" could only rank all 8,393 German jobs with the ML ones first. The Space has
  no family column: a family is the set of ids the role-assignment snapshot gives it
  (`load_family_ids`), and the only way in was `family=` beside `board=`, handed over as
  `id IN (…)` and capped at `MAX_FAMILY_IDS`, 5,000. The families run to 69,456 ids
  (software-engineering, snapshot of 2026-09-29).
- **P1-6, stale rows first.** "software engineer" in Canada led with three Zoho postings from
  2022–23; newest-to-HeadStart "kubernetes" led with one posted 2022-07-29.
- **P1-9, no floor on required experience.** `max_years` is the user's own experience, a ceiling;
  nothing kept "roles needing 8+ years".
- **P2.** No way to keep `similar_to` from answering with one employer's jobs (the risk ADR-0277
  names); "UK", "USA" and "UAE" refused as countries; and four gazetteer mistakes:
  `classify('Berlin, CT') = {'DE'}`, "East Berlin, CT, US" in DE and US, "Hyderabad, Sindh,
  Pakistan" in IN and PK, "Perth, Scotland" in GB and AU. The review of ADR-0273's PR (#867)
  added a fifth: "Tbilisi, Georgia" was the US state.

## Decision

### A category across the index reads an in-memory family table

`family=` without `board=` is a category across the whole index. `JobSearch` holds a
`FamilyTables` (set by the app, and by the local renderer, once each has loaded the role
assignments): for each family, on its first request, one streamed scan of the served table keeps
that family's rows with every column but the vector and the description, as an in-memory LanceDB
table, a *family table*. It is kept for the process: the served table never changes under it. A
family already built is read without waiting on another family's build.

- **Counts** (`/facets`) count the family table with the request's own filters, so the total and
  every option's count are exact and none names an id.
- **A browse** (no query) lists from the family table, in any order the page offers.
- **A ranked search** (`q=` or `like=`) needs the vectors, so the family table names the rows
  that match the filters and the served table ranks exactly those, by `id IN (…)`.
- **A description keyword** is matched on the served table, its rows found once by row id
  (ADR-0320; a family table has no description); the request reads the family's rows it
  matched, with the other filters. The
  answer's description coverage is counted over the whole family table, and when nothing matches
  the keyword is named as the Blocking filter if lifting it recovers more rows than lifting the
  filter the counts named.

`family=` beside `board=` is unchanged (ADR-0185's ids on those Boards, under the 5,000 cap).
`strict=1` refuses an unknown family, or a deployment without the assignments, as before; it no
longer refuses a family without a Board. The MCP tool drops its "category needs company" refusal.
The page never sends `family=` alone, so it is unchanged.

### Three filters only an agent sends

All three are `SearchFilters` fields, parsed by `JobSearch.parse_filters`, compiled by
`build_filter`, and so applied identically to the rows and the counts:

- **`max_age_days`**: posted within that many days, reading the posted date where it is readable
  (ADR-0173's guard) and the day HeadStart first saw the Job where it is missing or not. A served
  posted date is never later than first seen (ADR-0268), so the fallback never makes a Job look
  older than its own date says. A Job with neither is left out, and `strict=1` refuses the filter
  on a table without `first_seen`. **The MCP tool sends 365 unless told otherwise**, and its scope
  line says so ("posted in the last 365 days, the default; send max_age_days 0 for any age (a job
  with no readable posted date counts from its first-seen day)"); 0 sends nothing. The server's
  instructions now say a search leaves out postings over a year old. The website keeps its own
  behaviour.
- **`required_years_at_least`**: a floor on the Job's required experience (`min_years`, CONTEXT.md
  **Required experience**): at least N years, as stated, or where none is stated as estimated from
  the title's seniority ("Senior" reads as 5, ADR-0018). A Job whose required experience is
  unknown cannot meet it and is left out; the scope line says so. Named for what it reads so it
  cannot be taken for the user's experience, which stays `max_years`.
- **`exclude_company`**: the company box negated, no company name containing the text; a row with
  no company name is kept. A substring rather than a Board exclusion, because the same employer is
  served under several Boards and spellings — Eversource is 74 rows as "Eversource Energy" on
  Workday and 79 as "EVERSOURCE" on its Radancy front — which one substring removes and a Board
  key would not, unless the directory happened to join them. Its cost is the company box's own: it
  is a substring, so "meta" also removes any name containing it, and the scope line says "no
  company name containing …". It reads the served company name, so a row the answer shows by its
  Board's directory name because its own is only a host (ADR-0323) is not removed by that name.

They are agent-only (`facets.AGENT_ONLY`): the Blocking filter may name them, since an agent's
answer names the filter as the agent spelled it, but the page has no control for them. The MCP's
empty answer says "send max_age_days 0" when the age window is what leaves nothing.

### A country as it is written

The MCP tool reads `country` through `country_filter.code_for` before the schema check (the
`argument_readers` hook, as `category` does): a code in any case, the country's English name, or a
common abbreviation ("UK", "USA", "UAE", "KSA"). "America" is not read, since it also means the
Americas. Anything else still meets the enum's refusal. The Space's `country=` stays a code.

### Five gazetteer fixes

- **Berlin is a *city word*, a new strength.** A city whose name a smaller place in another
  country shares counts unless the row states another country, by its English name or one of its
  codes. So "Berlin, CT" and "New Berlin, Wisconsin, United States" are the US, while "Berlin;
  Montreal" stays Germany and Canada: unlike a shared word, a city word never yields to another
  country's city. Three sure terms keep Berlin's own forms German: "berlin, berlin", "berlin, be"
  and "berlin, de". A country with a city word compiles to at most seven `regexp_like` passes, the
  rest to five as before.
- **Perth is a shared word** (a US state code or another country named for sure takes it), with
  "perth, wa" and "wa, au" sure for Australia, since "WA" is also Washington's code.
- **Georgia the country is supported** (named by 156 served Jobs), by its cities: "Tbilisi,
  Georgia" is Georgia, and "georgia" stays the US state's shared word, which those cities guard.
  The filter now accepts 95 codes.
- **Hyderabad's India alias yields to "pakistan" or "sindh"** (`india_gazetteer.EXCLUDE`), which
  `where` and `classify` both read, so `india=hyderabad` and the Python rule change at once.

**The served `country` column is not corrected by this change.** `country=IN` reads that
materialized column (ADR-0138), and `update_meta` re-derives it only on a `DERIVATIONS_VERSION`
sweep or when a row's location changes. On v499, 14 served rows are `IN` while naming Hyderabad
with Pakistan or Sindh; 12 of them name India nowhere else and will leave `country=IN` on the next
sweep, which the next fix to `experience.py` or `salary.py` brings with its own bump. This change
does not bump it: a bump re-derives every stored row, for 12.

The agent contract is now 9 (`_AGENT_API_VERSION`, `space_client.AGENT_API`): an older Space would
silently ignore a whole-index `family=` and the three filters, and widen the answer.

## Measured

On a copy of the served table (v499, 498,539 rows) and the role assignments of 2026-09-29,
lancedb 0.36 as the Space pins it, medians of 3–5 on a laptop:

**The IN-list does not scale.** Its cost is parsing the where-clause, about 25 ms per 1,000 ids:

| `id IN (…)` of | count | ranked page | browse page |
| --- | --- | --- | --- |
| 1,000 ids | 49 ms | 151 ms | 134 ms |
| 10,000 ids | 269 ms | 419 ms | 411 ms |
| 69,445 ids (software-engineering) | 1,781 ms | 1,784 ms | 1,816 ms |

With every software-engineering id, the facet strip took 24–34 s, and a count with `country=DE`
3.0 s.

**Post-filtering a ranked window is wrong.** Of the 2,000 rows closest to an ML posting's vector,
1,464 were in ai-ml-data-science, 142 in software-engineering and 36 in data-engineering, so a
category the query does not match would list a few rows under a total of thousands.

**The family table is exact and fast.** Building software-engineering's took 529 ms at 65,536
rows a batch (2,176 ms in the default batches); it is 25 MB, and ai-ml-data-science's 13 MB. Then:

| software-engineering | total | `/facets` total | full strip | browse | ranked | ranked + sort |
| --- | --- | --- | --- | --- | --- | --- |
| no other filter | 69,445 | 0.9 ms | 69 ms | 21 ms | 1,641 ms | 1,416 ms |
| `country=DE` | 1,234 | 129 ms | 945 ms | 482 ms | 539 ms | 594 ms |
| `country=US` | 28,506 | 139 ms | 1,217 ms | 534 ms | 1,098 ms | 1,291 ms |

ai-ml-data-science with `country=DE` (795 jobs): 74 ms, 568 ms, 261 ms, 353 ms, 345 ms. Every
total equalled the family's ids the country rule matches, counted separately in Python, and every
row listed was in the family and the country. The ranked path still pays the IN-list for the
family's matching rows, so a whole category with no narrowing filter ranks in about 1.6 s; any
filter that narrows it brings that down.

**The filters.** `max_age_days=365` keeps 459,017 of 498,539 rows and leaves out 39,522 (7.9%);
24,607 rows are read by first seen and 101 have no age. A count with it is 19 ms. 424,878 rows
(85.2%) have a required-experience figure; the floor keeps 224,806 at 5 years, 60,842 at 8 and
37,664 at 10, 12–13 ms a count. `exclude_company` counts in 36–38 ms.

**The gazetteer.** Against `origin/main`'s, 203 served locations (366 jobs) change countries.
Georgia gains 156, which the US loses 60 of; the US also stops claiming 27 Western Australian
jobs ("Fremantle, WA, AU"). Germany loses 142: 98 in US Berlins (Berlin, CT; New Berlin, WI;
Berlin, NJ; East Berlin, CT), and 44 in lists that also state another country or its code
("Paris, Berlin, Barcelona, Milan, FR", "Noida; Berlin; India"). India loses 12 Pakistani jobs
naming Hyderabad. Australia loses 12 (Perth in Scotland, Perth Amboy, NJ) and gains 8.

On ADR-0273's labelled sample (398 rows; row 374, "Tbilisi, Georgia, Georgia", relabelled
Georgia now that Georgia is a code), precision is 0.998 (416/417) against 0.995 on `main`, recall
0.965 against 0.963. With the critique's four and "Tbilisi, Georgia" added: precision 0.998
(421/422) against 0.984, recall 0.966 against 0.959.

## Alternatives

- **An uncapped `id IN (…)`.** Correct, and 1.8 s a statement and 24 s a strip for the largest
  family; above.
- **A ranked window filtered to the family.** 130 ms, and wrong; above.
- **A `family` column in the served table.** A plain equality, the fastest of all, and a
  served-table schema change, which needs the owner. The family tables are the answer until then.
- **Keeping every family's table built at boot.** About 140 MB at once and 25 scans before the
  first request; built on first use, a family nobody asks for costs nothing.
- **A Board exclusion for `exclude_company`.** Exact for one Board, and it misses the same employer
  on another ATS unless the directory joined them (Eversource, above).
- **Berlin as a shared word**, the strength ADR-0273 already had. It moved the 98 US-Berlin jobs
  too, but a shared word also yields to another country's city, and 76 jobs in multi-city lists
  ("Berlin; Montreal", "Berlin, Barcelona") left Germany against 44 with the city word.
- **A `DERIVATIONS_VERSION` bump** to carry the Hyderabad fix into the served column at once would
  re-derive every row for 12 of them.

## Consequences

- "ML jobs in Germany" is one call: `category`, `country`, `query`. The first request for a family
  in a process takes about half a second more (on the laptop) while its table is built.
- An agent's search leaves out postings over a year old unless it says otherwise, and says so;
  the website's does not.
- "Roles needing 8+ years" and "not at this employer" are arguments.
- Follow-ups: the page's Board-and-category hand-off could read the family tables too and lose its
  5,000-id cap; a whole category ranked with no narrowing filter still pays a 69,000-id IN-list.
- Tests: `test_search_filters_compiler.py` (the three clauses), `test_serving_job_search.py` (a
  real table: category browse, ranked and description-keyword paths and the keyword's Blocking
  rule, the age fallback on every date shape, the floor, the exclusion, the strict refusal),
  `test_serving_facets.py`, `test_search_filters_country_filter.py`,
  `test_search_filters_country_gazetteer.py`, `test_search_filters_india_gazetteer.py`,
  `test_space_mcp_server.py` and `test_space_mcp_against_space_app.py`. The live filter harness
  (`verify_filters.py`) checks every new filter, and each country case against hand-labelled
  traps as well as the gazetteer's own rule. The MCP eval gains iteration tasks; the sealed
  held-out tasks are untouched.
