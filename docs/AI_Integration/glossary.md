# Glossary — the AI / ML / IR terms, from first principles

Every term used in these docs, explained plainly and tied to HeadStart. No prior ML background
assumed.

## The one distinction everything rests on

There are **two different kinds of models** in this system:

- **LLM** (large language model, e.g. Claude) — reads text and *writes* text. Used for query
  parsing, re-ranking, evaluation, and field enrichment.
- **Embedding model** — reads text and outputs *a vector* (a list of numbers capturing meaning);
  it writes nothing.

Different tool, different job. That's why "Anthropic has no embeddings endpoint": you use a
dedicated embedding model for the vector part and an LLM for the text parts.

## The core idea — semantic search

- **Embedding** — a list of numbers (from the embedding model) representing the *meaning* of a
  piece of text. The trick: similar meanings get numbers that are close together. Picture every
  Job description as a dot in a giant space where "meaning" is the geography. For HeadStart, each
  Job's title + description becomes one embedding.
- **Vector / dimensions** — "vector" is the formal word for that list of numbers.
  "384-dimensional" means the list is 384 numbers long. More dimensions capture more nuance but
  take more space.
- **Cosine similarity** — the standard measure of "how close are two embeddings." It's the angle
  between the two vectors: 1.0 = same direction (very similar meaning), ~0 = unrelated. Search
  works by embedding the query and finding the Jobs with the highest cosine similarity.
- **Semantic search** — searching by *meaning* instead of exact words. "Distributed systems role"
  can match a Job that says "large-scale infrastructure" with zero shared keywords, because their
  embeddings land near each other.

## Doing it over millions of Jobs

- **Vector database** — a store built to hold millions of embeddings and answer "which vectors
  are nearest to this one" fast. A normal SQL database can't do that efficiently. FAISS,
  LanceDB, and sqlite-vec are examples — all run locally, no server.
- **ANN — approximate nearest neighbor** — at 3.3M vectors, checking the query against *every*
  Job to find the truly closest is too slow. ANN finds the *almost*-closest, dramatically faster,
  with a tiny accuracy tradeoff. Standard practice at scale.
- **HNSW** — the most common ANN algorithm (Hierarchical Navigable Small World). It arranges the
  vectors into a graph you hop through to reach the query's neighborhood quickly. The vector DB
  implements it; you just use it. *"An HNSW index for approximate nearest-neighbor search over 3M
  embeddings"* is a clean interview sentence.

## The old way, and combining them

- **BM25 / lexical / keyword search** — the classic search algorithm (Elasticsearch, Lucene). It
  ranks by exact word overlap, weighted so rare words count more. Great at exact terms (searching
  "Kubernetes" finds Jobs literally containing it) but blind to meaning.
- **Hybrid retrieval** — running BM25 *and* semantic search and merging the results. Each covers
  the other's blind spot: keyword nails specific tech names, embeddings catch paraphrases. Being
  able to explain *why* you'd combine them is the "judgment" signal.

## The LLM pieces

- **Structured outputs (a.k.a. function calling)** — forcing the LLM to return a strict JSON shape
  you define (a schema) instead of free prose, so your code can reliably read it. For HeadStart:
  the user's paragraph in, `{ "min_years": 5, "salary_floor": 150000, "remote": true, ... }` out.
- **Re-ranking** — a two-stage trick. A fast, cheap search pulls ~50 candidate Jobs; a slower,
  smarter model re-orders just those 50 so the best rise to the top. You pay the expensive model
  on 50 items, not 3.3M.
- **Bi-encoder vs cross-encoder** — the technical reason re-ranking helps.
  - A **bi-encoder** turns the query and each Job into vectors *separately* and compares them —
    fast, and you can pre-compute all the Job vectors, so it's used for first-stage search over
    millions.
  - A **cross-encoder** feeds the query and one Job *together* through the model and scores the
    pair directly — more accurate (it sees them in context) but too slow to run over everything,
    so it's used (or an LLM is used) only on the ~50 candidates.
  - First stage = bi-encoder for speed; second stage = cross-encoder / LLM for accuracy.
- **Enrichment** — using an LLM to clean messy data. `experience` says "3-5 Years" or
  "Mid-Senior level"; an enrichment pass turns that into a number (`min_years: 3`) so you can
  filter on it.
- **Batches API** — submit a large pile of LLM requests to run in the background at half price
  (vs. one-at-a-time, full price). Perfect for a one-time enrichment of 3.3M rows.

## Evaluation — the cluster to really own

This is what most portfolio projects skip and what AI/ML interviews probe hardest.

- **Ground-truth / labeled set** — a set of test queries where *you've marked which Jobs are
  actually relevant*. The answer key; without it you can't score anything. Build a small one
  (50–100 queries) by hand or with LLM help.
- **Recall@k** — of all the genuinely relevant Jobs, what fraction showed up in your top *k*
  results? "Did we even *find* the good stuff?" (*k* is the cutoff, e.g. top 10.)
- **Precision@k** — of your top *k* results, what fraction were actually relevant? "Is the top of
  the list clean, or full of junk?" Precision and recall trade off: a wider net raises recall but
  usually lowers precision.
- **MRR — Mean Reciprocal Rank** — how high up the *first* relevant result lands, averaged across
  queries. If the first good Job is at position 3, that query scores 1/3. Rewards getting
  *something* right near the top.
- **nDCG — normalized Discounted Cumulative Gain** — the gold-standard ranking metric. It rewards
  putting the *most* relevant Jobs highest, gives less credit the further down a good result
  sits, and is normalized so 1.0 = a perfect ordering. "Evaluated at nDCG@10" signals you
  understand ranking quality, not just "did it return something."
- **LLM-as-judge** — instead of hand-labeling thousands of query/Job pairs, have an LLM score them
  ("rate how well this Job matches this query, 0–3") so you can evaluate at scale. The modern way
  to measure LLM and search systems. Know its pitfalls — the judge can be biased or inconsistent,
  so you validate it against some human labels.
- **Ablation** — proving each component earns its place by turning it *off* and re-measuring.
  "With re-ranking: nDCG 0.71. Without: 0.62. So re-ranking is worth +0.09." A set of these is an
  "ablation study" — exactly the experimental rigor AI/ML roles look for.

## The throughline

The embedding/vector terms show you can **build** retrieval. The evaluation terms show you can
**prove it works and reason about why** — and that second thing is what separates a hire from a
tutorial-follower. Most candidates can say "I used embeddings." Almost none can say "I measured
nDCG@10, ran ablations, and used an LLM-judge I validated against human labels."
