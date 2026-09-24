# ADR-0216: A Workday Board is named by its postings' legal entities, checked against its own page

**Status:** accepted · **Date:** 2026-09-25 · **Builds on:**
[ADR-0212](0212-a-board-is-named-by-a-curated-stated-or-humanised-name-never-its-slug.md) (the
curated → stated → humanised order this fills the "stated" step of, for Workday) · **Relates to:**
[ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (why Workday had no stated
name), [ADR-0099](0099-a-404d-workday-detail-falls-back-to-the-public-pages-json-ld.md) (the JSON-LD page
fallback)

## Context

On served table v121 (2026-09-24) 4,175 Workday Boards, 96,334 rows, showed a host, a path or a
tenant slug as `company` (`nvidia.wd5.myworkdayjobs.com/nvidiaexternalcareersite`,
`ag-wd3-airbus`, `caci`). Workday's listing names no company and its board page is a
client-rendered shell. ADR-0114 recorded the detail as carrying no name either. It does: the CXS
detail JSON has a top-level `hiringOrganization {name, url}` beside `jobPostingInfo`, present on 140
of 140 Boards sampled and non-empty on 91.5% of their details, and the public job page's JSON-LD
states the same value. `WorkdayScraper._extract_detail` read only `jobPostingInfo`.

The value is the posting's legal entity, not the brand, and it varies within a Board: "2100 NVIDIA
USA", "IL00 Mellanox Technologies, Ltd.", Airbus's nine entities, Northrop Grumman's division codes
("0090 CORP-Corporate Office"). Served raw, it would be worse than the slug.

## Decision

**Every Workday Board runs a clean → check → vote cascade** (`scrapers/workday_company_name.py`) over
the `hiringOrganization` values its detail pass already fetched, with one board-page GET:

1. **Clean** each value: drop a dba prefix, a leading entity code (`2100`, `QLYS_IN`, `LE30006`,
   `ADUS-`, `TLSM -`), a leading "The", trailing legal forms and a trailing country. Office and
   placeholder values ("Corporate Office", "Default Company") are dropped.
2. **Check** the longest leading word-run: it must appear as a proper noun (a capital or a digit)
   in the page's `og:title` or `og:description`, taking the page's spelling, or have 4+ of its
   letters inside the Board's `{tenant}/{site}`. No lone generic word, no run ending on of/and/&.
3. **Vote**: the top run wins at 40% of named postings, or as the only checked run.
4. **Fall back** to an `og:title` "Careers at X" / "X Careers" wrapper, then an `og:description`
   opening "X is a…", "At X,", "About X". Every candidate goes through `from_title`'s guards.

**Names are per site, never per tenant.** 12 of 20 multi-site tenants sampled host different
companies per site (volarisgroup, veralto, humana's CenterWell site).

**The answer is cached per Board in a committed file**, `data/validate/company_names/workday.csv`
(`board_key,name,source,checked_at`), written by `scripts/validate/workday_company_names.py`.
`WorkdayScraper.resolve_company` reads it first; a Board on file costs no request and keeps its
name from run to run. A Board not on file (a new landing) runs the cascade live each run until the
script is re-run. Considered and not chosen: an HF state ledger written by `scrape_join`. It would
cache new Boards automatically, but needs plumbing across three stages, a first answer is frozen
from whatever tech postings one run saw, and nobody reviews it. A committed file is reviewed in a
diff before any user sees a name, and the one-off script reads every posting type, not only tech.

**Curation fills what the cascade cannot.** `config/company_names.csv` (ADR-0212) gains an entry,
with evidence read from the Board's own og tags, sidebar logo alt, approot and posting text, for
every residue Board that states its name somewhere, and for Boards of 50+ rows the cascade named
partially or wrongly ("PPD" for Thermo Fisher Scientific, "Pennsylvania" for Penn State, "GE" for
GE Vernova). A Board whose own pages state no name gets no entry; ADR-0212's humanised tenant
serves it.

## Measurements (live, 2026-09-24/25)

- **Sweep:** all 4,175 affected Boards; per Board the board page, the first listing page and up
  to 8–12 details. Board page 200 on 4,157, listing 200 on 4,147.
- **Cascade yield:** 3,605 of 4,175 Boards named (86%), 81,777 of 96,334 rows (85%):
  hiringOrganization 3,348, og:description 173, og:title 84.
- **Accuracy**, a seeded random 120 Boards labelled by reading each page: 94 correct, 10 partial
  ("CHG" for CHG Healthcare, "Rangers" for Texas Rangers), 1 wrong (Sparus named after its
  subsidiary Southern Cross), 15 unnamed. Of the 105 named: 89.5% correct, 9.5% partial, 1% wrong.
- **After curation and the cache**, counted as served Boards (the served table spells some sites
  two ways, `/External` and `/external`; both files match keys case-insensitively, one row per
  folded Board): 544 served Boards (22,236 rows) take a curated name, 3,568 (73,867 rows) the
  cached cascade answer, and 63 (231 rows) are left to the humanised fallback. The cache holds
  3,358 keys: the 3,394 folded Boards the cascade named, less the 36 a curated row overrides.

## Consequences

- One extra GET per uncached Workday Board per run, one attempt, never walls the host.
- A cached name goes stale if a company renames; `--all` re-reads every Hiring Board.
- An uncached Board's live answer can move between runs: the pipeline's detail pass fetches only
  tech-gated postings, so the vote sees a different subset each run, and can differ from what the
  script (which reads every posting type) later writes. Re-running the script after a landing
  closes it; nothing in the pipeline fills the cache.
- A leading number that is part of a brand ("2020 Companies", "407 ETR") reads as an entity code;
  both are curated. Widen `_CODE` only on a measured population.
