# ADR-0380: Description facts name immutable TECH text and archive only replacements

**Date:** 2026-10-03 · **Status:** accepted owner design; pending parent integration
· **Amends:** [ADR-0330](0330-trends-are-recomputed-from-recorded-job-facts-whenever-a-rule-changes.md)

Historical rules must read the text actually observed then. `update_descriptions`
uses its existing accepted new/changed selection to write description facts under
`data/facts/description_facts/{ats}/`. Each row carries `id`, `description_hash`
(SHA-256 of exact UTF-8 stored text), `observed_at` (UTC reconciliation time),
`run_id`, `run_attempt`, and `code_sha`. Local runs may have empty GitHub identity
fields. This timestamp is an observation, not the ATS's edit time or the merge Tick.

Only superseded text is archived, before publishing a replacement store fragment,
under `data/facts/description_archive/{ats}/`: `id`, `description_hash`, and
`description`. Both histories use immutable content-named zstd Parquet batches;
an identical local retry verifies existing content. No new mutable state or full
store rewrite is introduced. The existing facts artifact/upload path carries both.
The join opts in with `--facts-dir data/facts`; compaction reads current text only
and leaves this archive intact.

`description_facts.read_description(facts_dir, current, job_id, content_hash)`
returns exact matching current or archived text, else `None`. `current` is the
already-loaded `read_store` mapping, reusable across calls. Archive corruption
raises instead of supplying unverifiable text. The parent selects the historical
hash from observation facts; this helper never selects a version by time. Archive
lookup scans an ATS's fragments using Parquet filters, so bulk historian callers
may eventually need a batched read if measured lookup cost warrants it.

No facts are emitted for unchanged text, restored held text, or the existing
question-mark degradation guard. Null, missing, empty and whitespace-only fetches
retain the existing store policy: they cannot establish an actual blank or a
deletion (ADR-0089). Legacy textless store entries remain unknown and unheld.
No new text is projected backwards; existing held versions are not backfilled
with invented past observation times. A superseded legacy text can be retrieved by
hash without proving when it was first observed.

## Parent-approved descriptor bootstrap and observation scan

The parent approved observing legacy held text once at the current observation
time, since an unchanged skip-listed description otherwise has no identity fact.
Run against a refreshed durable store and all existing descriptor facts locally:

```bash
python -m headstart.ingest.update_descriptions --seed-existing \
  --store data/descriptions --facts-dir data/facts --seed-batch-size 10000
```

This standalone operation stops without reconciling a corpus, rewriting any store
or state, or archiving any bodies. It loads one ATS with the existing `read_store`
policy (null/blank entries excluded), holds that ATS's existing identity pairs
for deduplication, and writes at most the requested descriptor rows per immutable
file. A retry scans existing observations and skips recorded `(id, hash)` pairs;
partially completed seeds resume with remaining pairs observed at the retry's
current time. This bounds output batches, not `read_store`'s per-ATS memory usage.
Normal unchanged reconciliations remain empty. Bootstrap is explicit and is not
enabled in the regular pipeline.

`description_facts.iter_observations(facts_dir, ats=None)` streams all descriptor
rows chronologically by their canonical UTC `observed_at`; equal timestamps have
deterministic file order, not a claimed causal order. Each file has one observation
time; file ordering reads its first descriptor, then rows stream in 8,192-row
batches. Rows include `run_id`, `run_attempt`, and `code_sha`. Join the run id and
attempt to `data/facts/job_facts/*.parquet` schema metadata for that union's `stamp`;
`observed_at` is reconciliation/bootstrap time and must not be renamed to that
stamp. The archive is content storage, with no chronological/run metadata: its
rows are `(id, description_hash, description)` and are read through exact-hash
`read_description`. Neither helper resolves a missing run join by guessing.

A nonnull actual reference-baseline body can supply its own historical text. A
null old baseline remains unknown: a bootstrap observation today is not evidence
that today's text existed at that baseline, even when the Job id matches.

Archive or identity-write failure stops replacement of that ATS's current store
and leaves its corpus and in-memory change ledger unchanged. Store fragments
publish through a temporary file, so a failed write cannot expose partial current
text. A completed archive or observed fact can survive a later write failure: it
records real input, and must not be treated as a committed served Tick. This is a
local publication ordering guarantee, not a transaction across HF uploads or ATSes.

The scope is parsed **Tech subset** descriptions only. No non-tech text capture,
raw ATS payloads (#904), vector precision change, or new-Board/ATS coverage gate.
The current store remains durable; ADR-0292's reaper remains deferred. Any future
reaper must archive current versions before removal. Versions already compacted
away before this capture begins remain unavailable. Archive files accumulate;
there is no retention deletion in this change.

Measured synthetic fixture (local, 2026-10-03): 200 new descriptions produce 200
identity rows, 9,496 bytes, and no archive. A later 20 replacements plus 50 new
descriptions add 70 identity rows (3,078 bytes) and 20 archived texts (11,389 bytes).
An unchanged pass and a 250-row unavailable-text pass add zero files or bytes.
Sizes include Parquet overhead and use repetitive synthetic text; they are not a
production compression rate or an annual forecast. Ignored captures and the
reproduction script live in `experiment/trends-description-history-2026-10-03/`.
