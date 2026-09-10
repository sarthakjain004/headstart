# ADR-0129: The LanceDB write checks its own base instead of asking first

**Status:** accepted · **Date:** 2026-09-10 · **Amends ADR-0091** (which moved `cleanup-index` to
its own concurrency group) and **ADR-0023** (which owns the compaction that writes with
`--delete "*"`). Neither decision is reversed; both gain a check at the write.

## Context

Two workflows write `data/lancedb` and neither holds a lock. The pipeline's `merge` job uploads it
additively; `cleanup-index` rebuilds it and uploads with `--delete "*"`, which reaps every remote
file its own local folder does not contain. Both do the same three things — read the table, spend
two to four minutes changing it, upload — and **nothing between the read and the upload asks
whether the base moved.**

On 2026-09-10 that cost run `34450830376` its entire index write. From the two runs' own logs:

```
08:22:32  cleanup-index: "no pipeline in flight — compacting"   (one had run since 07:36:48)
08:26:51  cleanup-index reads the table          409,810
08:27:55  pipeline merge reads the table         409,810        (same base, independently)
08:28:30  pipeline merge writes                  410,516        (+706)
08:29:49  pipeline uploads  -> 410,516 lands on HF
08:30:27  cleanup-index uploads --delete "*"  -> 409,810 overwrites it.  -706
```

The next run opened at 409,810 — the value compaction rebuilt, not the one the pipeline wrote.
Measured damage: all 215 of that run's evictions were resurrected (43 never re-evicted, so closed
postings kept being served) and 327 of its 922 adds were dropped, 178 of them never re-added.
Every job in both runs reported success. `index prune` logged `evict 0` on all five runs of that
window, so no existing counter moved.

**The first instinct — fix the poll — does not work, and this is the load-bearing finding.**
`cleanup-index`'s `window` job already waits in a loop for the pipeline to finish, and on this run
it did: 30 of its 45 attempts printed `pipeline in flight (1); re-checking in 60s` before the 31st
read zero. The query it polls is

```bash
gh run list --workflow=pipeline.yml --status in_progress --json databaseId --jq 'length'
```

and that query returns a **stale empty set**. Measured 2026-09-10 over 239 polls at 4s intervals
against a live repo: it reported `0` once while an unfiltered listing of the same workflow, issued
milliseconds later in the same iteration, showed the run `in_progress`. One observation, so 0.4%
is an order of magnitude and not a rate — but across the 45 polls one attempt makes, even that is
roughly a 1-in-6 chance of proceeding early, daily.

An earlier reading of this incident blamed the *predicate*: `pipeline.yml`'s own gate matches
`.status != "completed"` (which also catches `queued`) while the compaction guard matches only
`in_progress`, and the failure fell in a 3-second gap between the last embed shard finishing and
`merge` starting. That asymmetry is real and worth closing, but the probe **refutes it as the
cause here** — the run was reported `in_progress` by the plain listing at the instant the filtered
query said the field was clear. The endpoint is what is wrong, not the filter written against it.

And even a perfect answer would not be enough, because it is answered too early. Compaction checked
at 08:22:32, read at 08:26:51 and wrote at 08:30:27: by upload time its authorisation was eight
minutes stale. Any check that precedes a long piece of work is a statement about the past.

## Decision

**The write detects the collision itself. Compare, then swap.**

1. A new `headstart.ingest.state_guard` takes a fingerprint of a repo prefix at fetch time
   (`record`) and retakes it immediately before the upload (`verify`), refusing with a non-zero
   exit if it moved. Wired into **both** writers: the pipeline's `merge` job and `cleanup-index`'s
   `cleanup` job.

2. **The fingerprint is content, not a commit sha.** Sorted `path:blob_id` pairs under the prefix,
   SHA-256'd. Blob ids are git content hashes, so the digest ignores commits touching other
   prefixes and survives a history rewrite untouched.

3. **On conflict the step fails red.** Compaction is daily and idempotent, so a lost one costs a
   day of fragment reclamation. The pipeline's scrape work is already banked in the embedding
   store by the time this runs, so what a failed merge loses is one run's index write, which the
   next run recomputes from the corpus.

4. **A cross-run base record** (`data/lancedb/_index_base.json`) carries the row count each writer
   left; `index sync` logs it beside the count it opened and refuses a base it cannot explain.
   `state_guard` catches a collision *inside* a run; this catches a rollback that already
   happened, by any route.

## Alternatives considered

**Widen the poll predicate to `!= "completed"`.** Cheap, and closes a real asymmetry against
`queued`. Rejected as *the* fix because the probe shows the run was `in_progress` when the query
returned nothing — the data source is unreliable, not the filter over it. Worth doing separately;
it is not load-bearing and is not done here, because a change justified by a premise the
measurement contradicts is the shape this repo has been bitten by before.

**A commit sha as the CAS token**, which is what the fix was first specified as. Measured and
rejected: the pipeline publishes four or five commits per run (embedding store, lancedb,
description store, state), so the repo head always differs between its own fetch and its own
upload and a sha-based guard would refuse every run. The daily super-squash (ADR-0071) then
rewrites every sha without changing a byte — verified 2026-09-10, when `list_repo_commits`
returned a single `Super-squash branch 'main'` commit.

**Restore a shared concurrency group.** That is what ADR-0091 removed, and for a measured reason:
GitHub keeps one *pending* run per group and a newer arrival replaces it, so an hourly pipeline
starved the once-a-day compaction into three missed days and `_deletions/` past HF's
10,000-file directory limit. Reintroducing it trades silent data loss for a known outage.

**Keep the base record in `data/state/`.** Rejected: `cleanup-index` never fetches or uploads
`data/state`, so a record kept there would go stale on every compaction and red-run the next
pipeline for a compaction that was entirely correct. Inside `data/lancedb` both writers publish it
in the same commit as the table, so the record and the rows it vouches for cannot disagree.
LanceDB ignores a non-`.lance` sibling in its database directory (verified: `list_tables()` still
returns only the real tables).

**Retry on conflict instead of failing.** Re-fetch the moved base, redo the work, upload again.
Recovers the run rather than losing it, but adds a second execution path through the stage that
writes the served index, for a collision that should now be rare. Deferred until the guard shows
how often it actually fires.

## Consequences

- A collision now costs one run's index write and a red run, instead of rows and silence.
- Two Hub requests per writer per run (`record`, `verify`), each one `repo_info(files_metadata=True)`
  — the constant-cost listing ADR-0033 already chose, ~1.2s against 569 siblings.
- `verify` fails closed when the record is missing: reaching an upload with no recorded base means
  `record` never ran, which is the unguarded write this replaces.
- An empty prefix is a legitimate verdict (a genuine first run), but the Hub declining to list is
  not — `state_fetch._siblings`' existing fail-closed guard is reused rather than reimplemented, so
  a fingerprint is never computed over a listing that is empty because the Hub withheld it.
- The guard cannot see a collision whose two writes both land between `record` and `verify` of a
  *third* party, and it does not serialise anything. It converts silent loss into a visible refusal;
  it is not mutual exclusion.
