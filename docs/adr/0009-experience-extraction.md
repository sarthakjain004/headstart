# ADR-0009: Years-of-experience extraction — a tiered deterministic cascade

- Status: Accepted
- Date: 2026-06-29

## Context

To filter "≤ N years" the required experience must be a **number**, but embeddings can't reason about
numbers (ADR-0006) so it has to be extracted as structured data. The number lives in two places: a
source's structured field (Wellfound provides `years_experience` for ~64% of Jobs) or, when that's
absent, the free-text description (e.g. *"7+ years of experience"*). The extractor must be
source-agnostic (most ATS sources won't have a field) and extensible toward millions of Jobs.

## Decision

A **tiered, deterministic cascade** in `src/headstart/experience.py` — pure, I/O-free, unit-testable
functions, run cheapest-first by `extract(field, description)`, returning the first hit with the tier
that produced it (`ExperienceSpan(min_years, max_years, source)`):

- **Tier 1 — `from_field`**: parse a structured field (`"5+"` → 5; `"3 to 5"` → 3–5). Deterministic,
  no description needed.
- **Tier 2 — `from_description`**: experience-**anchored** regex over the description — a number with
  the word "experience" within ~25 chars, so *"40-year old code"* is never matched and adjectives
  ("7+ years of *proven* experience") are tolerated.

Output (`{id, min_years, max_years, source}`) is written to `data/enrich/wellfound_experience.jsonl`
by `scripts/enrich/extract_experience.py`, joined into LanceDB by `build_index.py` as the numeric
`min_years` / `max_years` / `experience_source` columns (kept *alongside* the raw `experience`
string). Filtering uses `min_years`; a `--max-years N` filter keeps Jobs with unknown experience
(`min_years IS NULL`) because "unknown" is not "too senior".

**Deferred tiers (not built):** an LLM pass (Batches) for prose the regex misses, and seniority
inference from the title for Jobs that state no number. Adding one is a new `from_*` function chained
in `extract`; widening regex recall is appending to `_DESC_PATTERNS`.

## Coverage (Wellfound, measured)

82.6% of Jobs got a number — 64.4% from the field (Tier 1), 18.1% from the description (Tier 2),
which recovered 51% of the field-empty Jobs. Manual eyeballing of regex matches showed clean
precision (the anchoring holds). 17.4% got nothing.

**This coverage will fall as the corpus grows, by design.** Tier 1's 64% is Wellfound-specific — it
depends on Wellfound exposing a field. Field-less sources fall through to the regex tier (the
source-agnostic floor, ~51% of field-empty here), so as such sources dominate, total coverage drifts
toward that floor — and *that* is when the deferred LLM/inference tiers start earning their cost. The
cascade degrades gracefully: `from_field` returns `None` and Tier 2 takes over automatically.

## Rejected alternatives

- **LLM extraction now** — deterministic 82.6% already suffices for v1; the LLM would chase the
  residual 17%, much of which is un-extractable (no number stated). Revisit when field-less sources
  lower coverage.
- **Embedding the number** — numbers don't filter; this is the ADR-0006 mistake.

## Consequences

`min_years` is a live filter (`--max-years`). Null-experience Jobs are kept on that filter (a policy
choice, easy to flip). Patterns and tiers are extensible without touching the runner or the schema
join. Scaling the extraction to millions is deferred.
