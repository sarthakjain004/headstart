# Taleo India-relevance measurement

Backs ADR-0144's Consequences section and `docs/code-review/
2026-09-15_last-5-prs-retrospective-critique.md` finding 4.

## Question

Does `taleo_enterprise`/`taleo_be` serve any real India-located jobs in the live product today,
independent of whether a tenant's Career Section is *named* for India?

## Method and result

`measure_india_rows.py` queries the served LanceDB table (`data/lancedb`, pulled fresh via
`scripts/fetch/pull_lancedb.py`) for every `taleo_enterprise`/`taleo_be` row whose `location`
contains "India" as a whole word. Run 2026-09-15 against served-table version 136 (459,291 total
rows, matching the `_index_base.json` witness): 127 `taleo_enterprise` rows (Mercedes-Benz 92,
TTEC — via its `percepta.taleo.net` tenant, not the `ttec.taleo.net` one — 30, UFlex 5) and 41
`taleo_be` rows (Milestone Technologies 12, plus 5 more employers). Full per-row detail in
`artifacts/india_located_rows.json`.

None of these sit on a Career Section whose name mentions India — a slug-based or India-host-
mining check (the kind `experiment/ats-provider-expansion/PLAN.md` §4c ran) cannot see them.
They're India offices of global tenants, not India-branded ones.

## A wrong first attempt, corrected

The first version of this query used a bare `"india" in location` substring test, which matched
"Indiana"/"Indianapolis" as India and produced an inflated, wrong row count before word-boundary
anchoring (`(?<![A-Za-z])india(?![A-Za-z])`) fixed it. Left here as the reason the script anchors
the match — the mistake is exactly the kind CLAUDE.md's "one-sample-generalised-into-documented-
fact" note warns about, just at the regex level rather than the sample-size level.
