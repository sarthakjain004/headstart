# ADR-0017: Post-hoc recall-biased tech-role filter as the authoritative tech gate

- Status: Accepted
- Date: 2026-07-03

## Context

HeadStart serves software/tech openings, but a scrape returns everything a board hosts — a single
company routinely mixes `Staff Nurse`, `Sales Representative`, and `Senior Software Engineer`. Only
tech roles should be embedded, indexed, and shown; the rest are wasted embedding compute (ADR-0005)
and noise in the UI. So we need a tech/non-tech gate, with a hard constraint from the spec: **a
non-tech job creeping through is acceptable, dropping a real tech job is not** (recall ≈ 100%).

Where to filter is the crux, and it depends on *which cost* you optimise:

- **Company selection** barely helps — almost no board is purely non-tech, so you can rarely drop a
  whole company.
- **Source query** (Lever `?department=`, Workday `jobFamilyGroup`) is the right lever for *scraping*
  cost, and is worth doing where cheap. But it **cannot be the authoritative tech gate**: only some
  ATSes expose a usable facet, department taxonomies are inconsistent (software hides under Product,
  R&D, Data, …), and a server-side facet *will* drop tech jobs mis-filed under an odd department —
  violating the recall constraint. Often the department isn't even in the list response.
- **Post-hoc filter** (after scrape, before embed) saves no scraping — but it is the *only* layer
  that is uniform across all ATSes and can be recall-safe by construction (it sees the full title +
  department and can be deliberately inclusive).

CLAUDE.md previously called a post-hoc filter "the wrong layer." That was scoped to the *scraping*
cost, and for that goal it's true. This decision optimises a **different** cost — embedding compute
and the served corpus — under a hard recall constraint, and for *that* goal post-hoc is the correct
layer.

## Decision

A **post-hoc, recall-biased regex filter** (`headstart.tech_filter`) is the authoritative tech gate.

- The scrapers keep writing the full set to `data/jobs/{ats}.jsonl` (unchanged). A filter stage
  (`src/headstart/ingest/filter_tech.py`, and `filter_jobs()` in the `python -m headstart` pipeline) writes the
  tech subset to **`data/jobs/tech/{ats}.jsonl`**, which is what the feed, embedding, index, and UI
  read from now on.
- `classify(title, department)` decides via regex, recall-first (precedence: a strong software
  signal → tech; a generic role token *with* a non-software qualifier → not tech; a generic token
  alone → tech; a tech department → tech; else not tech). It returns the reason, for the gate.
- **Verification gates** (`scripts/filter/verify_tech.py`): (1) a deterministic self-consistency
  check — no dropped job may match a strong signal (offline, and asserted in tests); and (2) a
  **reasoning gate** — an independent judge (via the Claude Code CLI, `claude -p`, no API key) reads
  a random sample of the *dropped* pile and flags any real tech job the regex missed, reporting the
  false-negative rate with reasoning so the patterns can be widened.

Measured on the current 285k-job scrape: **~17% kept** (≈ 83% less embedding work), **0** dropped
jobs match a strong signal, and after one gate-driven pattern widening the reasoning gate found
**0/100** false negatives (the first run had surfaced ~12%, e.g. `Tech Lead`, `AI Technologist`).

## Rejected alternatives

- **Source query as the authoritative filter** — lossy on recall, inconsistent taxonomy, partial ATS
  coverage. Kept as a *future, complementary* scrape-volume optimisation, never the authoritative gate.
- **Company-selection filtering** — boards are mixed, so it drops almost nothing.
- **An LLM per-job classifier** — far too expensive at millions-of-jobs scale. The LLM is the
  *verification* layer (sample the dropped pile), not the per-job gate.
- **Filter at query time** (embed everything, filter in the where-clause) — wastes the exact
  embedding compute this is meant to save, and bloats the index.

## Consequences

`data/jobs/tech/` is the canonical embed/served corpus; `data/jobs/` remains the full scrape (source
of truth). False positives are tolerated (they only cost a little embedding); false negatives are
guarded by the two-part gate, and the reasoning gate's misses feed back into the patterns. CLAUDE.md's
scope is updated: post-hoc is the right layer for the embed/index/UI tech gate, source query remains
the lever for scrape volume. This supersedes the "a post-hoc filter … is the wrong layer" line for
the embedding-cost goal only.
