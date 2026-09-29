# ADR-0286: A held Job whose text fails the English gate leaves the index

**Status:** accepted · **Date:** 2026-09-29 · **Builds on:**
[ADR-0285](0285-a-vector-is-rebuilt-when-its-postings-text-changes.md) (the re-evaluation it
hooks) · **Relates to:** [ADR-0050](0050-persist-descriptions-across-runs.md),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) · **Issue:** #706

## Context

The search index is English-only (CLAUDE.md): `embed_plan` holds a new Job out when
`doc_prep.is_english(title, description)` fails. The gate ran once, when a Job was first
embedded, and never again. A description that arrived or changed afterwards, or a row embedded
before the gate existed, left a non-English posting served on an English vector.

Measured on served table v298 (498,848 rows, 2026-09-29), 878 rows with a description fail
today's gate: workday 286, successfactors 252, avature 124, eightfold 66, tesla 28 and others. They
are mostly German, Dutch and French ("(Senior) Full Stack Product Engineer (m/w/d)", "Mendix
Developer met start in Testautomatisering"). Joined against the evidence #849 used:
- 207 are in ADR-0207's edit ledger, so their text changed after embedding;
- 5 share a vector with a row of another title;
- the other 691 carry neither signal, which suggests they were embedded before the gate existed.

The 124 avature rows are a different case. They are English postings whose stored text begins
with Spanish page labels, a scraper locale bug fixed separately under #706.

The tech half of #706 needs nothing new: 12 served rows fail today's tech filter, down from 1,014,
and ordinary sync removes them.

## Decision

**Re-gate an embedded Job every time `embed_plan` re-evaluates it, and drop it when it fails.**
("Held" in this ADR's title means a Job with a vector in the embedding store.) ADR-0285 made
`embed_plan` re-evaluate every embedded Job whose text changed (or is stamped `stale`) and every
title-only vector whose description arrived. Each already passes through the English gate, and a
failure used to mean only "don't re-embed". Now an embedded Job that fails is listed in
`data/state/pending_non_english.txt`. `embed_merge --non-english-ids` drops those ids from the
store outright: unlike an upgrade, nothing replaces them. The id is then not `fresh`
(`corpus ∩ store`), so `index sync` evicts its row through the normal path, Unconfirmed on its
Board's next read and evicted on the one after (ADR-0083). A new non-English Job is never listed,
since it was never embedded.

**On a scope-excluded Board too.** A Board that is never authoritative (#695) leaves the eviction
scope every run (ADR-0053), so a row that is merely not `fresh` would keep serving its old English
vector there, which is where #706 said the problem lives. A listed id is one this run's scrape
returned, so it was seen: `index sync --non-english-ids` adds it to the set ADR-0243 already keeps
in scope for tech-filter rejections, and it takes the same grace period.

**A re-gate list** (`config/regate_english.txt`) names the served rows that fail today and that
no re-evaluation would reach: the 878, less `avature:ea`'s 124, so 754 rows. A listed Job is
English-checked on every run that reads its Board, never re-embedded; the answer is deterministic,
so one that passes simply stays, and a row still listed after it leaves the index is inert. The
list is written by `scripts/embed/list_non_english_served.py`.

## Alternatives

- **Re-gate every served row every run.** Uniform, but `langdetect` over the ~250k rows a run
  reads costs minutes of merge time for about 1 new failure in 500.
- **Move the gate into `filter_tech`.** One gate for everything, but the tech subset feeds the
  curated Job feed too, and CLAUDE.md scopes the English gate to the search index only.
- **A version-counted sweep, like `DERIVATIONS_VERSION`.** #706 asked for one, so that a gate
  change reaches existing rows. It suits a rule that changes; this gate hasn't, and the gap was
  rows it never saw. The cost of not having one: a future change to `is_english` (its
  500-character window, or a `langdetect` upgrade) reaches served rows only through a new list
  written by the script above. Revisit if the gate changes.
- **Accept the 0.18%.** It contradicts the index's stated scope, and it grows with every edit.

## Consequences

- About 754 rows leave the index over the next reads of their Boards; Workday and SuccessFactors
  are two thirds of them. The rest of ADR-0285's re-evaluations add the few a run that edits
  produce.
- Their evictions are booked as Closed by Trends (ADR-0227), like any sync eviction, although the
  postings are still listed. That is about 0.15% of rows once, spread over several days, below
  what the Trends readings resolve. CONTEXT.md's **Eviction** entry names this path.
- A title-only row whose description arrives in another language now leaves the index instead of
  keeping its English title vector, the same answer a new Job with that text gets.
- **Merge after the avature locale fix.** An `avature:ea` row whose text is edited while it still
  carries the Spanish labels would be dropped by the per-edit re-gate (not the list, which
  excludes it). It returns as a new Job once its text is English.
