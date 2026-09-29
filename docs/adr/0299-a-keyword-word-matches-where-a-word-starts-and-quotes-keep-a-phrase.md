# ADR-0299: A keyword word matches where a word starts, and quotes keep a phrase together

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) §2 (the
substring rule) and [ADR-0263](0263-the-search-bar-can-match-words-in-the-job-title.md) (Title
words, which compile through the same rule, and its rejected option B, quoted text) · no schema,
index or stored-data change

## Context

The Keyword filter compiled each whitespace-separated word to `lower(col) LIKE '%word%'`, so a word
matched inside any other word. An agent asked the MCP's `search_jobs` for `keyword="AI Engineer"`
in Bengaluru and got 1,459 jobs, among them "System Engineer – OIL & GAS DOMAIN", "Senior Lead
Engineer, Retail Inventory" and "Returnship - IAM SailPoint Developer Engineer". The tool's schema
promised "an exact word or phrase", and its own description suggested `keyword` for "ML", which
also found HTML. The website's keyword box, the search bar's Title words mode and every emailed
Saved Set use the same compiler, so all of them read "ai" the same way.

ADR-0104 had rejected whole words because Rust's regex, DataFusion's engine, has no lookarounds:
`\b` is a word/non-word transition, so `\bc\+\+\b` never matches "c++ developer".

## Options, measured on the served table (v277, 498,853 rows, 2026-09-29)

"AI Engineer" in the title, in Bengaluru:

| Rule | Rows | What it does |
| --- | --- | --- |
| Substring, words anywhere (before) | 1,459 | "ai" inside DOMAIN, Retail, SailPoint, Maintenance; "ml" inside HTML |
| **Word-start, words anywhere** | **1,212** | drops those; keeps Engineers, Engineering, AIML, MLOps, GoLang |
| Whole word | 1,033 | also drops Airflow for "ai" and Google for "go", but "engineer" loses 1,261 distinct Bengaluru titles ("AI Engineering", "Trainee Engineers") and "ai" loses AIML and AIOps |
| Phrase, words together, word-start | 428 | misses "AI/ML Engineer", "AI & ML Engineer" |

Word-start still admits a longer word that begins with the term. Over whole words it adds, in
distinct Bengaluru titles, for "ai": AIML 19, AIOps 12 and a tail of Air/Airflow/Aircraft; for
"go": GoLang 77, Google 64 and Governance 43.

## Decision

The owner chose word-start matching with quoted phrases.

**A term matches where a word starts.** It must follow the column's start or a character that is
not a letter or digit, so `_`, `/` and `-` separate words ("IN_Senior Associate_AI/ML Engineer"
matches "ai"). Only a term that begins with a letter or digit is anchored, and no term's end is,
so "c++", ".net", "c#" and "node.js" keep their matches (1,668 of 1,669, 2,889 of 2,889, 1,035 of
1,035 and 516 of 516 rows) and "java" still finds JavaScript.

**A quoted phrase is one term.** Its words must follow each other, in order, across any run of
non-letter, non-digit characters (`"ai engineer"` finds "AI - Engineer" and "AI Engineering Lead",
not "AIOps Engineer"). Curly quotes count as quotes; a stray quote is dropped, not matched. ADR-0263
rejected quoted text because nothing on the page said the syntax existed and a quote typed for
another reason would change the results. Both are answered now: the keyword box's tip and the
Title words note say what quotes do, and an unbalanced quote changes nothing.

Each term compiles to `regexp_like(col, '(?i)(^|[^a-z0-9])term')`, escaping exactly the characters
Rust's `regex::escape` escapes and doubling quotes. The five-term cap and the 60-character cut per
term are unchanged. `(?i)` in place of `lower(col)` is deliberate. On a 60,000-row sample of live
descriptions, `lower(description) LIKE` took 332–338 ms for every term, because lowercasing the
column is the cost. The same regex without `lower()` took 79–224 ms and matched the same rows as with it. On
titles every rule measured stayed under 230 ms over the whole table. Neither column has an index,
so `LIKE` was a full scan too.

The MCP's `search_jobs` now describes the rule in its schema. When a `query` or `similar_to`
ranks the results, its headline also says the ranking does not narrow the count. That total, for
a query with no filter, was read as "AI jobs in Bengaluru" when it counted every job there.

## Consequences

- Every surface narrows the same way, since they share `_keyword_clauses`: the rail's keyword,
  Title words, `/facets` counts, the MCP and emailed Saved Sets. A saved keyword such as "ai" now
  lists fewer jobs than it did.
- `scripts/eval/verify_filters.py` checks Title words against the word-start rule, restated
  rather than imported, and adds "ai" and a quoted phrase to its cases.
- Word-start still lets a short term find longer words that begin with it (Google for "go").
  A user who needs the bare word can quote it with the next word, or search by meaning.
