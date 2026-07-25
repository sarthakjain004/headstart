# ADR-0029: Embedding cost is linear in tokens — length-sort batches, and measure backends before adopting one

- Status: Accepted (length-sorting); backend choice **Proposed**, pending the CI benchmark
- Date: 2026-07-25
- Corrects the cost model implied by [ADR-0005](0005-embedding-model.md)'s bucket/batch design for
  the CPU runners of [ADR-0025](0025-parallelize-nightly-pipeline.md). Trades against
  [ADR-0022](0022-tech-priority-board-ordering.md)'s within-Bucket priority order, deliberately and
  boundedly. Same model, prefix, and dimensionality (ADR-0005), so no re-embedding is implied.

## Context

The embed stage dominates the 6-hourly run, and every piece of machinery around it — four
token-length **Buckets**, a per-Bucket batch size of `32M ÷ bucket²`, shape pinning — was designed
in ADR-0005 for a **local Apple M5 Pro on MPS**. That ADR says outright: *"Speed is a non-factor on
the M5 Pro."* Production embedding now runs on **4-vCPU `ubuntu-latest` runners**, where speed is
the entire problem. The design was tuned for a machine the code no longer runs on.

Re-reading the measured per-Bucket rates (run `30131376268`, shard 2, 646 Docs / 2,669 s) with that
in mind shows the assumed cost model is wrong:

| Bucket | Batch | Docs | Secs | s/doc | tok/s |
|---|---:|---:|---:|---:|---:|
| ≤512 | 32 | 198 | 175 | 0.88 | 579 |
| ≤1024 | 30 | 102 | 202 | 1.98 | 517 |
| ≤2048 | 7 | 294 | 1,281 | 4.36 | 470 |
| ≤4096 | 1 | 52 | 1,011 | 19.44 | 211 → ~421 without the pin doc |

**Throughput per token is flat.** If attention's O(seq²) dominated, tok/s would fall ~8× from ≤512
to ≤4096; it falls 2.7×, and almost all of that was the MPS-only pin doc doubling work at batch 1.
So **cost is linear in total tokens**, not quadratic in sequence length.

At 470 tok/s for a 137M-parameter model that is ~129 GFLOP/s sustained. **The encoder is
compute-bound** — the work is in the FFN/linear GEMMs, not attention. (Later measurement put the
runner at 2 physical Zen 5 cores, peaking near 350–410 GFLOP/s, so this is ~1/3 of peak rather
than at it; see `embedding-throughput.md`. Ordinary for transformer inference, but it means
kernel/threading efficiency isn't fully excluded as a lever.) Three things can therefore make it
faster: fewer tokens, fewer FLOPs per token, or more FLOP/s. Batch-size tuning is not among them — which matters, because `batch_size_for(4096) == 1`
looks like the obvious bug and is not one. Only **3.2%** of Docs are in that Bucket.

Measured over 4,000 real English Docs with the real tokenizer: median **1,040** tokens, p90 1,549,
p99 2,550; Bucket mix 5.9 / 41.9 / 48.9 / 3.2%. Truncating at 2,048 would save **1.2%** of compute.
The top Bucket is loud but cheap.

The real waste is padding. Every batch is padded to its longest member, and batches are ordered by
**board priority** (ADR-0022), which is uncorrelated with length. Over the same 4,000 Docs:

| ordering | padded tokens | overhead vs true |
|---|---:|---:|
| priority (today) | 5,359,358 | **+23.0%** |
| length-sorted, window 8 batches | 4,522,190 | +3.8% |
| fully length-sorted | 4,373,370 | +0.4% |

Nearly a quarter of all embedding compute is spent on padding that carries no information.

## Decision

**1. Length-sort within priority windows.** In `_encode_groups`, reorder each Bucket's indices by
exact token count within windows of `batch_size × 8` before batching. The planner already computes
exact counts, so they ride in the assignment record (`tokens`) rather than being re-derived — a
character-length proxy misorders token-dense text, the same reason the planner tokenizes for real.

Window size is the ADR-0022 trade, and it is bounded on purpose. Measured saving by window:
1 (today) 0%, 2 → 8.0%, 4 → 12.8%, **8 → 15.6%**, 16 → 17.0%, full sort → 18.4%. Eight captures 85%
of what is available while keeping priority order to within eight batches (±56 Docs in the ≤2048
Bucket, out of ~1,950). A time-boxed shard still banks its highest-value Docs first; it simply pays
far less padding to do so.

Each Doc's own tokens are identical and nothing re-embeds — this only reorders *which Docs share
a batch*. Vectors are therefore **semantically** unchanged, not bit-identical: batch composition
sets the padded sequence length, which changes GEMM shapes and hence float reduction order, so
values drift by a few ulps. `pipeline-smoke` is the guard — it embeds one corpus through both the
monolith and the sharded path and asserts agreement within `atol=1e-4`, several orders of magnitude
above that drift. It passed on this change.

**2. Do not adopt a quantized backend on published numbers.** ONNX-int8 (3.08×) and OpenVINO-int8
(4×) are the standard CPU lever, and pre-built int8 artifacts already ship on the nomic repo
(`onnx/model_int8.onnx`, `onnx/model_quantized.onnx`) so no conversion is needed. But
sentence-transformers' own guidance is that those figures come from short (STSB-length) text, and
that *"for longer texts, ONNX and OpenVINO can even perform slightly worse than PyTorch"*. Our
median Doc is 1,040 tokens. The published speedups do not transfer.

So: `scripts/bench/embed_backends.py` + the `embed-bench` workflow measure torch-fp32 (baseline),
torch-bf16, ONNX fp32/int8/quantized, and OpenVINO on **`ubuntu-latest`, at our measured length
distribution**, reporting tok/s and mean cosine agreement against fp32. A backend is adopted only
if it wins there, and a *quantized* backend additionally requires a retrieval check on the
ADR-0011 eval harness — cosine agreement on synthetic text is a drift signal, not a quality gate.

**A dependency constraint may decide this before throughput does.** The backend extras do not
coexist with the transformers 5.x production runs: `optimum-intel` (the OpenVINO path) requires
`transformers<4.58`, so installing them resolves transformers down to **4.57.6**; pinning
`transformers>=5` instead backtracks `optimum` to 1.17.1. On 4.x, `nomic_bert` is not yet a native
architecture and needs `trust_remote_code=True` — which is why the first `embed-bench` dispatch
(run `30154750453`) skipped all six variants. So even a backend that wins on tok/s would cost a
two-major-version transformers downgrade in production. The benchmark now prints its resolved
versions so that trade is visible in the result rather than inferred afterwards.

Runner facts worth recording, from that first dispatch: `ubuntu-latest` was an **AMD EPYC 9V74**
(Zen 5 — AVX-512 with VNNI, so int8 is well supported by the hardware), and torch reports
**2 threads**, not 4 — the 4 vCPUs are 2 physical cores with SMT.

**That CPU model is not stable across runs.** The later thread sweep (`30160339492`) landed on an
**EPYC 7763** — Zen 3, AVX2, *no* AVX-512. The `ubuntu-latest` fleet is heterogeneous, so two
things follow: absolute tok/s from different runs are not comparable unless both report the same
part, and the int8-support argument above holds only on the Zen 5 draw. Compare runs by **ratio
within a single job**, never by rate across jobs. The core topology (2 physical + SMT) was the same
on both, which is why the threading result below transfers even though the rates do not.

## Alternatives considered

- **Truncate Docs.** Directly attacks the dominant term (tokens), and cheap to try. But capping at
  2,048 saves 1.2% and capping at 1,024 saves 16.6% while touching 52% of Docs — and it contradicts
  ADR-0005's central rationale, which rejected 512-token models precisely to avoid truncating the
  82% of descriptions that overflow. Deferred until the eval harness can say whether description
  tails carry retrieval signal or are boilerplate.
- **Raise the CPU attention budget / batch size.** The intuitive fix for `batch_size_for(4096) == 1`.
  Rejected on the roofline evidence: throughput is already flat per token, so the GEMMs are large
  enough, and the Bucket in question is 3.2% of Docs.
- **Force 4 intra-op threads (use the SMT siblings).** Left untested when this ADR was written, and
  arguable both ways: SMT fills memory stalls, and at ~1/3 of peak the core is clearly stalling
  somewhere. **Measured and rejected** — run `30160339492`, 9 interleaved passes (threads 1,2,4 ×
  3 repeats × 80 Docs, fixed seed, one process per thread count with `OMP_NUM_THREADS` set before
  start, all on one machine):

  | threads | median tok/s | spread | vs default |
  |---|---:|---:|---:|
  | 1 | 287 | 0.7% | 0.51× |
  | 2 (default) | 558 | 0.5% | 1.00× |
  | 4 | 535 | 0.4% | **0.96×** |

  1→2 scales 1.94×, so the harness resolves a real threading effect; 2→4 then costs **4.1%**, about
  six times the within-variant spread. Wall-clock per pass agrees independently (5m35s / 2m57s /
  3m04s, stable to ±1 s across repeats). Contention for the shared arithmetic units beats the
  stall-filling argument, as the textbook case for large GEMMs predicts. **Torch's default of 2 is
  already optimal — leave `OMP_NUM_THREADS` unset.**
- **A smaller or static model** (bge-small class at 33M; model2vec `potion-retrieval-32M`, orders of
  magnitude faster). The largest available win, since FLOPs/token is the dominant term. But
  `potion-retrieval-32M` reaches 86.65% of *all-MiniLM-L6-v2*, itself well below nomic on retrieval,
  and static embeddings have no long-context modelling at all — the opposite of what ADR-0005
  optimised for. This is a product-quality decision, not a performance one; it belongs behind the
  eval harness in its own ADR.
- **Larger runners.** Outside the free-tier constraint of ADR-0020.

## Consequences

- Expected ~15.6% less embedding compute for semantically identical vectors. The next run's `[embed]` rates are the
  check; `embed_plan`'s `_S_PER_DOC` should be recalibrated from them (it already reads ~27% high
  post-pin-fix, so this compounds with a correction that was already outstanding).
- The assignment record gains `tokens`. A record without it falls back to the Bucket cap — a safe
  upper bound — so an older assignment still embeds, just with coarser sorting.
- `docs/AI_Integration/embedding-throughput.md` inherits a new framing: its per-Bucket s/doc table
  is a *derived* view of one underlying rate (~470 tok/s), not four independent constants.
- The benchmark's backend extras (`optimum[onnxruntime]`, `openvino`, `optimum-intel`) are installed
  only in the `embed-bench` workflow, never in `[embed]`. Adopting a winner is a separate change
  that moves the dependency into production.
