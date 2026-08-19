# Embedding throughput on CI (predicting how long a run will take)

Records how we measured how fast `src/headstart/ingest/embed_run.py` actually runs on the
`ubuntu-latest` GitHub Actions runner, and how to predict the wall-clock time a future embedding
task will take. For the vocabulary (**Doc**, **Bucket**, **Batch size**, **Throughput**) see
[`CONTEXT.md`](../../CONTEXT.md#search); this doc is the numbers, not the concepts — operational
data read off real CI logs, not a design decision (contrast the rest of this folder, which is
design/reference for AI retrieval generally).

_Last updated: 2026-07-25._

## Why there's no single number

`embed_run.py` groups Docs into four token-length **Buckets** (512 / 1024 / 2048 / 4096)
because attention cost scales with sequence length squared. To keep peak memory roughly flat,
**Batch size** shrinks per Bucket (`budget ÷ bucket²`), so throughput drops sharply — not
gently — as Bucket size grows. There is no one "docs/sec"; there's a rate per Bucket.

The `pipeline.yml` comment's "~0.5 docs/s" is a rough blended average across whatever bucket
mix a given slice happens to contain — useful as a napkin number, not for predicting a specific
future task.

## Measured rates (from a real run)

Source: `gh run view 30089055186 --log` (nightly-pipeline, 2026-07-24, `ubuntu-latest`, CPU —
no GPU on this runner, so `device="cpu"`, fp32, and the attention budget is quartered vs. the
MPS/fp16 path ADR-0005 describes for local runs). Rates are back-computed from the timestamp
gap between consecutive `[embed] N/total` log lines (tagged `[embed_run] N/total` since the
ADR-0039 logging change), not the coarse rounded `jobs/s` the script itself prints.

| Bucket (tokens) | Batch size | Docs this run | Measured rate | s/doc |
|---|---:|---:|---|---:|
| ≤512 | 32 | 707 (all) | ~1.26 docs/s | ~0.79 |
| ≤1024 | 30 | 653 (all) | ~0.58 docs/s | ~1.73 |
| ≤2048 | 7 | 952 of 3,703 | ~0.23 docs/s | ~4.4 |
| ≤4096 | 1 | not reached | extrapolated ~0.06 docs/s | ~17–18 |

This run hit the step's 100-minute budget (`pipeline.yml`'s `timeout 100m`) partway through the
≤2048 Bucket, at 2,312 of 5,810 Docs — the `timeout ... || echo "embed time budget reached —
banking partial store"` fallback in `pipeline.yml` banked what was done and let the rest of the
pipeline (sync/upload) continue; the remainder resumes via `--resume` next run.

## Where the ≤4096 number comes from

The run never reached the ≤4096 Bucket, so that row is extrapolated, not measured directly —
two things support it:

1. **The math.** `batch_size_for(4096, budget)` on CPU works out to `1` (the CPU attention
   budget is `_ATTN_BUDGET // 4` = 32,000,000; `32,000,000 // 4096² = 1`). No batching
   efficiency survives at the top Bucket — every Doc is encoded fully alone.
2. **A historical anchor already in the code.** The comment above the Bucket-processing loop
   in `embed_run.py` records that an earlier heaviest-Bucket-first ordering once "burned a
   98-min budget on ~325 docs" — 98 × 60 ÷ 325 ≈ **18.1 s/doc**, blended across the heavier
   Buckets that ordering hit first. That lines up closely with the ~17–18 s/doc the seq²
   scaling predicts from the ≤2048 rate, which is why the run switched to ascending-Bucket
   order (cheap Docs bank first, so a time-budgeted run gets more Docs done overall).

## Confirmed on the sharded pipeline (run 30131376268, 2026-07-24)

The first five-stage run (ADR-0025 embed fan-out, ADR-0026 scrape fan-out) reached every Bucket,
so the extrapolated ≤4096 row above is now measured. Shard 2 of 15, 646 Docs, 2,669 s total:

| Bucket (tokens) | Batch size | Docs | Elapsed | s/doc | vs. table above |
|---|---:|---:|---:|---:|---|
| ≤512 | 32 | 198 | 175 s | 0.88 | ~0.79 |
| ≤1024 | 30 | 102 | 202 s | 1.98 | ~1.73 |
| ≤2048 | 7 | 294 | 1,281 s | 4.36 | ~4.4 |
| ≤4096 | 1 | 52 | 1,011 s | **19.4** | ~17–18, extrapolated |

The extrapolation held: `embed_plan`'s `_S_PER_DOC` (0.8 / 1.7 / 4.4 / 18.0) predicted a
42.7-minute makespan against 44.5 minutes actual — 4% error.

**These rates are now stale by design.** They were measured while every batch also encoded a pin
doc plus count-padding — an MPS-only shape workaround that no longer runs on CPU (see the
`BUCKETS` comment in `embed_run.py`). Dropping it should cut ~27% overall and ~50% from the
≤4096 Bucket, whose batch size of 1 meant each Doc was encoded alongside a full-length pin.
Re-measure with the recipe below after the next run and update `_S_PER_DOC`; until then the
planner's makespan prediction reads high.

## The rates above are one rate, not four (ADR-0029)

Read the two tables again by **tokens per second** rather than seconds per Doc:

| Bucket | s/doc | tok/s |
|---|---:|---:|
| ≤512 | 0.88 | 579 |
| ≤1024 | 1.98 | 517 |
| ≤2048 | 4.36 | 470 |
| ≤4096 | 19.44 | 211 — but ~421 once the pin doc is discounted |

Throughput per token is **flat**. If attention's O(seq²) dominated, tok/s would fall ~8× from ≤512
to ≤4096; it falls 2.7×, and essentially all of that was the MPS-only pin doc doubling work at
batch 1. **Embedding cost is linear in total tokens.** At 470 tok/s for a 137M-parameter model that
is ~129 GFLOP/s sustained, so the encoder is **compute-bound** — the work is in the FFN/linear
GEMMs, not in attention.

### What the runner actually is (measured 2026-07-25, run `30154750453`)

`ubuntu-latest` is an **AMD EPYC 9V74** (Zen 5: AVX-512 with VNNI, so int8 is well supported by the
hardware), and **torch reports 2 threads, not 4** — the 4 vCPUs are 2 physical cores plus SMT, and
torch sizes its intra-op pool by physical cores. Nothing in the workflow sets `OMP_NUM_THREADS`.

That revises the roofline claim rather than confirming it. Two Zen 5 cores with AVX-512 peak near
**350–410 GFLOP/s** fp32, so the measured 129 is **~31–37% of peak**, not at it. About a third of
peak is ordinary for real transformer inference — non-GEMM ops, memory-bound layernorm/softmax,
imperfect blocking — so "compute-bound" stands, but **"at roofline" was too strong, and kernel or
threading efficiency is not fully ruled out as a lever.** Raising thread count to 4 to use the SMT
siblings is the cheap experiment; it usually gains little on FMA-saturated GEMM code and can lose
to contention, which is presumably why torch defaults to physical cores — but it is untested here.

Two things follow, and both are counter-intuitive:

1. Only *fewer tokens*, *fewer FLOPs per token*, or *more FLOP/s* can speed this up. Batch tuning
   cannot — the GEMMs already saturate. `batch_size_for(4096) == 1` looks like a bug and is not one.
2. The ≤4096 Bucket is loud but cheap. It is **3.2%** of Docs; truncating every Doc at 2,048 tokens
   would save **1.2%** of total compute.

### Measured Doc-length distribution (4,000 real English Docs, real tokenizer, 2026-07-25)

| p10 | p25 | p50 | p75 | p90 | p95 | p99 | mean |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 641 | 850 | 1,040 | 1,267 | 1,549 | 1,804 | 2,550 | 1,089 |

Bucket mix 5.9 / 41.9 / 48.9 / 3.2%. Compute retained by truncation cap: 2048 → 98.8%,
1536 → 96.0%, 1024 → 83.4%, 768 → 67.3%, 512 → 46.3%.

### Padding was a quarter of the bill

A batch pads to its longest member, and batches were ordered by board priority (ADR-0022), which is
uncorrelated with length:

| ordering | padded tokens | overhead vs true |
|---|---:|---:|
| priority (pre-ADR-0029) | 5,359,358 | **+23.0%** |
| length-sorted, window 8 batches (now) | 4,522,190 | +3.8% |
| fully length-sorted | 4,373,370 | +0.4% |

`_encode_groups` now length-sorts within windows of `batch × 8`, worth ~15.6% less compute for
semantically identical vectors (padding changes GEMM shapes, so values drift a few ulps — well
inside `pipeline-smoke`'s `atol=1e-4` mono-vs-sharded check). Saving by window size: 2 → 8.0%,
4 → 12.8%, 8 → 15.6%, 16 → 17.0%, full sort → 18.4%; eight keeps priority order to within eight
batches.

**When re-measuring after ADR-0029, expect the per-Bucket s/doc figures to drop by roughly this
much on top of the pin-doc fix** — and recalibrate `_S_PER_DOC` from the new numbers.

## Corpus scale (the other half of the arithmetic)

Rates alone don't predict a run — you also need how many Docs the run will see. Read that from
the liveness ledger and the store. **Do not scale a slice's line count by its Board count:** the
priority-first slice under-samples postings per Board (run 30131376268 covered 15.6% of Boards
but only 11.3% of ledger postings), and compounding that error once understated a full pass by
about 10x.

**Nor apply the blended tech keep rate to the ledger.** The run's headline 31.8% is a blend over
the _slice_, which is priority-first and therefore rich in tech-dense ATSes. The _ledger_ is
dominated by Workday — 71% of all live postings — and Workday keeps only **6.9%**. Weighting each
ATS by its own rate gives a ledger-wide keep of **14.9%**, less than half the slice blend. Always
project per ATS.

Live Boards on enabled ATSes (`join` sits in `registry.DISABLED_ATS`), as of 2026-07-25. Keep
rates are the per-ATS column of the tech filter in run 30131376268:

| ATS | Live postings | Keep % | Projected tech |
|---|---:|---:|---:|
| workday | 2,454,364 | 6.9 | 169,351 |
| greenhouse | 196,308 | 36.7 | 72,045 |
| eightfold | 122,541 | 52.7 | 64,579 |
| smartrecruiters | 247,344 | 25.8 | 63,815 |
| zoho | 102,170 | 38.1 | 38,927 |
| recruitee | 62,633 | 39.3 | 24,615 |
| ashby | 53,222 | 42.0 | 22,353 |
| lever | 48,884 | 36.0 | 17,598 |
| successfactors | 38,554 | 20.9 | 8,058 |
| rippling | 23,229 | 32.9 | 7,642 |
| teamtailor | 25,549 | 21.5 | 5,493 |
| ripplehire | 15,526 | 32.9 | 5,108 |
| keka | 12,663 | 34.5 | 4,369 |
| freshteam | 7,903 | 45.4 | 3,588 |
| personio | 17,090 | 14.6 | 2,495 |
| trakstar | 4,707 | 40.0 | 1,883 |
| darwinbox | 7,617 | 18.8 | 1,432 |
| workable | 3,506 | 20.1 | 705 |
| **TOTAL** | **3,443,810** | **14.9** | **~514,000** |

### Three different numbers, and which one you want

The single biggest source of error here is conflating quantities that differ by 2-3x. Keep them
apart:

| Quantity | Value | What it is |
|---|---:|---|
| Live tech postings at full coverage | ~514,000 | **Projected.** Every live Board scraped. A snapshot ceiling, not an accumulation |
| Vectors in the store (`manifest.json`) | 263,769 | **Measured.** Every English tech Doc _ever_ embedded, dead ones included — append-only, never GC'd |
| Rows in the served `jobs` table | ~200,000 | **Measured.** Currently open, on a still-live Board, English, embedded, not pruned. This is the UI's number |

The UI's ~200k is the only one describing what a user can search. It sits below 514,000 for two
reasons that have nothing to do with each other: **coverage** (the rotating slice has only ever
reached part of the live set) and the **English gate** (a fixed share of the tech corpus is never
indexed by design — CLAUDE.md).

And the store _exceeds_ the served table by ~64,000 because it is a historical accumulation: a
Board that posted 100 Jobs in June and 100 different ones in July contributes 200 store rows and only
100 live rows. It still holds 1,093 `join` rows even though `join` is in `registry.DISABLED_ATS`.

### Measured coverage (store `meta.jsonl`, 2026-07-25)

Counting the store by ATS and by Board key is the honest coverage measure, and it is one cheap
download. The store spans **16,608 distinct Boards of 51,314 live** — 32%.

| ATS | In store | Projected live tech | Store ÷ projection |
|---|---:|---:|---:|
| workday | 112,646 | 169,351 | 67% |
| greenhouse | 50,892 | 72,045 | 71% |
| ashby | 22,696 | 22,353 | 102% |
| zoho | 21,850 | 38,927 | 56% |
| lever | 12,070 | 17,598 | 69% |
| smartrecruiters | 10,507 | 63,815 | 16% |
| recruitee | 8,443 | 24,615 | 34% |
| ripplehire | 4,549 | 5,108 | 89% |
| eightfold | 3,787 | 64,579 | 6% |
| workable | 3,533 | 705 | 501% |
| keka | 3,025 | 4,369 | 69% |
| rippling | 2,357 | 7,642 | 31% |
| teamtailor | 1,592 | 5,493 | 29% |
| darwinbox | 1,550 | 1,432 | 108% |
| personio | 1,033 | 2,495 | 41% |
| freshteam | 970 | 3,588 | 27% |
| successfactors | 595 | 8,058 | 7% |
| trakstar | 581 | 1,883 | 31% |
| **TOTAL** | **263,769** | **514,056** | **51%** |

Read that last column carefully — it is a ratio between an accumulation and a snapshot, so it can
legitimately exceed 100%. Where it does (ashby, darwinbox, and workable at 501%), the ledger's
posting counts are stale-low for a high-churn ATS, which means ~514,000 is if anything a _floor_.
Where it is far below (eightfold 6%, successfactors 7%, smartrecruiters 16%), those ATSes are
genuinely under-scraped and are where raising `--max-boards` pays.

### Backlog

The **English gate is the least-known term**. In run 30131376268, 6,757 of the 16,465
not-already-embedded Docs (41%) were dropped as non-English — but the corpus-wide rate can't be
read off one run, because already-embedded ids are skipped _before_ the gate and those are English
by construction, so the fresh set is enriched in non-English. Bracketing at 20-41% puts the live
embeddable corpus at 303,000-411,000 against ~200,000 currently served, so the **full-coverage
backlog is roughly 100,000-210,000 Docs**.

Capacity per run is `shards × budget_minutes × 60 ÷ s_per_doc`. At 15 shards, a 200-minute embed
budget, and 2.89 s/Doc (projected post-pin-fix blended rate), that is ~62,000 Docs — so the
catch-up is **2-4 runs**, after which each run carries only genuinely new postings.

Measuring the corpus-wide English fraction directly is the highest-value number still missing
here, and a store GC is the other gap: nothing prunes dead vectors, so the store-to-index gap only
grows.

## Predicting a future embedding task

1. Tokenize the target corpus with the real tokenizer — not a character-count estimate.
   `embed_run.py` measures every Doc's exact token count via `model.tokenizer(...)` before
   bucketing; it never guesses from character length, because a char-based estimate undershoots
   on token-dense text (e.g. a bilingual description whose CJK tail tokenizes at ~1 token/char).
2. Sort each Doc into a Bucket by its token count.
3. `predicted_seconds ≈ Σ (docs_in_bucket × s/doc for that bucket)`, using the table above.
4. Add a small fixed overhead for model load + the tokenizing pass — on the order of seconds
   to roughly a minute for a few thousand Docs; negligible next to bulk embedding time once the
   corpus is more than a handful of Docs.

Treat the result as an estimate, not a guarantee: `ubuntu-latest` is a shared runner, so
run-to-run variance from host contention is real. That's presumably why the pipeline itself is
built to time-box and resume (`timeout 100m ... --resume`) rather than schedule against a
predicted duration.

## How to refresh these numbers

```bash
# recent runs of the nightly pipeline
gh run list --workflow pipeline.yml --limit 5 --json databaseId,createdAt,conclusion

# bucket boundaries + the final summary line
gh run view <run-id> --log | grep -E "\[embed\] bucket|to embed:|done: embedded"

# full per-batch throughput stream (compute real rates from the timestamps, not the
# rounded jobs/s the script prints)
gh run view <run-id> --log | grep -E "\[embed\]"
```

## Files

- [`src/headstart/ingest/embed_run.py`](../../src/headstart/ingest/embed_run.py) — the embed step; see the
  `BUCKETS` / `_ATTN_BUDGET` / `batch_size_for` block for the bucketing logic itself.
- [`.github/workflows/pipeline.yml`](../../.github/workflows/pipeline.yml) — the nightly job
  that runs it, its `timeout 100m` budget, and the `--resume` continuation.
- [`docs/adr/0005-embedding-model.md`](../adr/0005-embedding-model.md) — why
  `nomic-embed-text-v1.5`, and the local-MPS numbers this doc's CI numbers diverge from.
