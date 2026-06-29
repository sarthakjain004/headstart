# ADR-0005: Embedding model — local `nomic-embed-text-v1.5` for English semantic search

- Status: Accepted
- Date: 2026-06-25

## Context

The first slice of AI semantic retrieval (see `docs/AI_Integration/`) is built over `data/jobs/wellfound.csv`
— 6,374 Jobs with real descriptions (median ~892 tokens; **82% exceed 512 tokens**, max ~4,800). We need
to pick the bi-encoder embedding model that turns each Job (and later each query) into a vector. Four
requirements pin the choice: descriptions are **long** (so truncation is the dominant risk), the search
corpus is **English-only for now** (multilingual deferred; non-English is pre-filtered out before
embedding — a separate `langdetect` gate, not the model's job — per Project Scope in `CLAUDE.md`), it must
run **locally** (free, private, reproducible) on an **Apple M5 Pro**, and it should leave room to scale to
the eventual ~3.3M-Job corpus.

## Decision

Use **`nomic-embed-text-v1.5`**, run locally via `sentence-transformers`.

**Why this one.** It is the model that satisfies all four drivers at once: **8192-token context** (every
Wellfound description embeds whole — no truncation of the 82% that overflow 512), **English-focused**
(matches the scoped corpus), **768-dim with Matryoshka** representation (vectors can be truncated to
512/256-dim later, at 3.3M scale, without re-embedding), **Apache-2.0 with fully open training data** (clean
reproducibility story), and small (~137M params) so it is fast on this hardware.

**Execution.** Run with `device="mps"` so the transformer forward pass (≈ matmuls) executes on the M5 Pro
GPU, including the M5 generation's per-GPU-core neural accelerators. PyTorch's MPS backend targets the
**GPU, not the Neural Engine (ANE)** — using the ANE would require a Core ML conversion and is deliberately
out of scope. At 6,374 Jobs encoding is a couple of minutes; the full corpus is a one-time job of hours,
all local, no embedding API needed.

**Rejected alternatives.**
- **512-token models** (`all-MiniLM-L6-v2` at 256, `bge-small/base-en`, `gte-small`, E5 at 512) — would
  silently truncate the majority of every description, discarding the signal we embed for. The single
  disqualifier.
- **Multilingual long-context models** (`bge-m3`, `jina-embeddings-v3`) — multilingual isn't a requirement
  for this slice, and `jina-v3` is CC-BY-NC (non-commercial), a license landmine. Revisit `bge-m3` when the
  global/Zoho corpus lands.
- **`gte-large-en-v1.5`** — a viable higher-quality long-context English alternative (8192 ctx, 1024-dim,
  ~434M). Kept as the **upgrade path** if the eval harness shows nomic's ranking is too dull. Speed is a
  non-factor on the M5 Pro, so the call is quality-vs-flexibility; nomic wins now on Matryoshka, open data,
  and a leaner index.
- **API models** (OpenAI `text-embedding-3`, Voyage) — unnecessary cost and data egress when local is free
  and fast on this hardware.

## Conventions (load-bearing — get these wrong and ranking silently degrades)

`nomic-embed-text-v1.5` is an **asymmetric retrieval** model and requires **task prefixes** — a literal
string glued to the front of the text before encoding, which tells the model what role the text plays:

- **Index time** (building the vector store): prepend `search_document:` to each Job's `title + description`.
- **Query time** (per user search): prepend `search_query:` to the user's query.
- **Symmetric tasks** ("more like this Job", clustering, dedup — both sides are the same kind of text):
  use `clustering:` on both sides instead of the query/document split.

Mismatched or omitted prefixes throw no error; they just make results worse. L2-normalize the vectors and
compare by **cosine similarity**. (`sentence-transformers` can apply the prefix for you via
`prompt_name=...`; it is the same string-on-the-front mechanism either way.)
