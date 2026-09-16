# ADR-0161: The eviction scope travels between stages as Board keys, not as the corpus it was derived from

**Status:** accepted · **Date:** 2026-09-16 · **Relates to:** [ADR-0014](0014-search-index-ingestion-and-freshness.md) (the board-scoped sync), [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (why the scope must come from the *full* scrape), [ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (the key space it lands in), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (what is subtracted from it), [ADR-0026](0026-shard-the-scrape.md) (the join this is written in)

## Context

`join` uploads a `corpus-state` artifact that `merge` downloads straight back. Neither job is
parallel, so all of it is on the critical path: measured across the five runs of 2026-09-16, **223 s
mean to upload** — the single largest step in `join`, larger than the tech filter at 187 s — and
**91 s mean to download**. That is 5.2 min/run, 26.2 min across those five runs, **9.4% of total
wall clock**.

The artifact is `data/jobs` + `data/state` + `data/descriptions`. Read the zip's own central
directory for run `35088621664` (artifact `10445016249`, 260 entries) and the split is exact:

| part | files | compressed | share | uncompressed |
| --- | ---: | ---: | ---: | ---: |
| `data/jobs/*.jsonl` (pre-tech-filter) | 33 | 1,565,546,920 | **60.3%** | 9,094,276,749 |
| `data/descriptions` | 100 | 644,923,854 | 24.8% | 646,894,877 |
| `data/jobs/tech` | 33 | 348,857,676 | 13.4% | 1,994,229,162 |
| `data/state` | 94 | 36,660,474 | 1.4% | 204,697,291 |
| total | 260 | 2,596,032,826 | 100% | 11,940,098,079 |

Sixty per cent of the artifact is the untech'd half of the scrape — 9.1 GB of job records, 2,076,434
to 2,131,462 lines a run against ~423,000 tech ones.

**The obvious change — just drop it — is wrong, and silently so.** `index sync` reads it
deliberately: the eviction scope is the Boards the *full* scrape covered, not the Boards the tech
corpus covers, because a Board that was scraped and dropped to zero **tech** jobs must still have its
closed postings evicted. Scope it on the tech corpus and that Board vanishes from the scope
altogether and serves its stale rows forever. ADR-0023 names this as one of the two classes the
prune sweep was added to mop up; it is a bug this repo has already had.

But the scope is a **set of Board keys** — 14,700 of them on that run. Every other consumer of the
untech'd records runs in `join` (`filter_tech`, `update_ledgers priority`/`gap`), not in `merge`;
grepping every module the merge job runs (`embed_merge`, `update_meta`, `index sync`/`prune`,
`role_trends`, `state_witness`) finds `data/jobs/tech` and `data/state` and `data/descriptions`, and
`data/jobs/*.jsonl` in exactly one place: `index._scraped_boards`. So 9.1 GB crossed two jobs to
answer a question worth 452 KB, and the job that already held the records — `join` — threw the
answer away.

`docs/pipeline/2026-09-01_twelve-run-log-review.md` traced the same consumers independently,
reached the same conclusion, proposed the same file name, and named the hazard that makes the
change non-trivial: `_scraped_boards` *silently* falls back to the tech corpus's Boards when the
scrape dir holds no `.jsonl`, so removing the records without supplying the set some other way
would not error — it would quietly narrow the scope to tech-only Boards. The two halves must land
together, which is why they do here.

## Decision

`scrape_join` derives the scope where the records already are and records it;
`index sync` reads that instead of re-deriving it.

- **`scrape_join.write_scraped_boards`** → `data/state/scraped_boards.json`, a JSON list of
  `resolve_board()` keys. Derived in the union loop that already touches every line, so it costs one
  `json.loads` per line (measured: ~12 s of added parse time at 2.08 M lines) plus one
  `live_keep_set()` over the committed ledger (measured: 0.8 s). Written unconditionally, even when
  the join covered nothing — `data/state` round-trips through the HF dataset, so a skipped write
  would scope this run's eviction on the previous run's Boards, the same hazard
  `write_unauthoritative_boards` is written unconditionally for.
- **`index_plan.scraped_boards(recorded, scraped, corpus_ids, live)`** answers from the cheapest
  source that can: the full scrape on disk if it is there, else the recorded keys, else the corpus
  ids' Boards. It moved out of `index.py` into `index_plan` on the way, because that module exists
  to keep "the scoping invariants unit-testable on CI's base-deps-only install" and `index.py`
  imports LanceDB — the equivalence test below is the safety argument for this ADR and it has to
  actually run in CI, not skip.
- **`pipeline.yml`** ships `data/jobs/tech` in place of `data/jobs`. The other two paths keep `data/`
  as the artifact root, so `tech/` still extracts to `data/jobs/tech` and `--source` is unchanged.

The scrape-on-disk arm deliberately **outranks** the recorded file. The records are the definition
and the file is a summary of them, so a local run that just scraped must never be scoped by a
summary that round-tripped through HF. In the pipeline that arm is inert: `data/jobs/` arrives
holding only `tech/`, and the glob is non-recursive.

### What makes the two derivations the same answer

`resolve_board(job_id, live)` is a function of the id and of `live`, which is
`boards_by_canon(live_keep_set(ledger))`. `data/validate/liveness/` is **committed to git**, and
`join` and `merge` check out the same commit, so both halves resolve through the same lookup. The
`join` derivation covers the lines this run's union wrote; `data/jobs/` is gitignored and not on the
HF dataset, so in CI it is created by that union and holds nothing else.

`tests/test_scrape_join.py::test_the_recorded_scope_is_the_one_the_full_scrape_defines` pins it:
one fixture, the scope derived both ways, `assert` set equality. The fixture carries the cases where
a naive derivation diverges — a colon-bearing native id (ADR-0049), a Board on no ledger row, a
Board with postings but no *tech* postings — and the recorded side is handed the tech corpus as
`corpus_ids`, so ignoring the file falls through to a scope that is measurably *different*, not
merely re-derived. Both failure modes were confirmed red before the change was kept.

## Consequences

- The artifact drops from 2,596,032,826 to ~1,030,600,000 compressed bytes, **−60.3%**, and the
  scope file adds ~452 KB (~114 KB compressed at the artifact's `compression-level: 6`). Upload and
  download both shrink with it; on the 2026-09-16 means that is ~189 s of the 314 s.
- `scrape_join` now parses every line it copies. A malformed line raises there rather than in
  `filter_tech` one step later — the same run dies either way, on the same data, in the same job,
  and `data/jobs` is ephemeral, so nothing is banked differently.
- **The scope is now a thing that can be stale.** It rides `data/state`, which is uploaded to HF and
  pulled by the next run's `scrape-plan` and `join`. Every arm is built so a stale copy cannot be
  believed: `join` rewrites it unconditionally, `merge` gets it from the artifact rather than from
  HF, and a run with real records prefers them.
- Reading the file fails **open**. An unreadable or wrong-shaped file logs a warning and falls
  through to deriving the scope — which, without the records, is the tech corpus's Boards: narrower
  than the truth, so it under-evicts rather than over-evicts. That direction is deliberate; the
  opposite one deletes live postings.
- A `merge` that never receives the artifact still degrades the way it already did: no file, no
  records, empty corpus, empty scope, nothing added and nothing evicted.
- It lands in `data/state`, so `merge` publishes it to the HF dataset like every other ledger there:
  ~452 KB per run against the ~1.86 GB a full run already writes — 0.02% of the binding storage
  constraint, and the price of following `unauthoritative_boards.json`'s precedent rather than
  inventing a second inter-job channel.

### Rejected

- **Drop `data/jobs` and scope on the tech corpus.** The silent data loss above. This is the change
  the issue register proposed and it is the reason this ADR exists.
- **Derive the scope from the shard reports.** They key Boards *attempted*, not Boards that emitted
  a line. A Board scraped that yields zero jobs of any kind is deliberately out of scope today
  (ADR-0023 prune owns those rows); reading the reports would silently widen eviction.
- **Re-read `data/jobs` after the union with `iter_jobs`.** Byte-identical to the reference
  implementation, but a second pass over 9.1 GB plus a 2.08 M-id dedup set, for an answer the first
  pass can produce for free.
