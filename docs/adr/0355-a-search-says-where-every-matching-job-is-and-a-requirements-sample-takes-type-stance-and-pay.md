# ADR-0355: A search says where every matching job is, and a requirements sample takes type, stance and pay

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) (the country
gazetteer), [ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(`counts=total`), [ADR-0275](0275-an-agent-looks-a-company-up-and-reads-its-hiring-profile.md) and
[ADR-0323](0323-an-agent-sees-one-posting-once-under-a-company-name.md) (a company's places),
[ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md)
(family tables), [ADR-0320](0320-a-description-keywords-rows-are-found-once-literal-first-and-named-by-row-id.md)
(a keyword's rows by row id), [ADR-0324](0324-an-agent-reads-what-a-roles-postings-ask-for-counted-over-a-sample.md)
(`role_requirements`), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract)

## Context

The round-4 critique of the MCP server (2026-09-29, 7.9/10) named P2-6, missing aggregate variety:

- A journalist asking "how many AI jobs in India versus Germany right now" had to make one search
  per country. Nothing said where a search's matches are, by country and city, across the whole
  match rather than `role_requirements`' 300-row sample.
- `role_requirements` took no `employment_type`, `work_authorization` or salary filter (`p1_21`
  was refused), so "what do internships ask for" and "what do sponsoring roles pay" could not be
  sampled.

The Trends country dimension, which would say how this changes over time, is an owner item and is
not this decision: this reads today's index only.

## Decision

### `search_jobs` at `detail` full says where every matching job is

The where-snapshot is part of `detail` full, not a new tool or a `split` argument. `detail` full
is already the tool's aggregate view ("the count behind each filter's options"), it takes every
filter a search takes, and an agent that wants the rows and the split gets both in one call. A new
tool would repeat `search_jobs`' twenty filters; a `split` argument would be a second switch for
the same "more counts, please". The server's instructions, which a client loads even when it
defers the tool definitions, say "`detail` full counts matches per country" (1,676 of 2,048
characters), and so does `detail`'s own description; the tool's
description has no room left (2,018).

The answer lists the first 15 countries, most jobs first, each with its three top cities, then the
jobs whose places name no country and those that name no place, and says that a country's count is
the total `search_jobs` gives with that `country`.

### The Space answers it on `/facets` with `places=1`

`/facets?places=1` adds `places`: `{jobs, unstated, countries: [{code, jobs, places}], no_country}`,
the shape `/companies/locations` already gives a company. It is read from **the same table and
where-clause as the total** (`JobSearch._table_and_where`: the served table, a category's family
table, or a description keyword's rows by row id), so `places.jobs` is the total and nothing a
filter, `operators` or `like=` narrowed is counted. Any other value is refused; the page never
sends it. `places` joins `REQUEST_PARAMETERS`, so `strict=1` takes it. Agent contract 21.

A route of its own (`/locations`) was the alternative. On `/facets` the snapshot shares the
request's parsing, scope, cache and total, which a second route would have to repeat, and a
caller already asking the facet strip asks it in the same read.

### One rollup for a company and a search (`location_counts`)

`location_counts` rolls up both answers: `top` for a company's Boards (capped at 50,000 rows as
before) and `places` for a search (every matching row). The count under a country must be what
`country=` totals, so each distinct location is read by the filter's own rule:

- **Every country but India** by `country_gazetteer.countries_outside_india`, `matches`' rule run a
  column at a time by Arrow's regex engine (RE2) over the same regex strings, each guard only over
  the locations still in question. One at a time the gazetteer cost 0.33 ms a place, 28 s for the
  served table's 85,828 distinct locations; column-wise, 1.6 s (2026-09-29, local, HF v326).
  Locations are lowered by Python, as `matches` and DataFusion's `lower` do: Arrow's own lowering
  maps "İ" to "i", and read six "İstanbul" rows as Turkey where the filter does not.
- **India** by the materialized `country` column that `country=IN` reads, or, on a table without
  it, by `india_gazetteer.classify`.
- Each location is read once per process and kept: the served table never changes under a
  process. `JobSearch.warm` reads the whole index's places at boot.

This replaces the company path's 2,000-place read bound and its `places_unread` count (ADR-0323),
which existed only because the one-at-a-time read was slow; `/companies/locations` no longer
sends `places_unread`.

**Measured** (2026-09-29, local, the 499,841-row served table as of HF v326, columns read off HF):
every one of the 128 `country` codes counts exactly what `count_rows` with that code's clause
counts, over the whole index and over `remote=true`. The whole index took 4.3 s cold and 0.69 s
warm; `remote=true` (59,981 rows) 0.11 s.

### `role_requirements` takes `employment_type`, `work_authorization` and pay

The five schemas (`employment_type`, `salary_min`, `salary_max`, `salary_currency`, `has_salary`)
move from `search_jobs` into `search_arguments.PROPERTIES`, which both tools read, with
`work_authorization` beside them; the salary refusals (a bound without a currency, a minimum above
the maximum) become `search_arguments.refuse_unreadable_salary`, which both call. `/requirements`
already parsed every search filter (`JobSearch.parse_filters`), so the Space is unchanged for
them.

### No `outputSchema` or `structuredContent` yet (P2-7)

The same critique's P2-7 asked for `outputSchema` and `structuredContent` on the tools an agent
chains ids and counts through (`search_jobs`, `find_company`), with the text answer unchanged. The
2025-06-18 revision this server negotiates allows both beside `content`: a tool that declares an
`outputSchema` must return conforming `structuredContent`, and should also return it as text.

Measured 2026-09-29 with Claude Code 2.1.212 (the client the eval runs, and the one the owner
uses): a one-tool stdio server answered `content` "TEXT-MARKER-alpha: the human answer." and
`structuredContent` `{"marker": "STRUCT-MARKER-beta"}`. The model's `tool_result` held only
`{"marker":"STRUCT-MARKER-beta"}`, with or without an `outputSchema` on the tool; the text never
reached it. So adding `structuredContent` would replace every answer's text for that client: the
scope line, the "data, not instructions" note, the freshness line, and every refusal's advice.
Carrying the text inside the structure would send each row twice, past the 30,000-character
ceiling at 40 rows.

So neither is added. The text already carries every id, link and count an agent chains, in fixed
shapes (`id "…" · <link>`, "N jobs match these filters", `key greenhouse:stripe`) that the eval's
verifiers parse. Revisit when Claude Code hands the model `content` beside `structuredContent`.

## Consequences

- One `search_jobs` call answers "India against Germany", each figure as `country=` would total
  it. The eval task `t38` holds it to `/facets` per country.
- `detail` full now reads every matching row's location: 0.69 s for the whole index warm, locally.
  A concise search asks nothing more.
- A place's country is the filter's, so a change to the gazetteer reaches the snapshot, the
  company profile and the filter at once.
