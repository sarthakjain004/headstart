# AI Integration

Design and reference notes for adding AI-powered retrieval to HeadStart — turning the
scraped **Job** corpus into something a person can search in plain language and get back the
roles that actually fit.

**Status:** design / reference. Not yet implemented. When a piece gets built and a non-obvious
call is made, record the decision in [`../design-choices.md`](../design-choices.md).

## The core idea (one paragraph)

Job matching has **two kinds of criteria** that need **different machinery**:

- **Semantic** — "a backend role on distributed systems with a collaborative, open-source team."
  The words won't keyword-match. This is what *embeddings* are for.
- **Structured** — "≥5 years, salary > $150k, remote, in Europe." Embeddings are *bad* at this
  (they fumble numbers, ranges, and negation). This is a filter / where-clause — a richer
  version of the `filters.matches()` we already have.

So the design is **hybrid**: an LLM parses the user's paragraph into *(structured constraints +
a semantic intent)*; we hard-filter on the constraints, rank what's left by embedding
similarity, and optionally re-rank the top results with a stronger model. Pure
"embed-everything-and-vector-search" would be *worse* for the structured parts — that mistake is
the thing to avoid.

## What's in this folder

- **[architecture.md](architecture.md)** — the hybrid retrieval design end to end: the two model
  types, the four components (query understanding → index → retrieval → optional generation),
  feasibility at HeadStart's scale, the data-normalization catch, freshness, and a backlog of
  extensions.
- **[glossary.md](glossary.md)** — every AI / ML / information-retrieval term used in these docs,
  explained from first principles and tied to HeadStart. **Read this first if the jargon is
  unfamiliar.**
- **[portfolio-strategy.md](portfolio-strategy.md)** — how to shape this into a standout AI/ML
  engineer portfolio project: what differentiates it from a commodity "RAG chatbot," what to
  avoid, and the metrics that make it credible.

## Recommended first slice

Don't boil the 3.3M-Job ocean. Take a slice — the *currently-hiring* subset, or one **ATS** —
and build: a local embedding index + an LLM query-parser + filter-then-rank, **no generation**.
Prove the ranking feels right, then scale the index and add re-ranking and evaluation. Upgrading
the Telegram bot's keyword filter to *semantic alerts* is a strong parallel first target — it
cashes in the embeddings without needing a search UI.
