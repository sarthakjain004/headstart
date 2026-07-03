# Architecture — semantic + structured Job retrieval

This is the design for letting a person describe the role they want in plain language and get
back the **Jobs** that fit. See [glossary.md](glossary.md) for any unfamiliar term.

## The insight: two kinds of criteria, two kinds of machinery

A query like *"senior backend role on distributed systems, remote, paying over $150k"* mixes:

- **Semantic intent** — "backend / distributed systems." Match by *meaning*, because the exact
  words may not appear in the Job. → **embeddings + vector search**.
- **Hard constraints** — "senior (≥5y), remote, salary > $150k." Match by *rule*. Embeddings
  handle numbers, ranges, and negation poorly. → **structured filter** (a richer
  `filters.matches()`).

The design **parses the query into both**, filters on the constraints first, then ranks the
survivors by semantic similarity. Embedding everything and vector-searching the whole query
would degrade the constraint half — that is the canonical RAG mistake to avoid.

## Two kinds of models (don't conflate them)

- An **LLM** (e.g. Claude) reads text and *writes* text. Used here for query parsing, re-ranking,
  evaluation, and field enrichment.
- An **embedding model** reads text and outputs *a vector* (a list of numbers capturing meaning);
  it writes nothing. Used here to turn each Job and each query into a vector.

Anthropic has **no embeddings endpoint** — the embedding model is a separate piece: a local model
(`sentence-transformers`: BGE / E5 / GTE-small) for a free, offline setup, or an API (Voyage,
OpenAI) if you'd rather not run one.

## The pipeline

### 1. Query understanding (one LLM call)

The user's paragraph → a typed object via **structured outputs**:

```
{ "min_years": 5, "salary_floor": 150000, "locations": ["Europe"],
  "remote": true, "employment_type": "Full-time",
  "semantic_query": "backend engineer distributed systems" }
```

Cheap on a small model (`claude-haiku-4-5`), and it is a **single request, not an agent**. The
structured fields feed the filter; `semantic_query` feeds the vector search. This slots directly
onto the existing `Filter` seam in `src/headstart/filters.py`.

### 2. The index

Embed each Job's `title + description` once, keyed by `Job.id`, and store the vectors in an
**embedded vector database** — LanceDB, FAISS, or sqlite-vec (no server to run).

The streaming work already in place is the substrate: `data/jobs/tech/{ats}.jsonl` — the tech subset
(ADR-0017), deduped by `id`, written incrementally. So you **embed only new ids each scrape and evict
ids that go dead** — the index stays fresh for cents, and the freshness story is already wired to the
scrape/liveness pipeline. Only tech roles are embedded; the full scrape stays in `data/jobs/`.

### 3. Retrieval

1. **Filter** the corpus by the structured constraints (years, salary, location, remote, type).
2. **Vector-rank** the survivors by similarity to `semantic_query`.
3. *(optional)* **Re-rank** the top ~50 with a stronger model (cross-encoder or LLM) to sharpen
   the top 10.

### 4. Generation — the optional "G" in RAG

An LLM over the top-K to explain *why* each Job matches, summarize the shortlist, or answer
follow-ups ("which of these are most startup-y?"). **Skip for v1** — the ranked list is already
useful, and generation is the smaller half of the value.

## Feasibility at HeadStart's scale

- **~3.3M Jobs.** You cannot fit the corpus in an LLM context — that is the whole reason
  retrieval exists. A 3M-vector index is fine for FAISS/LanceDB, but it is a real
  approximate-nearest-neighbor (ANN/HNSW) index, not a toy.
- **Cost / footprint.** Local embeddings are free but you run the model; an API pass over 3.3M
  descriptions is tens of dollars one-time, then near-free incrementally (only new ids). Storage
  is a few GB.
- **Latency.** First-stage vector search over millions of vectors is milliseconds with an ANN
  index; the LLM steps (query parse, re-rank) dominate, which is why re-ranking runs on ~50
  candidates, not the whole corpus.

## The data-normalization catch

`Job.experience` and `Job.salary` are **free-text, provider-phrased** ("3-5 Years",
"Mid-Senior level", whatever the **Board** said). To filter on `≥5 years` or `> $150k` you must
**normalize them to numbers first** — either a parse step at scrape time, or a one-time LLM
**enrichment** pass. The **Batches API** runs that at half price and is ideal for 3.3M rows. This
is the real work hiding inside "filter by years/salary," and it doubles as an AI feature worth
talking about.

## Freshness

Postings close. Tie embedding and eviction to the existing liveness + scrape cadence so the
index doesn't rot — re-embed new/changed Jobs, drop ids that go dead.

## Extensions (backlog)

- **Semantic alerts** — upgrade the Telegram bot's `Filter.q` from substring match to a saved
  embedding per subscriber; new Jobs match by similarity. Highest value for lowest effort: reuses
  the bot, turns keyword alerts into "roles that feel like what I want."
- **Resume → Jobs** — embed a pasted CV and reverse-search the index (profile-to-jobs).
- **"More like this"** — vector-neighbor lookup from any Job.
- **Hybrid keyword + vector** — add BM25 alongside the vector search for better recall.
- **Skill / tech-stack tags** — an LLM pass extracts structured facets (a better `department`).
- **Near-duplicate collapse** — embeddings cluster the same role posted on several Boards
  (complements the exact-`id` dedup, which only catches identical ids).

## Grounding in the current code

- `src/headstart/filters.py` — `Filter` / `matches()` is the structured-filter seam to extend.
- `src/headstart/models.py` — the `Job` record; `id` is the natural embedding key; `experience`
  and `salary` are the free-text fields needing enrichment.
- `data/jobs/{ats}.jsonl` — the incremental, id-keyed corpus to embed from.
- `src/headstart/bot.py` — the alert path to upgrade for semantic alerts.
