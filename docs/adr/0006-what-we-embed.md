# ADR-0006: What we embed — title + cleaned description; structured fields stay as filter metadata

- Status: Accepted
- Date: 2026-06-28

## Context

The embedding model (ADR-0005) turns **one string per Job** into one vector — so the whole design
question is *what goes into that string, and what stays out*. A Wellfound row carries both
free-text (`title`, `description`) and structured fields (`remote`, `job_type`, `years_experience`,
`compensation`, `location`, …). The hybrid retrieval design (`docs/AI_Integration/`) splits a query
into a *semantic* part (match by meaning → embeddings) and *hard constraints* (match by rule →
filter). The index must mirror that split.

## Decision

**Embed `title + cleaned description`, nothing else.** The document string is
`"search_document: " + title + "\n\n" + markdown_stripped(description)` (the prefix per ADR-0005).
The description is markdown, so syntax (`**`, `###`, `[text](url)`) is stripped to plain text and
whitespace collapsed; the redundant header block some postings carry ("Experience: 3 to 5 years…")
is kept as prose because it is real semantic context, just demoted from markdown.

**Structured fields ride *alongside* the vector as metadata, never inside it.** `id, company,
location, remote, job_type, years_experience, compensation, currency, department, url, posted_at`
are written to `meta.jsonl` row-aligned with the vectors, for the structured-filter half. Embedding
them would reintroduce the canonical RAG mistake: vectors can't reason about numbers, ranges, or
negation, so a salary or a year baked into the text smears into the geometry instead of filtering
cleanly.

**English gate before embedding.** A `langdetect` pass over `title + description` holds non-English
rows out of the index (14 of 6,374 on Wellfound), per Project Scope in `CLAUDE.md`. The model isn't
trusted to "filter" foreign text — it would embed it badly — so the gate is an explicit prior step.

**Years-of-experience is handled by extraction, not the embedding.** The required years live in the
`years_experience` field only ~64% of the time; where absent they're in the prose. A separate
extraction/enrichment step (its own future ADR) fills a normalized numeric field from the column or,
where empty, the description — so YoE is always a numeric *filter*, never a similarity match. The
description thus feeds two tools: the embedding (for meaning) and extraction (for the number).

**Rejected alternatives.** Embedding the whole row / folding structured fields into the text —
breaks numeric filtering, the exact failure the hybrid split exists to avoid. Title-only embedding —
discards the description's semantic richness, which is most of the signal.

## Consequences

One vector per Job (descriptions fit nomic's 8192-token window, so no chunking). The structured
filter's quality now depends on the metadata fields and on the YoE-extraction component, which
becomes the next piece after storage. Implemented in `scripts/embed/embed_wellfound.py`; output
under `data/embeddings/wellfound/`.
