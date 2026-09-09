# ADR-0120: The trends ledger is Parquet, not CSV

**Status:** accepted · **Date:** 2026-09-09 · **Changes the storage format of `data/state/role_trends.csv` (ADR-0040, ADR-0051, ADR-0075). No schema change, no API field change, no row lost.**

## Context

`data/state/role_trends.csv` is append-only and re-uploaded **whole** by the `merge` job on every
pipeline run. Measured on the file pulled fresh from HF on 2026-09-09:

| | |
|---|---|
| size | **172,537,804 bytes** |
| rows | **2,468,569** |
| distinct timestamps | **510** |
| appended per run | **6,767–6,777 rows** (~470 KB), across the four runs of 2026-09-09 |

That is roughly **366:1 write amplification** — ~1 GB/day of upload to add ~3 MB/day of data —
against what CLAUDE.md names as this workflow's binding cost constraint. The merge log shows the
whole-file push in as many words: `role_trends.csv: 0%| | 0.00/172M`.

ADR-0040 sized this file at "a few dozen rows per run" and explicitly deferred any retention
policy. ADR-0051's fifteen watched roles and ADR-0075's per-ATS decomposition each multiplied the
per-run row count; nothing revisited the estimate. `docs/product/2026-08-14_adversarial-audit.md`
flagged the trajectory — "a ledger that crosses HF's LFS threshold ~2026-09-12 and already parses
into ~1 GB RSS at one-year scale on every one of ~11 daily boots" — but named no fix.

**There is a second cost on the read side.** `deploy/hf-space/app.py` downloads this same file on
every Space cold start and loads all 2.47M rows into `_TRENDS`, filtering in Python per request.
The file is expensive to write *and* expensive to serve.

The data is extraordinarily compressible and nobody had checked. 510 distinct timestamps repeat
across 2.47M rows; `metric`, `family`, `band` and `ats` are all small closed vocabularies; `ts` is
stored as a 25-byte ISO string that is really an 8-byte instant.

## Decision

**Store the ledger as a single Parquet file with zstd compression and dictionary encoding:
`data/state/role_trends.parquet`.**

Measured on the real 2,468,569-row ledger:

| format | bytes | |
|---|---|---|
| CSV | 172,537,804 | |
| Parquet + zstd + dictionary | **3,430,805** | **50.3x smaller** |

Schema — seven columns, unchanged in name, order and meaning from the CSV:

```
ts: timestamp[ms, tz=UTC], version: int64, metric: string,
family: string, band: string, ats: string, count: int64
```

`ts` is **milliseconds, not seconds**. Parquet's logical timestamp types start at MILLIS, so a
`timestamp[s]` column is silently written as `timestamp[ms]` and reads back that way — declaring
seconds makes the *second* append raise on `concat_tables`, the ledger read from disk being `ms`
and the run's fresh rows `s`. This was found by running two appends against the real ledger, not
by reading the code. Every stamp the writer emits is a whole second regardless.

### What follows from 3.4 MB

**One file, not partitioned by date.** Partitioning would save ~3.4 MB per run and cost a directory
layout, a migration and a merge step. At this size the whole-file rewrite the merge job already
performs is cheaper than the machinery to avoid it.

**No retention or rollup policy.** ADR-0040 deferred one; at 50.3x it stays deferred. The ledger
holds 510 timestamps in 3.4 MB, so a year of history is affordable without pruning anything. This
is worth stating because retention is the obvious reflex for a file that grew 366:1, and it is the
wrong one — the file was never too *long*, it was too *wide per row*.

**Parquet has no append.** `append_ledger` reads the file, concatenates, and writes it back through
a temp file and a rename. That rewrite is the *cheap* half: 3.4 MB rewritten beats 172 MB appended
to, and the atomic rename removes a failure mode the CSV had — a run killed mid-write left a 0-byte
or truncated ledger, and now leaves the previous one intact.

### Migration and cutover

A pre-ADR-0120 CSV sitting beside the ledger is **folded in on the first Parquet write**, once,
carrying every historical row (verified below). The three known CSV shapes — pre-ADR-0051 (no
`metric`), pre-ADR-0075 (no `ats`), and the current seven-column form — are each normalised as they
were before; that logic moved rather than disappeared.

**The stale CSV must be retired by hand.** The merge job uploads `data/state` as a *folder* with
`hf upload` and **without `--delete`** (only `cleanup-index` passes it), so the 172 MB CSV survives
on HF until something removes it explicitly. The writer deliberately does not delete it: removing
it locally would not retire the remote copy, and would only make a re-run re-migrate. Retirement is
a one-time `HfApi().delete_file("data/state/role_trends.csv", ...)` **after** the first Parquet has
landed — in that order, so the ledger is never absent from the dataset. Until it runs, this change
has added 3.4 MB rather than saved 169 MB.

**The rollout has a window.** The pipeline writes and the Space reads, and they deploy
independently: `deploy-space.yml` pushes on any `deploy/hf-space/**` change to main, so the Space
picks up the Parquet reader as soon as this merges, while the Parquet file only appears when the
next `merge` runs. In between, the Space finds neither file and the trends panel goes **dark rather
than broken** — `_load_trends` returns `[]`, `/trends` answers 503, and `trends_on` hides the panel.
That is the same dark-until-ready shape the Space already uses for the alerts panel and the sign-in
wall, and it is verified by test, not assumed. The window is bounded by the pipeline's own cadence
(ADR-0093 chains its successor roughly hourly).

## Consequences

- The `merge` job's per-run upload for this file drops from ~172 MB to ~3.4 MB.
- The Space's cold-start download drops by the same factor. It still holds every row in memory and
  filters in Python — this ADR does not change the read path's shape, only what it reads.
- **`pyarrow` is now declared in `deploy/hf-space/requirements.txt`.** It previously arrived only
  transitively via `lancedb`. This repo has already been bitten once by exactly that: `requests`
  arrived transitively via `huggingface_hub`, which moved to httpx in 1.0, and every state fetch
  died on `ModuleNotFoundError`. Declared, not assumed.
- The ledger is no longer readable with `head`, `wc -l` or a spreadsheet. This is a real loss for
  ad-hoc inspection, accepted because the file had already passed the size where those tools were
  usable — `wc -l` on 172 MB is not an inspection anyone was performing.
- **`_load_trends` no longer runs in CI.** The route tests used to exercise it by reading a CSV
  fixture; a Parquet reader cannot be tested without pyarrow, which is not in the `[dev]` extra the
  quality job installs. The route tests were therefore changed to build their rows directly — so
  they keep running in CI, which is why `flask` is in `[dev]` at all — and the loader got its own
  pyarrow-gated test that runs locally. Adding pyarrow to `[dev]` would fix the gap but pulls a
  large binary wheel into a job whose stated purpose is to stay light; not worth it for one
  function, and the loader is covered by the migration evidence below.
- **The read-side saving does not arrive until the CSV is retired.** `join` fetches
  `data/state/*`, so until the delete runs it still downloads the 172 MB CSV every run and passes
  it to `merge` through the `corpus-state` artifact. Retirement is what banks the win on both
  sides, not the format change alone.
- **A pre-existing hazard the migration inherits, unchanged.** `merge` gets `data/state` only from
  the `corpus-state` artifact, whose download is `continue-on-error: true`. If it is missing, the
  ledger is absent and this step writes a fresh one — which the upload then publishes over the
  real one. That was equally true of the CSV (a 2-row CSV would have replaced the 172 MB file the
  same way), so this ADR neither introduces nor fixes it; it is called out because the migration
  has a one-shot flavour that makes it look new. It is mitigated in practice by not deleting the
  CSV until the landed Parquet has been checked for its full row count.
- **The `/trends` route still compares `ts` as a string.** `_load_trends` renders the timestamp
  column back to the ledger's own `+00:00` whole-second spelling, so the date filters' semantics
  are byte-identical across the format change. Handing the route a `datetime` would raise on the
  first `r["ts"] >= since`; handing it a differently-spelled string would silently reselect.

## Verification

All figures measured on the real ledger pulled from HF on 2026-09-09, not on a fixture.

- **Round-trip, row-for-row:** all **2,468,569** rows compared field-by-field between the source
  CSV and the Parquet written by the pipeline's own `append_ledger` — **0 mismatches, 0 rows left
  over**. Content equality, not a row count.
- **Filter equivalence:** `deploy/hf-space/app.py` loaded through the repo's own stub harness and
  the real `/trends` route driven twice over the same ledger — once with the pre-ADR-0120 CSV
  loader, once with the Parquet loader — across **16 queries** covering `since`, `until`, both
  together, a `Z`-suffixed millisecond bound, a naive (timezone-less) bound, single and multiple
  `ats`, `metric=new`, the family and roles drills, an out-of-range window and a malformed bound.
  **All 16 byte-identical** (sha256 of the response body), including the 400s and the
  200-with-empty-series. The loaders' 2,468,569 output rows are themselves identical.
- **Consecutive appends:** three appends in sequence against the migrated ledger produce exactly
  the expected row counts and hold the file at ~3.43 MB. This is the check that caught the
  `timestamp[s]` defect above.
- **Migration cost:** the one-time CSV fold-in peaks at **1.64 GB RSS** and takes **2.8 s** —
  affordable on a standard GitHub runner. Steady-state appends read a 3.4 MB file instead.
