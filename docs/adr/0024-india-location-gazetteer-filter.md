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
