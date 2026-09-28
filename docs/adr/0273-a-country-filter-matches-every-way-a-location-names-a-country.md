# ADR-0273: A country filter matches every way a location names a country

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0024](0024-india-location-gazetteer-filter.md) (the query-time gazetteer this extends to the
world), [ADR-0138](0138-a-materialized-country-column-serves-the-india-filter.md) (the materialized
`country` column `country=IN` reuses), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract and `strict=1`), [ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md)
(the MCP server that asked for it)

## Context

HeadStart's scope is global, but only India had structured places: the India filter's 70-value
gazetteer (ADR-0024). Everywhere else a searcher typed a `location` substring. The round-1 critique
of the MCP server (P1-3, 4.5/10) measured what that costs: "United States" and "USA" return
different jobs, and "Germany" misses a row located "Hamburg, Berlin".

Measured on the served table (v298, 498,848 rows, read straight off HF on 2026-09-29), a country is
written many ways, and its English name is the minority form for most of them:

- The United States is "United States", "United States of America", "USA", "US", "U.S.",
  "Remote - US", "US-CA-Santa Clara", a state ("Texas", "TX") or a city alone ("Austin",
  "Littleton"). The name "united states" is in 129,809 rows; the United States is named by
  225,064.
- Two-letter codes collide. "CA" is California ("San Francisco, CA", 610 rows) and Canada
  ("Toronto, ON, CA", 445). "IN" is India ("Bangalore, IN", 457) far more than Indiana. "DE" is
  Germany ("Berlin, DE") and Delaware ("Wilmington, DE, US"). "IL" is Illinois and Israel ("Tel
  Aviv, 05, IL"). "HR" is Haryana ("Gurugram, HR, India"), not Croatia. "MD" is Maryland and the
  Madrid region ("Madrid, MD, ES"). "(fr)" and "(jp)" are language tags.
- Names nest inside other names: New Mexico, Baja California, Northern Ireland, New South Wales,
  "Taiwan, Province of China", "Beth Israel Lahey Health" (a Boston hospital), "USA - New York -
  Malta" (a New York fab).
- City names are shared: London (Ontario), Dublin (Ohio), Vienna (Virginia), Melbourne (Florida),
  Paris (Texas), Berlin (Connecticut).

The India filter's column (`country`, ADR-0138) holds only "IN". Filling it for the world would be
a served-table schema change, which needs the owner.

## Decision

**A query-time `country` Search filter**: one ISO 3166-1 alpha-2 code, compiled to a match on
`location` from a world gazetteer. No schema change.

**The pair of modules, in the India pair's shape.** `search_filters/country_gazetteer.py` holds the
places and compiles them; `search_filters/country_filter.py` is the filter's face: the codes it
accepts (`CODES`, 94), their names, and the clause `build_filter` compiles. `country=IN` is the
India filter's whole-country rule, `india_filter.clause("india", has_country)`, so on the served
table it is `country = 'IN'` and can never disagree with `india=india`. India's names still guard
every other country (below), so "Chennai, TN, India" is not Tennessee.

**Each country names its places two ways, in two strengths.**

- A *word* matches anywhere, bounded by anything that is not a letter or a digit.
- A *segment* matches only a whole part between separators: `, ; | / ( ) :`, a hyphen with a
  space beside it, or a bare hyphen at either end of the string ("US-Remote", "Remote-US"). A
  hyphen inside a name does not separate, so the "la" of "Louvain-la-Neuve" is not Louisiana, and
  whitespace never does, so the "de" of "Rio de Janeiro" is not Germany. Codes are segments, and
  so is a name whose word form is a trap: "mexico" as a segment is Mexico, and "New Mexico" is
  not.
- A *sure* term names the country whatever else the row says.
- A *shared* term also names another place. It counts only when the row names no other country for
  sure, and a shared word also yields to another country's shared segment. So "Vienna, VA" is
  Virginia, "Dublin, OH" is Ohio, "Toronto, ON, CA" is Canada and not California, and "Luxembourg,
  Luxembourg (fr)" is not France. Every two-letter code but "us" is shared.

No term belongs to two countries (a test enforces it): a term both claimed would put its bare rows
in both. Where a code or name is contested, the data picked its owner: "id" is Idaho (Jakarta rows
name Jakarta), "sa" is Saudi Arabia ("Riyadh, SA", 46 rows; "Adelaide, SA" names Adelaide), "nl"
is the Netherlands, and "sk" and "pe" belong to no one.

Coverage is every country named by at least 50 served Jobs (93 plus India). Below that the rows are
mostly lists of every country a remote job allows. The terms were drawn from the data: for each
country, the most frequent other parts of the rows that name it, then the most frequent rows no
country placed, then every row two countries claimed. On the full table 95.5% of rows are placed;
the rest are mostly "Remote", "Hybrid", regions ("EMEA", "LATAM") and small US towns.

**The clause is at most five `regexp_like` passes** over `lower(location)`, one per alternation
(ADR-0024's single-automaton rule): `sure OR ((shared segment OR (shared word AND NOT another's
shared segment)) AND NOT another's sure name)`. The same regex strings drive
`country_gazetteer.matches`, the Python rule, so the SQL and the Python can disagree only where
DataFusion's regex engine and Python's do. Every term is a lowercase constant without quotes;
nothing a request sends reaches the SQL beyond a dict lookup.

**Where it reaches.**

- `SearchFilters.country`; `JobSearch.parse_filters` reads `country` and upper-cases it. An unknown
  code is dropped with one log line, or, under `strict=1`, refused with a 400 that names every
  supported code.
- `facets` can name it as the Blocking filter, like any field. The page's filter rail gains a
  Country dropdown beside the India one (`search.html`, `app.js` `LABELS` and `CONTROL`), so the
  page and the MCP tool stay at parity. A Subscription or a Saved Set carries it
  (`ALLOWED_SEARCH_FILTERS`).
- MCP `search_jobs` gains a `country` argument: an enum of the 94 codes, sent as `country`, named in
  the scope line. `location` and `india_place` stay.
- The agent contract is now 3 (`_AGENT_API_VERSION`, `space_client.AGENT_API`): a Space older than
  this would silently ignore `country` and widen the search to the whole world.

## Measured

**Precision and recall on a labelled sample.** 400 locations drawn with seed 273, none of them
chosen by the gazetteer: 200 rows weighted by Jobs (what a searcher meets) and 200 distinct
strings weighted equally (the long tail). I labelled each by reading the string alone, before
running the gazetteer; 2 were undeterminable ("Benson", "city, state, GE") and left out. 398 rows
carry 429 country labels across 60 countries. Scored against the old way, the country's English
name as a `location` substring:

| | Precision | Recall |
| --- | --- | --- |
| Country filter, all 398 rows | 0.995 (415/417) | 0.967 (415/429) |
| Country filter, the 199 Job-weighted rows | 1.000 (193/193) | 0.970 (193/199) |
| Name substring, all 398 rows | 0.976 (285/292) | 0.664 (285/429) |

Per country (true positives, false positives, misses): US 211/2/3, IN 37/0/2, CA 18/0/0, GB 16/0/1,
MX 11/0/0, PH 8/0/0, PL 7/0/0, DE 6/0/0, CN 6/0/0, BR 6/0/0. The 14 misses: small places no
gazetteer names ("Waluj", "Vikhroli", "Burwood", "Rueil-Malmaison", "TRUTNOV", "Amarillo",
"WINSTON SALEM", "Negambo"), "Home, D.C.", and the rule's stated cost: a row naming several
countries loses one named only by a shared term ("Singapore, Paris, Athens" is Singapore only;
"London, EMEA, GB; Paris, EMEA, FR; Lille, EMEA, FR" is France only; Peru in a list of South
American countries). The last miss is also one of the 2 false positives: "Cilegon, ID" is
Indonesia read as Idaho. The other is "Tbilisi, Georgia, Georgia", the country read as the state. The numbers were measured once, after the
gazetteer was frozen; no term was added from the sample.

**On the whole table, against the name substring:** US 225,064 rows against 129,809 for "united
states" (1.73x), GB 23,361 against 17,597, CA 16,999 against 12,587, DE 8,395 against 5,526 (1.52x),
AU 6,343 against 4,464, FR 3,134 against 2,520, JP 3,345 against 2,637, BR 3,116 against 2,531.

**SQL agrees with Python.** For US, DE, GB, CA, IN, JP, AU, IE, MX and CN, the ids the SQL clause
selects on all 498,848 rows (pylance 11) equal the ids `matches` selects, with none on either side
alone. `test_where_agrees_with_matches_on_a_real_table` repeats this for every code on the test
rows, through lancedb.

**Cost.** Local copy of the served table's scalar columns, lancedb 0.33, medians of 5 (3 for the
strip):

| Filter | One count | The `/facets` strip |
| --- | --- | --- |
| none | 0 ms | 39 ms |
| `location=united states` | 29 ms | 155 ms |
| `india=india` (materialized) | 3 ms | 60 ms |
| `india=bengaluru` | 36 ms | 229 ms |
| `country=US` / `DE` / `GB` | 419 / 386 / 377 ms | 2,665 / 2,650 / 2,589 ms |
| the India gazetteer, unmaterialized | 541 ms | |

The cost is compiling the regexes, not scanning: DataFusion compiles a `regexp_like` pattern once
per batch, and the guard alternation (every other country's sure names, 11,393 characters for US)
takes about 3 ms to compile against 1 ms for the country's own. A country count costs what the
India filter's country-level case cost before ADR-0138 materialized it.

## Options rejected

- **Fill the `country` column for the world** (a list column, or one per country). It would make a
  country count cost what `country = 'IN'` does, 3 ms. It is a served-table schema change, which
  needs the owner, and a gazetteer fix would then reach stored rows only on the next metadata
  sweep. It is the follow-up if the facet strip's cost matters.
- **A guard of country names only**, without other countries' cities. About half the compile cost,
  but "Madrid, MD, ES" becomes Maryland and "Chennai, TN" Tennessee: the guard is what keeps a
  two-letter code honest.
- **A `CASE` expression**, so the guard runs only on rows a shared term matched: Lance's filter
  parser does not accept `CASE` ("is not supported SQL in lance").
- **The regex operators `~` and `~*`**: not supported by Lance's filter parser either. The
  case-insensitive flag instead of `lower()` made the guard slower (749 against 234 ms).
- **Every country in the world.** Below 50 Jobs a country is mostly a name in a remote-anywhere
  list, and each added name enlarges every other country's guard.

## Consequences

- An agent or a searcher asks for a country once, not every spelling of it. `location` stays for
  a place the gazetteer does not know, and `india_place` for an Indian city.
- A country's first count and its facet strip are slow: about 0.4 s and 2.6 s locally. The facet
  cache (`FACET_CACHE_TTL_SECONDS`) serves repeats.
- A new term must stay unique across countries, and a new country enlarges every guard. The report
  scripts that built the gazetteer are not committed (experiments stay local); their method is
  above.
- Tests: `tests/test_search_filters_country_gazetteer.py` (hygiene, uniqueness, 48 real rows with
  their traps, SQL against a real table), `tests/test_search_filters_country_filter.py`,
  `test_search_filters_compiler.py`, `test_serving_job_search.py` (parse, warn, `strict=1`
  refusal), `test_space_mcp_server.py` and `test_space_mcp_against_space_app.py` (the argument end
  to end). The MCP eval gains iteration task t16; the sealed held-out tasks are untouched.

## Follow-ups

- The live search-filter harness (`scripts/eval/verify_filters.py`) learns `country`.
- Materializing countries, if the owner accepts the schema change.
- The critique's other geography asks: a location or country facet (a per-country breakdown in
  `detail: "full"`), and `location` taking alternatives.
