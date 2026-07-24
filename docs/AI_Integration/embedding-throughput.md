# Embedding throughput on CI (predicting how long a run will take)

Records how we measured how fast `scripts/embed/embed_jobs.py` actually runs on the
`ubuntu-latest` GitHub Actions runner, and how to predict the wall-clock time a future embedding
task will take. For the vocabulary (**Doc**, **Bucket**, **Batch size**, **Throughput**) see
[`CONTEXT.md`](../../CONTEXT.md#search); this doc is the numbers, not the concepts — operational
data read off real CI logs, not a design decision (contrast the rest of this folder, which is
design/reference for AI retrieval generally).

_Last updated: 2026-07-24._

## Why there's no single number

`embed_jobs.py` groups Docs into four token-length **Buckets** (512 / 1024 / 2048 / 4096)
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
gap between consecutive `[embed] N/total` log lines, not the coarse rounded `jobs/s` the script
itself prints.

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
   in `embed_jobs.py` records that an earlier heaviest-Bucket-first ordering once "burned a
   98-min budget on ~325 docs" — 98 × 60 ÷ 325 ≈ **18.1 s/doc**, blended across the heavier
   Buckets that ordering hit first. That lines up closely with the ~17–18 s/doc the seq²
   scaling predicts from the ≤2048 rate, which is why the run switched to ascending-Bucket
   order (cheap Docs bank first, so a time-budgeted run gets more Docs done overall).

## Predicting a future embedding task

1. Tokenize the target corpus with the real tokenizer — not a character-count estimate.
   `embed_jobs.py` measures every Doc's exact token count via `model.tokenizer(...)` before
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

- [`scripts/embed/embed_jobs.py`](../../scripts/embed/embed_jobs.py) — the embed step; see the
  `_BUCKETS` / `_ATTN_BUDGET` / `batch_size_for` block for the bucketing logic itself.
- [`.github/workflows/pipeline.yml`](../../.github/workflows/pipeline.yml) — the nightly job
  that runs it, its `timeout 100m` budget, and the `--resume` continuation.
- [`docs/adr/0005-embedding-model.md`](../adr/0005-embedding-model.md) — why
  `nomic-embed-text-v1.5`, and the local-MPS numbers this doc's CI numbers diverge from.
