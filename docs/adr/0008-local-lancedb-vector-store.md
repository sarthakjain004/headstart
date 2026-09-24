# ADR-0008: Local LanceDB for the query-time vector store (cloud later)

- Status: Accepted
- Date: 2026-06-28

## Context

The embedding store (`embeddings.f32` + `meta.jsonl`) is enough for a brute-force numpy probe, but
the search design is **filter-then-rank**: hard-filter on the typed metadata (ADR-0007 — `remote`,
`employment_type`) and rank the survivors by vector similarity. It must also scale from today's
6,360 side-corpus vectors (ADR-0005) to the eventual ~3.3M-Job corpus. We want a **local, embedded**
store now; cloud storage is deferred because it carries real cost/investment.

## Decision

Use **LanceDB**, embedded and local, at `data/lancedb/`. It stores the vectors and the canonical
typed metadata in one table and does a `where` filter plus vector search in a single query — exactly
filter-then-rank (with `prefilter=True`, the filter narrows the candidate set *before* ranking). It
scales from 6k to millions on the **same API** (brute-force when small, an ANN index when large), is
one `pip install` with no server, and is Apache-2.0. The cloud path is a clean extension rather than
a rewrite: the underlying Lance format runs directly on object storage (S3), so "explore cloud
later" becomes a config change.

Ingest: a loader script loads the store into the table. Query: a search script pre-filters on
metadata then ranks by cosine. (Both were side-corpus-only scripts, since removed; the production
path is `headstart.ingest.index` and `headstart.search`, ADR-0014/ADR-0019.)

## Rejected alternatives

- **sqlite-vec** — leanest possible dependency and full SQL filtering in one file, great at this
  scale, but it does brute-force vector scan (no ANN index), so it would be outgrown at ~millions
  and force a migration exactly when it matters. LanceDB avoids the tool switch.
- **FAISS** — fastest pure ANN index, but stores *only* vectors: metadata and filtering live
  elsewhere and filter-then-rank must be hand-rolled with id selectors, fighting the hybrid design.
- **numpy brute-force** (the current probe) — instant at 6k, but no persistent store, no ANN, no
  native filtering; fine as a probe, not as the store.
- **Qdrant / Milvus / Weaviate** — server-oriented; overkill for "simple local now". Reconsider as
  cloud options later.

## Consequences

B1's typed metadata pays off directly: `remote` is a filterable bool column, `employment_type` a
filterable string. Filtering on `experience` / `salary` still waits on the enrichment component
(they remain raw strings). `data/lancedb/` is regenerable from the embedding store, so it is
gitignored. Moving to cloud storage is a future, separate ADR.

*(Amended 2026-09-24: the names of the original side-corpus and its scripts were removed from this
record by the owner's decision, along with that corpus; the decision above is unchanged.)*
