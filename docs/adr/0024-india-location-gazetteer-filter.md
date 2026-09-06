# ADR-0024: India location filter via a query-time gazetteer

- Status: Accepted
- Date: 2026-07-20
- Extends the Space's filter set ([ADR-0020](0020-free-tier-deployment.md)); filter-then-rank
  semantics per [ADR-0008](0008-filter-then-rank-search.md)

## Context

The Space's only location filter was a raw substring — `lower(location) LIKE '%term%'` — over
free-text ATS location strings. A live-index inventory (`experiment/india-location-filter/`,
2026-07-20: 170,480 rows) measured what that costs India, the product's strongest user segment:
24,964 rows are India jobs, but **47% of them never contain the word "india"** — zoho, keka and
ripplehire write city-only strings ("Bangalore North", "Pune City", "Gurgaon Kty."), workday writes
"IN-Pune". Spellings split the rest: Bengaluru/Bangalore is ~50/50 (4,294 vs 5,032, plus typos like
"Banagalore"), Gurgaon/Gurugram likewise. So typing "india" found barely half the jobs (and 196 of
its hits were *Indianapolis*), and typing either city spelling missed the other half.

## Decision

A **hardcoded India gazetteer expanded at query time** — `src/headstart/geo.py`: canonical city →
observed alias substrings (variants, real typos, metro localities), a state list, region bundles
(Delhi NCR), and `where(place)` building the LIKE OR-chain. The Space UI grows an **India dropdown**
(all India + top ~24 cities); the selected canonical is whitelisted against the gazetteer — free
text never reaches this clause. The country clause is the word "india" (minus an `indiana` guard)
OR every city alias OR the state names.

Why this shape:

- **Not semantic.** Embeddings are fuzzy exactly where a filter must be exact — "Pune" sits near
  "Mumbai" in embedding space; a city filter that leaks neighbours is a filter that lies.
- **Not ingestion-time columns (yet).** Derived `city`/`country` columns are the cleaner end state
  but need a schema migration plus full-index backfill for a result users can't distinguish from
  query-time expansion. The gazetteer is the durable asset; it can move to ingestion later.
- **Hardcoding is right-sized.** ~70 vetted entries cover ≥99% of observed India rows; city names
  change on decade timescales; a geo library is a heavy dependency for worse control.

**Every alias must be unambiguous as a substring of any world location string.** The raw inventory
map was contaminated (Salt Lake City, UT counted as Kolkata via "salt lake"), so aliases are vetted,
with traps recorded in `geo.py`'s docstring ("wai" ⊂ taiwan, "verna" ⊂ Governador Valadares,
"punjab" is also Pakistani, …) and NOT-LIKE guards where a good alias has one collision ("surat"
vs "Surat Thani"; "kalyan" vs Pune's "Kalyani Nagar"). Tests (`tests/test_geo.py`) run the real
clauses against a LanceDB table seeded with every trap.

The gazetteer stays a **dependency-free single file**: unit-tested in `src/headstart`, copied by
`deploy-space.yml` next to the standalone Space app (`import geo`) — one source of truth, no twin
to rot.

## Consequences

- Verified against the live index: "all India" → 24,844 rows (was 13,200 for "india"; the 120-row
  gap to the raw 24,964 inventory is the vetoed traps — correctness, not loss); bengaluru → 9,352
  across both spellings + typos + localities. Zero foreign false positives in a country-keyword
  sweep of the matches.
- Latency: city clauses 60–100 ms; the full country OR-chain ~1 s on the laptop (worse on the free
  CPU tier). Acceptable for v1; if it bites, trim the country chain to india-word + high-volume
  cities + states, or promote the gazetteer to ingestion-time columns.
- New India-relevant spellings/localities require a gazetteer edit (regenerate the inventory in
  `experiment/india-location-filter/` and re-vet). Other countries can follow the same pattern.
- The free-text "Location contains" box is unchanged and composes (AND) with the India filter.

## Amendment (2026-09-06): one regex alternation, not 267 LIKEs

The gazetteer's expansion was one `lower(location) LIKE '%alias%'` per alias, OR'd together. For
"india" that is a **10,307-character clause of 267 predicates**, and each one is its own pass over
the column. This ADR already flagged the shape as costly — *"the full country OR-chain ~1 s on the
laptop (worse on the free CPU tier)"* — but that was before ADR-0084's facet strip put the clause
inside ~46 counts per request. Measured on the served table (318,003 rows):

| | before | after |
| --- | --- | --- |
| one `count_rows` with the India clause | 2,669 ms | 350 ms |
| `/facets` with All India selected | **8,670 ms** | **1,314 ms** |
| clause | 10,307 chars, 267 `LIKE` | 3,068 chars, 10 `regexp_like` |

The five-part structure and every collision guard are unchanged; only the compilation is. Each
OR-chain of substring `LIKE`s becomes one `regexp_like` alternation, and since only 2 of 68 cities
carry an `EXCLUDE` guard, the other 66 share a single alternation with the states — which is where
the predicate count actually falls.

**Verified by set equality, not by count.** A faster filter that returns different rows is not a
faster filter, and the first attempt at this returned 96,795 rows against the correct 50,013
because it swept the exclusion terms in as positives. The committed version was checked by
comparing the full set of matched ids, old implementation against new, across **all 70 places the
filter accepts** — "india", every region, every city, including both cities that carry collision
guards. 70/70 identical. `experiment/india-filter-regex/` holds the harness.

Two escapes are now load-bearing where one was before: aliases are `re.escape`d so their own
punctuation is not read as regex syntax (`(ind)` would otherwise be a capture group, and any alias
carrying `.` would become a wildcard), and quote-doubled so the SQL literal stays closed.
`IND_FORMS` keeps its LIKE shape as the source of truth — `test_ind_is_never_a_bare_substring`
asserts anchoring on those constants — and is translated per pattern, with a test pinning that the
anchoring survives the translation.

`where()` is also memoised now: it is a pure function of module constants and `facets` calls it
once per count.

**This does not supersede "not ingestion-time columns (yet)."** A derived country column remains
the cleaner end state and the only route to millisecond-level; the reasoning recorded above for
deferring it — a schema migration plus a full-index backfill — is unchanged. What changed is that
the deferral was justified partly by *"a result users can't distinguish"*, and 8.7 s versus 1.3 s
is distinguishable. This buys most of that back for no migration.
