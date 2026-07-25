#!/usr/bin/env python3
"""Benchmark embedding backends at HeadStart's real Doc lengths (ADR-0029).

Answers one question: **on the 4-vCPU `ubuntu-latest` runner the embed shards actually run on,
does an ONNX/OpenVINO/int8 backend beat the current torch-fp32 path for Docs of our length?**

That qualifier is the whole point. sentence-transformers' published backend speedups (ONNX-int8
3.08x, OpenVINO-int8 4x) are measured on STSB-length text, and its own docs warn that "for longer
texts, ONNX and OpenVINO can even perform slightly worse than PyTorch". HeadStart's Docs have a
median of ~1,040 tokens, so the published numbers do not transfer and have to be re-measured.

**Corpus.** Synthetic Docs sampled to the *measured* token-length distribution of the real tech
corpus (p50 1040, p90 1549, p99 2550; Bucket mix 5.9/41.9/48.9/3.2% — see
`docs/AI_Integration/embedding-throughput.md`). Backend throughput depends on token counts and
batch shapes, not on what the words mean, so synthetic text is sound for the *speed* question.
It is NOT sound for the quality question: the `agree` column below (mean cosine vs the fp32
baseline) is a drift signal only — confirm any int8 adoption on the retrieval eval harness
(ADR-0011) before shipping it.

Run: python -m scripts.bench.embed_backends           # or: python scripts/bench/embed_backends.py
     python scripts/bench/embed_backends.py --docs 200 --backends torch-fp32,onnx-int8
"""

from __future__ import annotations

import argparse
import gc
import random
import sys
import time

import numpy as np

from headstart.ingest.doc_prep import _BUCKETS, _MAX_SEQ_TOKENS
from headstart.ingest.embed_run import _length_sorted, batch_size_for
from headstart.search import DOC_PREFIX, MODEL

# Measured token-length distribution of the real tech corpus (4,000 English Docs, real
# tokenizer). Sampling from this rather than a flat length keeps the batch shapes honest.
_PERCENTILES = [
    (0.10, 641),
    (0.25, 850),
    (0.50, 1040),
    (0.75, 1267),
    (0.90, 1549),
    (0.95, 1804),
    (0.99, 2550),
    (1.00, 4096),
]

# Each variant: (label, backend, file_name or None, dtype hint or None).
_VARIANTS: dict[str, tuple[str, str | None, str | None]] = {
    "torch-fp32": ("torch", None, None),  # production today
    "torch-bf16": ("torch", None, "bfloat16"),  # free if the runner has AVX512-BF16/AMX
    "onnx-fp32": ("onnx", "onnx/model.onnx", None),
    "onnx-int8": ("onnx", "onnx/model_int8.onnx", None),
    "onnx-quantized": ("onnx", "onnx/model_quantized.onnx", None),
    "openvino-fp32": ("openvino", None, None),
}


def _sample_lengths(n: int, seed: int = 0) -> list[int]:
    """Token lengths drawn from the measured distribution by piecewise-linear inverse CDF."""
    rng = random.Random(seed)
    out: list[int] = []
    for _ in range(n):
        u = rng.random()
        prev_p, prev_v = 0.0, 128
        for p, v in _PERCENTILES:
            if u <= p:
                frac = (u - prev_p) / (p - prev_p) if p > prev_p else 0.0
                out.append(int(prev_v + frac * (v - prev_v)))
                break
            prev_p, prev_v = p, v
    return out


def _make_docs(lengths: list[int], tokenizer) -> list[str]:
    """Docs of (approximately) the requested token lengths, from job-description vocabulary.

    Built by repeating a realistic sentence pool and trimming to the target token count with the
    real tokenizer, so each Doc lands in the Bucket its length implies.
    """
    pool = (
        "We are hiring a senior backend engineer to design and operate distributed services. "
        "You will build APIs in Python and Go, own reliability, and mentor other engineers. "
        "Requirements include strong data modelling, testing discipline, and cloud experience. "
        "Benefits include health insurance, equity, flexible hours, and a learning budget. "
    )
    docs: list[str] = []
    for i, target in enumerate(lengths):
        text = pool * (target // 40 + 2)
        ids = tokenizer(text, truncation=True, max_length=target)["input_ids"]
        docs.append(DOC_PREFIX + tokenizer.decode(ids, skip_special_tokens=True))
        if (i + 1) % 100 == 0:
            print(f"  built {i + 1}/{len(lengths)} docs", file=sys.stderr, flush=True)
    return docs


def _load(label: str):
    """Load one variant. Raises if its backend dependency is absent — ``main`` skips on that."""
    from sentence_transformers import SentenceTransformer

    backend, file_name, dtype = _VARIANTS[label]
    kwargs: dict = {}
    if file_name:
        kwargs["file_name"] = file_name
    model = SentenceTransformer(MODEL, backend=backend, model_kwargs=kwargs or None)
    if dtype == "bfloat16":
        model = model.bfloat16()
    model.max_seq_length = min(model.max_seq_length, _MAX_SEQ_TOKENS)
    return model


def _encode_bucketed(model, docs: list[str], lengths: list[int], budget: int):
    """Encode exactly as production does: grouped by Bucket, batched by ``batch_size_for``,
    length-sorted within the batch (ADR-0029). Returns (vectors_in_input_order, seconds)."""
    order: list[int] = []
    groups: dict[int, list[int]] = {b: [] for b in _BUCKETS}
    for i, n_tok in enumerate(lengths):
        for b in _BUCKETS:
            if n_tok <= b:
                groups[b].append(i)
                break
        else:
            groups[_BUCKETS[-1]].append(i)

    out: list[np.ndarray] = []
    start = time.monotonic()
    for bucket in _BUCKETS:
        if not groups[bucket]:
            continue
        n = batch_size_for(bucket, budget)
        # the production windowed sort, not a full sort — a full sort would measure a
        # cheaper ordering than the pipeline actually runs (ADR-0029)
        idxs = _length_sorted(groups[bucket], lengths, n)
        for s in range(0, len(idxs), n):
            chunk = idxs[s : s + n]
            vecs = model.encode(
                [docs[j] for j in chunk],
                normalize_embeddings=True,
                batch_size=len(chunk),
                show_progress_bar=False,
            )
            out.append(np.asarray(vecs, dtype="float32"))
            order.extend(chunk)
    elapsed = time.monotonic() - start
    stacked = np.vstack(out)
    restored = np.empty_like(stacked)
    restored[np.asarray(order)] = stacked
    return restored, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", type=int, default=150, help="synthetic Docs to encode")
    ap.add_argument(
        "--backends",
        default=",".join(_VARIANTS),
        help=f"comma-separated subset of: {', '.join(_VARIANTS)}",
    )
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import torch

    budget = 128_000_000 // 4  # the CPU attention budget embed_run uses
    print(
        f"threads={torch.get_num_threads()} torch={torch.__version__} "
        f"docs={args.docs} model={MODEL}",
        file=sys.stderr,
        flush=True,
    )

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    lengths = _sample_lengths(args.docs, args.seed)
    print(
        f"target lengths: p50={sorted(lengths)[len(lengths) // 2]} "
        f"total={sum(lengths):,} tokens",
        file=sys.stderr,
        flush=True,
    )
    docs = _make_docs(lengths, tokenizer)
    actual = [
        len(tokenizer(d, truncation=True, max_length=_MAX_SEQ_TOKENS)["input_ids"])
        for d in docs
    ]
    total_tokens = sum(actual)
    print(f"actual total: {total_tokens:,} tokens\n", file=sys.stderr, flush=True)

    baseline: np.ndarray | None = None
    rows: list[tuple[str, float, float, float, str]] = []
    for label in args.backends.split(","):
        label = label.strip()
        if label not in _VARIANTS:
            print(f"[skip] {label}: unknown variant", file=sys.stderr, flush=True)
            continue
        try:
            model = _load(label)
        except Exception as exc:  # noqa: BLE001 - a missing backend must not sink the run
            print(
                f"[skip] {label}: {type(exc).__name__}: {str(exc)[:120]}",
                file=sys.stderr,
                flush=True,
            )
            continue
        try:
            vecs, secs = _encode_bucketed(model, docs, actual, budget)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[fail] {label}: {type(exc).__name__}: {str(exc)[:120]}",
                file=sys.stderr,
                flush=True,
            )
            continue
        finally:
            del model
            gc.collect()

        if baseline is None:
            baseline, agree = vecs, "baseline"
        else:
            agree = f"{float((baseline * vecs).sum(axis=1).mean()):.5f}"
        tok_s, s_doc = total_tokens / secs, secs / len(docs)
        rows.append((label, secs, tok_s, s_doc, agree))
        print(
            f"[bench] {label:<16} {secs:8.1f}s  {tok_s:8.0f} tok/s  "
            f"{s_doc:6.3f} s/doc  agree={agree}",
            file=sys.stderr,
            flush=True,
        )

    if not rows:
        print("no backend ran", file=sys.stderr, flush=True)
        return 1

    if rows[0][0] != "torch-fp32":
        print(
            f"note: baseline is {rows[0][0]}, not torch-fp32 — speedups are relative to it",
            file=sys.stderr,
            flush=True,
        )
    base_s = rows[0][1]
    print(f"\n{'backend':<16}{'secs':>9}{'tok/s':>10}{'s/doc':>9}{'speedup':>9}  agree")
    for label, secs, tok_s, s_doc, agree in rows:
        print(
            f"{label:<16}{secs:>9.1f}{tok_s:>10.0f}{s_doc:>9.3f}{base_s / secs:>8.2f}x  {agree}"
        )
    print(
        "\nagree = mean cosine vs torch-fp32 on identical inputs (drift signal only — "
        "confirm on the retrieval eval harness before adopting a quantized backend)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
