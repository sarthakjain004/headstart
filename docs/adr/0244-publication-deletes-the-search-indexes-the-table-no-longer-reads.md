# ADR-0244: Publication deletes the Search indexes the table no longer reads

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0174](0174-every-pipeline-publishes-current-search-indexes.md) (where replaced index files go)

## Context

ADR-0174 has every merge replace the table's Search indexes (`index refresh-indexes`) and publish
them. Lance writes each replacement into a new `_indices/{uuid}/` directory and leaves the old one
on disk. The table's commit to the Hub is additive, so every old directory stayed there until
`cleanup-index` rewrote the table. The vector index alone is ~395 MB a copy.

On 2026-09-26, at dataset commit `020e05cd`, the table's latest version (545) read 17 index
segments, 0.41 GB across 20 files. The Hub held 460 index files, 9.34 GB, in 391 directories. The
other 8.94 GB was more than half of what the dataset stores live. It grew by ~0.41 GB a run
between compactions. Every merge's `state_fetch 'data/lancedb/*'` downloaded all of it: 11.1 GB
to 13.6 GB over the seven runs 36200233818..36218633315, in 150–249 s, the merge job's largest step.
Every Space boot pulls `data/lancedb/*` too, so it downloaded the same dead weight.

## Decision

`index_publish` opens the local table with `lancedb` to learn its latest version, reads that version's
manifest, and treats an `_indices/{uuid}/` directory
as referenced when that uuid's 16 bytes appear in it. The manifest carries every segment of every
index the version serves, and one index can span several directories. It is read as bytes
because the pipeline installs `lancedb` without `pylance`, and `pylance` is the only Python reader
of a manifest's index section. The rule was checked against `lance`'s own
`describe_indices()` on 8 of the Hub's manifests (versions 508–545). Every time it found every
segment `lance` lists. On 7 of the 8 it found one more: the index the version's own commit
replaced, which the manifest still names. So it always keeps the current generation of an index,
and usually the one before it too. The N-1 margin is whatever the manifest still names; this
change does not enforce it on its own.

Each directory it does not find is superseded. The publication commit leaves those directories out
of its additions and deletes their files from the Hub, in the same commit as the table.

Only `_indices/` is touched. Data fragments, deletion files and old manifests stay additive as
before, and `cleanup-index` still reaps them. The delete fails safe in three ways:

* A table `lancedb` cannot open deletes nothing.
* So does a table whose version's manifest is not on disk.
* So does a table whose manifest yields fewer directories than the table has indexes.
* A directory not named by a uuid is kept.

In each of these cases the commit goes up as it did before this change, with one warning.

Nothing is done at fetch time. Once the Hub holds only the referenced indexes, fetching
`data/lancedb/*` fetches only those. A fetch-side filter would need the manifest before the
download, a second copy of the same rule, and it would save nothing the delete does not.

## Consequences

* Measured on the Hub listing at `020e05cd`, every file version 545 needs is present: 132 fragment
  and deletion files, and 17 of 17 index segments. So the table opens with the superseded files
  gone. That is a listing check, not a Hub table opened with those files alone. Run over that
  manifest, the rule keeps 18 of the 391 directories (0.80 GB) and supersedes
  373 (8.54 GB). A test runs three index generations, deletes the oldest and queries through the
  live index.
* The first publish after this deletes ~8.5 GB of index files. `reclaim_storage` then deletes their
  blobs in the same run: they were pushed on earlier runs, so the age guard does not hold them.
  After that, each publish deletes the index that is two generations old, ~0.41 GB.
* The merge fetch and the Space boot shrink by the same ~8.5 GB. What is left is the table's data
  files and two sets of indexes.
* A reader on a version older than the previous one loses its indexes, as it already loses its blobs to
  `reclaim_storage` once they are orphaned. Nothing in the repo opens an old version: the Space
  and every stage open the latest.
