# ADR-0331: A requirements sample counts each requisition once, under its directory name

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0324](0324-an-agent-reads-what-a-roles-postings-ask-for-counted-over-a-sample.md) (the
requirements view) · **Relates to:**
[ADR-0323](0323-an-agent-sees-one-posting-once-under-a-company-name.md) (copies and directory
names), [ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (a Board from a Job id),
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the agent
contract)

## Context

ADR-0324 shipped `role_requirements` over `GET /requirements` (#901). Its code review, and the review
of the follow-up (#909), found that the sample counted a requisition once per copy (one AgileEngine
requisition was 12 of the 300 rows closest to "data engineer"; 263 of the 300 were distinct), that
a Job whose company names only its Board was listed and counted under its host, that the route
took a sample size nobody asked for, that several of ADR-0324's constrained terms had no measured
"after" figure, and that the tool and the route had copied rules the codebase already keeps
elsewhere.

## Decision

**Each requisition counts once.** The sampled Jobs are grouped by the rule a search page lists
copies by (ADR-0274, widened by ADR-0323): one requisition per country on one Board, or on two
Boards of its employer. That rule moves from `space_mcp/posting_copies.py` to
`jobs/requisition_copies.py`: the Space's serving path must not import the MCP client package, and
the module is named for the glossary's **Requisition** rather than "posting". Every count is over
the first Job of each group. The route reports `read` (Jobs read) and `distinct` (requisitions
counted); the answer says "counted over 263 distinct postings, of 514,163 postings that the filters
admit, copies included (300 postings read; 37 copies of one counted once)" (the local copy of the
served table, 2026-09-29).

**A Job whose company names only its Board is shown under the Company directory's name**, by one
rule: `company_name.with_directory_name` (with `names_no_company`, both moved to
`boards/company_name.py`), which `space_mcp/company_names.named` also calls. The Space finds a
Job's Board among the directory's own keys (`TrendHistory.board_and_name_of_job`, via
`board_identity.board_end`), never by `board_of`'s guess, which names a phantom Board for a native
id carrying a colon (ADR-0049). A Job the directory does not name is its Board's own employer, so
Jobs naming no company are never one employer, and the skill its employer is named after is not
counted for it.

**The sample size is fixed** at 300 Jobs read (`n=` removed): the tool always sent 300.

**The route's counts are keyed `jobs`**, as `/companies/locations`' are; the text an agent reads
keeps "postings". The route also returns `sample_size` and `category_window`, which the tool words
its lead from instead of restating them.

**Shared, not copied.** The filter arguments both tools take (their schema, Space names, query
string and scope line) live in `space_mcp/search_arguments.py`; the cache is `job_search`'s own
`_cache_get`/`_cache_put`; the vector search's setup is one `JobSearch._nearest`; ranking is
`serving/count_ranking.most_first`, which `location_counts` also uses; each family's ids and the
current families travel as one `RoleAssignments`.

**Agent contract 10**: the JSON change is contract, so `_AGENT_API_VERSION` and `AGENT_API` rise
together (ADR-0324's route was 7, ADR-0323 having taken 6, ADR-0321 8 and ADR-0322 9).

## Measurement

On ADR-0324's corpus (5,000 descriptions, 200 per role family, local copy of the served table,
2026-09-29), read by hand as ADR-0324 read them:

| Skill | Found | Change | After |
|---|---|---|---|
| Spark | re-read | none | 12/12 |
| Digital forensics | re-read | none | 11/11 |
| PLC | "PLC NAND" (penta-level-cell flash) 1 of 12 | not before "NAND" | 12/12 |
| Signal processing | "mixed-signal" 1 of 12; then bare "DSP" as a demand-side ad platform ("Amazon DSP buyers", "#1 DSP on G2") 2 of 12 | not after "mixed-"; bare "DSP" replaced by DSP phrases ("DSP processors", "DSP algorithms", "DSP-based") | 20/20; postings counted 90 → 72 |
| iOS | Cisco IOS 1 of 12 | only cased "iOS"; upper-case "IOS" dropped | 12/12; of 18 postings naming only "IOS", 3 meant Apple's (recall lost) and 15 Cisco's |
| Go | bare "Go" outside a list: 9 of 20 the language | stays list-only; 11 cased phrases added ("Go services", "Go programming", "Go experience") | phrases 11/11; Go 229 → 236 postings; about 33 more name Go alone (the known recall gap) |

Copy grouping costs 64 ms over 300 Jobs; a "data engineer" sample took 1.7 s locally, against 1.0 s
before.

## Deferred

- **Computer vision** stays in the vocabulary at 4 of 10 by the strict rule: the six misses were
  one employer's boilerplate, and the term was right every time. The answer gives each skill's
  distinct employers and says a skill few employers mention is weaker evidence.
- **One config locator.** `tech_skills` finds its file by the walk several modules copy
  (`space_mcp/role_families`, `search_filters/fx`, `boards/company_name` and others). Making it one
  helper touches modules this change does not own.

## Consequences

- `space_mcp/posting_copies` is now `jobs/requisition_copies`; ADR-0323, the docs and the Space's
  deploy paths name the new place.
- Tests: `test_jobs_requisition_copies.py`, `test_boards_company_name.py` (the moved naming rule),
  `test_trends_trend_history.py` (a Job's Board from the directory), and the requirements tests.
