# ADR-0168: Delete the orphaned blobs, don't ask for them to be collected

**Status:** accepted · **Date:** 2026-09-18 · **Amends:**
[ADR-0071](0071-back-to-back-runs-instead-of-a-fixed-cadence.md) (§"the reclaim runs inside the
pipeline's `merge` job" — the placement it chose is kept, the mechanism it placed there is
replaced) · **Relates to:** [ADR-0030](0030-fail-closed-on-unfetched-state.md) (fail closed on a listing the
Hub did not really answer), [ADR-0091](0091-compaction-outranks-the-pipeline.md) (the sibling
housekeeping job, and the "nothing notices an absence" gap this repeats)

## Context

On 2026-09-18 the pipeline's `merge` job began failing outright:

```
403 Forbidden: Private repository storage limit reached, please upgrade your plan
to increase your private storage limit.
```

Three of fifteen runs died that way. The dataset read **96.83 GB of the free tier's 100 GB** while
its current revision held **7.57 GB** across a single commit, with no branch, tag or open PR
pinning anything. 89.26 GB was dead weight the repo was still paying for: 27 superseded copies of
`data/embeddings/jobs/embeddings.f32` (73.20 GB) and 27 of `meta.jsonl` (15.57 GB).

**The quota counts stored bytes, not reachable ones.** Git is content-addressed and deliberately
non-destructive; LFS splits it further, so a commit holds a ~130-byte pointer while the bytes live
in an object store keyed by sha256. Deleting a file — or rewriting every commit that mentions it —
removes only pointers. The blob stays, because that is what makes history recoverable.

`super_squash_history`, which is all the reclaim step did, collapses the branch to one commit and
therefore makes prior blobs *unreachable*. That makes them **eligible** for collection and nothing
more. Collection is asynchronous, batched, and entirely HF's to schedule; no API promises when.

Two things made this invisible rather than merely slow:

**The step never measured its own effect.** It re-read `live2` to assert live files had not shrunk
— a good check — but never re-read `usedStorage`. It printed
`::notice::squashed; … usedStorage falls as HF collects the orphans`: a prediction, in the present
tense, on every run. Four consecutive merge logs carried `98.85 → 99.23 → 99.42 → 99.78 GB` while
each announced success.

**The margin was one day wide and the design did not know it.** The run rewrites both large files
wholesale — 3.31 GB of new blobs per run at ~30 runs/day, so the repo sheds its entire 100 GB quota
in dead weight every 24 hours. The maximum tolerable collection lag was ~24 h, for a process with
no SLA and no signal. The step's own threshold comment still assumed 1.86 GB/run at 19.4 runs/day.

This wall had been hit once before at the same number — 2026-07-27, 96.8 GB of 100 against 1.9 GB
live — and the fix chosen then was the squash whose assumption was never re-validated.

## Decision

**Delete the orphaned blobs with `permanently_delete_lfs_files`, then require the quota to have
moved.** `headstart.ingest.reclaim_storage` replaces the inline step in `merge` and the body of the
manual `squash-dataset-history` escape hatch.

It still squashes first — collapsing history to one commit is what makes "absent from HEAD" mean
"referenced by nothing", so the delete set is unambiguous — but squashing is no longer mistaken for
the thing that frees bytes.

Because this call will happily delete a blob a live commit points at, three invariants hold it:

- **Never delete a live blob.** The set is chosen by `file_oid not in live_oids`
  (`RepoSibling.lfs.sha256` and `LFSFileInfo.file_oid` are the same identifier) and the
  intersection is *asserted* empty immediately before the call, not merely filtered once.
- **Never race an in-flight upload.** A blob pushed within `--min-age-minutes` (45) is left alone
  however orphaned it looks, because a concurrent writer's object can land between the listing that
  defines "live" and the delete that acts on it.
- **Fail closed on an unreadable listing.** An empty live set against a non-empty store is the
  catastrophic case — it makes *every* blob look orphaned — so it is refused as a Hub failure
  rather than read as a repo with nothing live in it. ADR-0030's rule, applied to the destructive
  direction.

**The verification polls, because the counter lags the delete.** Measured live 2026-09-18,
deleting 14 orphans worth 6.71 GB: the call returned in 2.8s, `usedStorage` still read the
pre-delete 14.31 GB at t+3.3s **and** t+9.1s, and had fallen to 7.60 GB by t+24.5s, stable
thereafter. A single read straight after the delete therefore sees the *old* figure and fails a
reclaim that worked — which would have fired the `::error::` on every healthy run and trained
everyone to ignore the one alarm that matters. So the check re-reads for up to 180s and only calls
it a failure at the end. The first draft of this change asserted that timing without measuring it,
which is the very mistake the change exists to correct.

**The logic moved out of the workflow YAML into a module**, which is the other half of the fix: the
old step could not be tested, and so it never was. `tests/test_reclaim_storage.py` fakes the Hub at
the `HfApi` seam and pins each invariant plus the bug itself — removing the `usedStorage` check
turns `test_exits_nonzero_when_usedstorage_does_not_fall` red.

**The trigger is orphaned bytes, not `usedStorage`.** The old threshold fired on a quantity that
conflates live and dead data; the module acts on the dead weight directly, above a 1 GB floor. At
3.3 GB/run that is essentially every run — deliberately, because a destructive path that runs only
at 40 GB is a path that is cold exactly when it is finally needed.

## Consequences

- Storage sits near live plus one or two runs of churn — measured 14.31 GB against 7.60 GB live
  just before a manual reclaim — instead of drifting to the quota. It is *not* pinned at live size:
  the 45-minute age guard deliberately spares the most recent run's blobs, and runs are ~48 minutes
  apart. The reclaim is verified rather than predicted; one that frees nothing exits non-zero.
- The step stays `continue-on-error: true`: the data is already uploaded when it runs, and
  reclaiming space must never be able to lose a run. The `::error::` is the signal, not the exit.
- **All rollback history is destroyed, every run.** This was already true of the squash; it is now
  true by deletion as well, so the blobs cannot be recovered by HF either. Acceptable because every
  byte is derived state the pipeline regenerates, and the live revision is asserted intact across
  the operation.
- Manual reclaim now actually reclaims, and is renamed `squash-dataset-history.yml` ->
  `reclaim-dataset-storage.yml` to say so: it previously squashed and reported success while
  freeing nothing — a trap for whoever reached for it mid-outage. Older ADRs still name it by
  its old filename; they are records of what was decided then and are left as written.
- **Still missing: nothing alerts when the reclaim has not succeeded in N days.** The `::error::`
  makes each individual failure visible in its own run; none of it notices an *absence*. That is
  ADR-0091's open item verbatim, one step over, and it is not addressed here.
