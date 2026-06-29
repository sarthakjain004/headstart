# ADR-0007: Search metadata is a typed, canonical `Job`-shaped projection

- Status: Accepted
- Date: 2026-06-28

## Context

Each embedding carries structured fields beside it for the filter half of search (ADR-0006). But
the embed pipeline reads the raw `data/jobs/wellfound.csv`, whose one-off scraper emits
**non-canonical column names** (`job_type`, `years_experience`, `compensation`, `currency`) and
untyped strings (`remote` is the text `"True"`). Meanwhile the 18 real ATS scrapers already build
canonical `Job` records — 15 set `employment_type`, 9 `experience`, 4 `salary`, and `remote` is a
real `bool` — and emit them as JSONL. So Wellfound is the lone source that drifts from the project's
ATS-agnostic `Job` vocabulary, and the metadata inherited that drift, untyped.

## Decision

The search metadata is a **typed projection of the canonical `Job`** — the filterable subset of
`Job` fields, with real types, written beside each vector. A `to_meta(row)` adapter next to the
Wellfound reader maps its raw columns onto `Job` vocabulary and types the clean fields:

- `remote` `"True"`/`"False"` → real **bool** (`None` if blank).
- `job_type` → **`employment_type`** (values kept; already a clean enum).
- `years_experience` → **`experience`**, left as the **raw string** (`"3+"`).
- `compensation` → **`salary`**, left as the **raw string** (the range carries its currency symbol).
- redundant `currency` is **dropped** (Wellfound derives it from the compensation symbol).
- `id`, `ats`, `company`, `title`, `location`, `department`, `url`, `posted_at` pass through as strings.

**Scope (B1) stops at deterministic typing.** The messy `experience` and `salary` stay raw strings
here; turning `"3+"` into a number and parsing salary ranges is the separate extraction/enrichment
component (regex + LLM), not this change.

**The adapter is temporary.** Canonical sources (the ATS JSONL) need no mapping — they're already
`Job`-shaped. `to_meta` exists only until the Wellfound scraper is updated to emit canonical fields
directly, at which point it collapses to near-identity.

## Rejected alternatives

- **Keep Wellfound's raw column names.** Each new source would carry its own dialect, so the filter
  logic would need per-source field maps forever — defeating the entire purpose of the `Job` model.
- **Parse salary/years to numbers now.** That is the enrichment component (LLM-shaped, its own ADR);
  folding it into B1 conflates a 10-line typing fix with a corpus-wide extraction problem.

## Consequences

The metadata is source-agnostic and immediately filterable on the typed fields (`remote == true`,
`employment_type == "full-time"`). Filtering on `experience`/`salary` waits on the enrichment
component. Implemented as `to_meta` in `scripts/embed/embed_wellfound.py`; existing
`data/embeddings/wellfound/meta.jsonl` must be regenerated to pick up the new shape.
