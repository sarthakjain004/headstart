# ADR-0061: Stored metadata is refreshable — facts reconcile from the corpus, derivations re-derive on a version bump

**Status:** accepted · **Date:** 2026-08-18 · **Relates to:**
[ADR-0007](0007-search-metadata-canonical-typed.md), [ADR-0025](0025-parallelize-nightly-pipeline.md),
[ADR-0048](0048-skip-details-we-already-hold.md), [ADR-0050](0050-persist-descriptions-across-runs.md)
· **Closes:** #162

## Context

Every column of `data/embeddings/jobs/meta.jsonl` is written exactly once, by `doc_prep.to_meta`
at **embed time**, and `embed_plan` skips ids that are already embedded. `index sync` builds table
rows straight from those frozen dicts, and `index compact` rebuilds the table from *itself* — so
nothing anywhere re-reads a job after its first embedding. The consequence, hit repeatedly:

- The experience-extraction fix in PR #164 corrects `min_years` for **new** jobs only; the ~264k
  rows already served keep values a known-buggy extractor produced (#162 measured 1,839 inflated
  floors and 926 needlessly-`unspecified` rows among just the 52% of rows whose text we hold).
- A board that edits a job's salary, location, or remote flag after we embed it serves the stale
  value forever, because the fresh scrape row is only consulted for ids *not* yet embedded.

This is the ADR-0048 trap in general form. ADR-0050 broke it for description *text*; this ADR
breaks it for everything else.

## Decision

Classify every stored column as a **fact** or a **derivation**, and give each class its own
refresh path. A fact is observed and cannot be recomputed — it can only be re-observed. A
derivation is `f(code, facts)` — recomputable at will, so a code fix must reach every row.

| column | class | refresh path |
|---|---|---|
| `vector` | fact (about the embedded doc) | frozen; only the ADR-0050 upgrade path replaces it |
| `has_description` | **fact about the vector** — records whether the *embedded doc* carried text; the upgrade planner keys on it | frozen with the vector; refreshing it from the store would hide title-only vectors from the upgrade path forever |
| `first_seen` | fact (table-owned) | never rewritten (ADR-0031) |
| `id`, `ats` | identity | never rewritten |
| `title` | fact, **display-refreshed** | fact reconcile; the vector keeps encoding the old title until a doc-drift upgrade exists ([ADR-0021](0021-re-embed-on-content-change.md)'s hook) — a current title over a slightly stale vector beats a stale title |
| `company`, `location`, `remote`, `employment_type`, `experience` (raw), `salary`, `department`, `url`, `posted_at` | facts | **fact reconcile**: every run, jobs in this run's corpus that are already in the store get these fields overwritten from the fresh scrape row |
| `min_years`, `max_years`, `experience_source` | derivations | **version sweep**: recomputed from held facts when the extractor changes |
| `description` | fact — **not stored in meta at all** | lives in the ADR-0050 store; an *edited* description still propagates nowhere, because doc-drift detection (ADR-0021) remains deferred |

**The stage: `headstart.ingest.update_meta`**, in the merge job between `embed_merge` and
`index sync` — inside the store's single-writer window (ADR-0025), with everything already on
disk (the corpus and description store arrive in the `corpus-state` artifact; the store via
`state_fetch`). Two passes:

1. **Fact reconcile, every run.** For corpus jobs whose id is in the store, overwrite the fact
   columns from the scrape row. When `experience` (the raw field) or `title` changed, the row is
   re-derived immediately even at an unchanged version — both are cascade inputs, so leaving the
   numbers put would let the served floor contradict the served raw string. The description store
   is not read for this pass.
2. **Version sweep, on bump.** `doc_prep.DERIVATIONS_VERSION` (an int beside `to_meta`) against
   the watermark in `data/state/derivations.json`. When code is newer, re-run the full cascade
   `extract(experience, description, title)` for every row whose description the ADR-0050 store
   *settles* (an entry, text or authoritative-null); rows the store has never settled keep their
   embed-time values — recomputing without the text a row was derived from could only downgrade
   it. The watermark advances only after the rewrite lands, and **not at all if the sweep found no
   held descriptions**: the merge job downloads the description store on `continue-on-error`, so an
   empty store there means the artifact was lost, and stamping would record a sweep that read
   nothing and leave every row unswept for good.

Both passes rewrite `meta.jsonl` atomically (tmp + rename), **preserving row order and count** —
the file is row-aligned with `embeddings.f32`, and `_load_store` hard-errors on drift.

**Propagation: `index sync` reconciles metadata unconditionally.** After adds, evictions and
upgrades, sync diffs the non-vector, non-`first_seen` columns of every id present in both the
store and the table, and rewrites the changed rows (delete + re-add, carrying `first_seen` across
and reusing the store's vector — identical bytes, since a vector never changes without a re-embed
that re-adds the row anyway). No watermark bookkeeping on the sync side: the invariant is simply
*the table's metadata always equals the store's*, and sync self-heals any drift, whichever pass —
or bug — produced it. The diff is a projection read of ~264k rows without vectors, a few MB;
reported as its own `refreshed N` line so it can never inflate the ADR-#161 add/evict accounting.
The scan is ~16 columns over ~264k rows — hundreds of MB of Python dicts, not "a few MB"; it is
survivable on the merge runner but is the real cost of making the reconcile unconditional, and the
rewrite is issued in `_ADD_CHUNK` batches so neither the row list nor the delete-before-add window
grows with the size of the sweep.

## Costs, measured against the constraints that shaped earlier ADRs

- **HF blob minting** (the ADR-0050 concern): `meta.jsonl` is already appended to and re-uploaded
  whole every run; an occasional in-place rewrite is the same upload class, and
  `squash-dataset-history` reclaims history either way. The description store's fragment overlay
  is *not* copied here because meta has no equivalent of its 174 MB/run rewrite problem.
- **Lance table writes**: the reconcile writes append-class fragments sized to the diff, exactly
  like sync's adds (the discipline that moved `compact` to `cleanup-index`). A version bump makes
  one large diff, once; the next `cleanup-index` compaction absorbs it. **Unmeasured**, and the
  risk is real: facts are re-observed for every corpus∩store row, so one volatile fact field would
  turn this into a near-full-table rewrite every run — the amplification ADR-0031 rejected. The
  first runs after merge must be watched for exactly that, and the `refreshed N` line is what to
  watch.
- **Hot-path cost when idle**: the fact pass touches only corpus∩store ids; the sweep is one
  integer compare; the sync diff is one columnar read. No new network fetches in any job.

## Alternatives considered

- **Propagate via a `cleanup-index` rebuild-from-store** — no new sync code, but corrections wait
  days, and `compact` changes meaning from vacuum to authority, concentrating risk in the job
  that rewrites everything at once.
- **Thin meta: store facts only, derive at sync time** — the purest form of this ADR's principle,
  but it moves extraction onto every sync, makes sync read the description store, and rewrites
  the embed/meta contract. Adoptable later without undoing this design.
- **Per-row version stamps instead of one watermark** — resumable partial sweeps, but the sweep
  is minutes of regex over ~300k texts; a store-level watermark plus diff-only writes is simpler
  and every bit as safe (a failed sweep leaves the watermark behind and retries next run).
- **Fragment overlay for meta (the ADR-0050 pattern)** — solves a rewrite-cost problem meta does
  not have, at the price of a two-layer read under the row-alignment invariant.

## The description gap this exposes

The sweep can only repair rows whose text the store settles, and **127,501 of 263,769 served rows
(48.3%) have no store entry at all** — every one of them carrying no `has_description`, i.e.
embedded before ADR-0050 and never revisited since. Measured by ATS, closing that gap is far
smaller than it sounds:

| | jobs | boards | what settles them |
|---|---|---|---|
| listing-only ATSes (greenhouse, ashby, lever, workable, recruitee, keka, personio, darwinbox, teamtailor, freshteam) | 34,170 | 5,491 | the description arrives free in the listing — one request per Board |
| detail-pass ATSes (workday, smartrecruiters, zoho, eightfold, ripplehire, rippling, trakstar, successfactors) | 93,331 | 5,299 | the listing plus a per-Job fetch |
| **total** | **127,501** | **10,790** | |

`join`'s 1,093 rows are in that count but unreachable — it is in `registry.DISABLED_ATS`, so no
Board selection will ever scrape it; those rows leave the index by eviction rather than repair.

10,790 Boards is **half a normal 20,000-Board slice**, and every one of the 127,501 rows carries a
stored `url`, so none is unreachable. The reason it has never happened is only that the slice is
priority-ordered and never selects these Boards. That makes the backfill a **Board-selection**
problem rather than a new fetching stack, and it is deliberately left to its own change so this one
stays reviewable.

## Consequences

An extractor fix now reaches every served row within one pipeline run of merging: bump
`DERIVATIONS_VERSION`, and the sweep + sync reconcile do the rest. #162 closes by construction —
this ships with `DERIVATIONS_VERSION = 1` against an absent watermark, so the first run *is* the
backfill, re-deriving every settled row with whatever extractor is current (including PR #164's
fix, in whichever order the two PRs merge). It repairs the 136,268 rows whose text we hold; the
other 127,501 are **not** self-closing: once the Board-selection change settles their
descriptions, nothing re-derives them until the next version bump, and `embed_plan` will not
re-embed them either (they are not `degraded` — they carry a description now). Whichever change
settles a description must therefore also mark it for re-derivation; that is part of the
Board-selection work, not a free consequence of this one. Scrape-fact edits (salary, remote, location …) now reach the
served table in the next run instead of never. `verify-search-filters` remains the harness that
would catch a reconcile writing wrong columns, and the README served-table schema is untouched —
no column was added, removed, or retyped.
