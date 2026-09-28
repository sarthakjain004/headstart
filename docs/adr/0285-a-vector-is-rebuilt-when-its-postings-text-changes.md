# ADR-0285: A vector is rebuilt when its posting's text changes

**Status:** accepted · **Date:** 2026-09-29 · **Builds:** the content hash
[ADR-0021](0021-re-embed-on-content-change.md) deferred · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the upgrade path it reuses),
[ADR-0062](0062-drain-the-description-gap.md) (the once-only backfill it copies),
[ADR-0207](0207-the-served-description-follows-the-posting.md) (the edit ledger it reads) ·
**Issue:** #694

## Context

A Job's vector is built once, from its title and description at first embed. `embed_plan` skips
every id the store holds, so an edited posting keeps a vector of its old text. ADR-0207 now
serves the edited `title` and `description`, and re-derives salary and experience from them, but
search still ranks the row by what it used to say. The sharpest case is **clone-then-edit**. A
company clones a requisition (identical text, so a byte-identical vector), publishes it, then
rewrites it. `workday:amat/external:R2624055` ("New College Grad - Process Engineer - Doctorate")
still carries `R2618176`'s vector ("Process Engineer IV"): cosine 1.0000 to that text, 0.94 to its
own.

ADR-0021 chose this design on 2026-07-04 — "hash the embedded text into each meta row; re-embed on
hash mismatch" — and deferred it until the edit churn was measured, because the embed budget is
sized for new ids. It is measured now. ADR-0207's change ledger (`description_changes.tsv.gz`, HF
state 2026-09-29) records **25,702 replacements across 21,770 Jobs** since it went live on
2026-09-24, over roughly 80–90 runs: about 285 a run on average, against 749–1,786 new vectors a
run. Most of it came in bursts from scraper changes: SuccessFactors alone is 9,543 Jobs, largely
#744's joining of every description block.

The evidence of staleness already served:
- served table v298: **283 groups of byte-identical vectors whose titles differ, 1,943 rows**;
- the 21,770 edited Jobs above, embedded before their text changed.

## Decision

**Each stored row carries `doc_hash`,** a 64-bit BLAKE2b of the title and description the vector
was built from (`doc_prep.doc_hash`). It hashes the raw scraped fields, not `build_doc`'s output,
so a change to how the Doc is assembled cannot re-embed everything at once. It is planner-only
metadata, like `has_description`: `to_meta` writes it at embed time, and `index` never serves it.

**`embed_plan` re-embeds a held Job whose stored `doc_hash` differs from its current text's.** It
goes through ADR-0050's existing upgrade path: the id is listed in `pending_upgrades.txt`, the
merge evicts the old store row, and `index sync` replaces the served row with its `first_seen`
carried. At most `_MAX_EDIT_REEMBEDS` (2,000) such re-embeds a run. The rest keep their differing
fingerprint and are re-embedded when their Board is next in a Slice. The English gate still
applies: an edit into a language the index holds out is not re-embedded (#706 is the gate's own
question).

**Rows embedded before this carry no fingerprint, and `update_meta` stamps one once,** by
ADR-0062's rule for `has_description`: written where absent, never refreshed, since only a
re-embed may change it.
- **A row known to encode text it no longer carries is stamped `"stale"`,** which no real hash
  equals, so `embed_plan` re-embeds it on its Board's next read. Two sources:
  - `config/stale_shared_vectors.txt`: the 1,943 shared-vector rows, listed by
    `scripts/embed/list_stale_shared_vectors.py`. A whole disagreeing group is listed, because
    which row the vector truly encodes can't be told apart, and re-embedding the right one costs
    one Doc and changes nothing.
  - every Job in ADR-0207's change ledger.
- **Any other row in this run's corpus is stamped with its current text's fingerprint.** That is
  the best stand-in for text only known at embed time. From then on an edit shows.

`embed_plan` and `update_meta` hash the same text: both read the join's description-filled corpus
(`corpus-state`), `embed_plan` through `corpus.iter_jobs`, which yields each line unchanged.

## Alternatives

- **Feed ADR-0207's change ledger and title changes straight into `pending_upgrades`**, as #694
  suggested. `update_descriptions` knows a description changed, but a title change is only seen by
  `update_meta`, which runs after the embed, so the two signals would reach the planner a run
  apart and through different files. A fingerprint on the row covers both from one comparison and
  keeps working whoever changes the text.
- **Detect shared vectors in `update_meta` every run** instead of committing a list. It would
  catch clones embedded before the stamp without a file, but it reads the whole vector file in the
  stage that rewrites the source-of-truth metadata, every run, for a one-off repair.
- **Re-embed everything once.** It fixes every stale vector, including the ones no signal names,
  but at ~500k Docs it is weeks of the embed budget for a few percent of rows.
- **Leave it** (ADR-0021's interim). Rejected now that the volume is known and fits.

## Consequences

- Steady state: about 285 extra Docs a run on average, bounded by the 2,000 cap on a burst.
- The one-off repair: about 23,700 rows (21,770 edited plus 1,943 shared), re-embedded at up to
  2,000 a run as their Boards are read, so roughly 12 runs of doubled embed volume.
- A description that flips between two renderings re-embeds on each flip. ADR-0207 logs these;
  2,097 Jobs changed more than once.
- Vectors that went stale before their first stamp without either signal (#694 estimated ~1.3%
  of random rows below cosine 0.95, from 2 of 150, a wide interval) stay stale until their next
  edit. The list and the ledger are the evidence we have.
- `evict_store.py` stays the tool for bulk invalidation (a model or prefix change), as ADR-0021
  said.
- Once every row carries a fingerprint, the list and the ledger read are inert. The list can be
  deleted then.
