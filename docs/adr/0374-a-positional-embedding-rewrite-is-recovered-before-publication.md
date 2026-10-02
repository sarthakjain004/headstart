# ADR-0374: Recover positional embedding rewrites before publication

**Status:** accepted · **Date:** 2026-10-02 · **Relates to:** ADR-0190, ADR-0050

Removing an interior embedding row changes both the metadata sequence and vector positions.
Replacing metadata first and recovering by truncating the old vector tail can silently give a
surviving Job another Job's vector. An actual subprocess interruption reproduces this, and the
nonfatal `embed_prune` step could previously publish the unfinished pair.

Keep the existing store filenames and add a durable pending-rewrite journal. Sync both prepared
files before installing the journal; recovery finishes their replacements and updates the
manifest count before removing it. Recover before merge reconciliation and before pruning reads
the store. Independently recover and validate the store immediately before publication; an
unresolved rewrite or inconsistent count/byte length aborts that upload even if pruning timed out.
Safe failures before the rewrite can still leave the larger store and permit publication.

Exception-only rollback was rejected because process termination cannot run it. A new generation
directory/pointer format would also work but requires changing every reader and the HF layout;
the journal fixes this single-writer pipeline without that migration. It is not a concurrent-reader
snapshot protocol and cannot recognize historical corruption whose files already have matching
sizes. Regression tests assert ID-to-vector correspondence, not merely matching lengths, across
interruptions at every replacement boundary.
