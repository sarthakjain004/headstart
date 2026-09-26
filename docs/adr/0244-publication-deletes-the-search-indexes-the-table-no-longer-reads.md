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

`index_publish` reads the local table's latest manifest with `lance`, taking every segment of every
index it serves, since one index can span several directories. Each `_indices/{uuid}/` directory it
does not name is superseded. The publication commit leaves the superseded directories out of its
additions and deletes their files from the Hub, in the same commit as the table. The commit that
publishes a version is the one that drops what that version replaced.

Only `_indices/` is touched. Data fragments, deletion files and old manifests stay additive, as
before, and `cleanup-index` still reaps them. The delete fails safe: a table whose manifest cannot
be read deletes nothing, and the commit goes up as it did before this change. Only directories
this copy holds and the manifest does not name are candidates, so an index the manifest names
without a directory of its own (Lance's fragment-reuse index, which nothing here creates) changes
nothing.

It keeps only the latest version's indexes, not the previous version's too. Keeping N-1 would
guard a reader of the previous version, and there is none: the Space and every stage open the
latest, and a reader that downloaded the previous commit already has its files. The blobs that
commit points at are deleted by `reclaim_storage` once they are orphaned anyway, so holding N-1's
index files on the Hub would keep ~0.41 GB a run without making the previous version readable.

Nothing is done at fetch time. Once the Hub holds only the referenced indexes, fetching
`data/lancedb/*` fetches only those. A fetch-side filter would need the manifest before the
download, a second copy of the same rule, and it would save nothing the delete does not.

## Consequences

* Measured on the Hub listing at `020e05cd`: every file version 545 needs is present (132 fragment
  and deletion files, 17 of 17 index segments), so the table opens with the superseded files gone.
  A test builds a table, replaces an index, deletes the old directory and queries through the new
  one.
* The first publish after this deletes ~8.9 GB of index files. `reclaim_storage` then deletes their
  blobs in the same run: they were pushed on earlier runs, so the age guard does not hold them.
  After that each publish deletes the previous run's ~0.41 GB.
* The merge fetch and the Space boot shrink by the same ~8.9 GB, to about the table's data files
  and one set of indexes.
* A reader on an older version of the table loses its indexes, as it already loses its blobs to
  `reclaim_storage` once they are orphaned. Nothing in the repo opens an old version: the Space
  and every stage open the latest.
