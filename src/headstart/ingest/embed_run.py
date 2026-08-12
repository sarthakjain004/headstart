"""Embed a job corpus with nomic-embed-text-v1.5 (ADR-0005) — the production embed step (ADR-0019).

Read canonical Job dicts via ``corpus.iter_jobs`` (default source: ``data/jobs/tech/``, the
authoritative tech corpus per ADR-0017), keep English rows (langdetect gate, per CLAUDE.md),
build each Job's document text (title + markdown-stripped description), prefix it with
``search_document:`` (ADR-0005), and encode on the Apple GPU (MPS) into 768-dim,
L2-normalized vectors. Structured fields ride alongside as metadata, never embedded (ADR-0006);
the required-experience numbers (``min_years`` / ``max_years`` / ``experience_source``) are
computed inline into the metadata via ``experience.extract`` (ADR-0019 — no separate enrich join).

Two run modes:
- Default / ``--resume`` — self-select the delta from the corpus (skip ids already in
  ``meta.jsonl``), English-gate, tokenize, bucket, and embed into ``data/embeddings/jobs/``.
- ``--assignment <file>`` — embed a planner-built shard (a JSONL of ``{doc, bucket, tokens, meta}``)
  into a fresh ``--outdir`` fragment (ADR-0025). The planner already did the dedup, English gate,
  doc build, metadata, and tokenization, so a shard is stateless: no corpus, no prior store. The
  doc-prep those two modes must agree on lives in ``headstart.ingest.doc_prep`` (re-exported below).

Output under ``data/embeddings/jobs/`` (or ``--outdir``):
- ``embeddings.f32`` — raw float32 vectors, row-major, appended as each batch finishes.
  Load with ``np.fromfile("embeddings.f32", dtype="float32").reshape(-1, dim)`` (``dim`` in manifest).
- ``meta.jsonl`` — one metadata record per vector, row-aligned with the vectors; the authority for resume.
- ``manifest.json`` — provenance, written last as the "this run finished" marker.

Crash-safe and resumable, mirroring the ``JobWriter`` pattern in :mod:`headstart.harvest`:
vectors and metadata stream to disk in lockstep (A1), a failed batch is isolated and retried on the
next run (A3), and ``--resume`` skips Jobs already embedded so you only encode the delta (A2).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from headstart import log
from headstart.board_priority import load_scores
from headstart.corpus import board_of, iter_jobs
from headstart.ingest import REPO_ROOT, run_report
from headstart.ingest.doc_prep import (  # re-exported: doc-prep shared with the embed planner (ADR-0025)
    _BUCKETS,
    _MAX_SEQ_TOKENS,
    bucket_for,
    build_doc,
    is_english,
    to_meta,
)
from headstart.search import DOC_PREFIX, MODEL

_log = log.get(__name__, __spec__)

_SOURCE = REPO_ROOT / "data" / "jobs" / "tech"
_OUTDIR = REPO_ROOT / "data" / "embeddings" / "jobs"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"

_FLOAT_BYTES = 4  # float32

# Batching is shaped by two empirical MPS findings (controlled experiments, 2026-07-04):
#
# 1. Attention memory scales with batch × seq² — a fixed batch of 8 (calibrated on Wellfound's
#    ~4,800-token max, ~9 GB peak) pushed a 64 GB machine deep into swap on this corpus's
#    longer docs. n × seq² ≤ _ATTN_BUDGET holds the transient peak roughly constant.
# 2. The Metal driver caches compiled-graph workspace per *unique (batch, seq) shape* and never
#    frees it: repeating one shape holds driver memory flat, while every new shape adds ~2-3 GB
#    for 4k-token batches until allocations are refused and the process wedges. Since padding to
#    the batch's longest doc makes nearly every batch a fresh shape, the whole run must use a
#    finite shape set: docs are grouped into token-length _BUCKETS (measured with the real
#    tokenizer, not estimated — bilingual tails tokenize at ~1 token/char), each bucket gets a
#    fixed batch size from the budget, every batch is padded to exactly that count with repeats
#    of its first doc, and one pin doc of exactly the bucket's token length rides along so the
#    tokenizer pads every batch to the bucket length. Shapes per run: len(_BUCKETS).
#
# Finding 2 is a property of the *Metal driver*, so the shape pinning it motivates is applied
# only on MPS. A CPU run — the CI pipeline, whose shards land on GPU-less GitHub VMs
# (ADR-0025/ADR-0026) — has no such cache, so there the pin doc and the count-padding buy nothing
# and cost full extra forward passes. Worst exactly where it hurts most: CPU quarters the budget,
# so batch_size_for(4096) == 1 and each 4,096-token Doc was encoded alongside a 4,096-token pin —
# 2x the work in the bucket that owns ~38% of a shard's wall time. Measured on run 30131376268
# (2026-07-24, 646 Docs/shard): pinning cost 728s of 2,669s, ~27%. On CPU a batch is therefore
# the real Docs alone, padded only to the longest Doc in it.
#
# Sequences are hard-capped at 4,096 tokens (the top bucket): a single full-context 8,192-token
# doc transiently demands ~50 GB on this stack. 4,096 is inside the envelope the Wellfound run
# proved safe, and only ~0.01% of tech-corpus docs are longer (their boilerplate tails get
# truncated). This consciously narrows ADR-0005's "no truncation" to "up to 4k tokens".
# _BUCKETS / _MAX_SEQ_TOKENS moved to headstart.ingest.doc_prep (shared with the planner); the
# batch-sizing budget below is encode-side and stays here.
_ATTN_BUDGET = 128_000_000  # tokens²; ~2/3 of the observed 8 × 4800² ≈ 9 GB anchor
_BATCH_CAP = 32

# Batches to length-sort together before batching (ADR-0029). Trades priority slack for padding;
# ADR-0029 records the measured overhead at each window size and why 8.
_SORT_WINDOW = 8


def batch_size_for(bucket: int, budget: int = _ATTN_BUDGET) -> int:
    """Fixed docs-per-batch for a bucket, so every batch in it presents one identical shape."""
    return max(1, min(_BATCH_CAP, budget // (bucket * bucket)))


def order_by_priority(idxs: list[int], metas: list[dict], scores: dict) -> list[int]:
    """A bucket's doc indices reordered board-score-desc (ADR-0022): under the CI time
    budget, the highest-value boards' docs bank first. Stable, so corpus order breaks
    ties; boards without a score sink to the tail."""
    return sorted(
        idxs, key=lambda i: scores.get(board_of(metas[i]["id"]), 0.0), reverse=True
    )


def encode_batch(batch_docs: list[str], n: int, pin: str | None) -> list[str]:
    """The doc list handed to ``model.encode`` for one batch.

    ``pin`` set (MPS): pin the shape to ``(n + 1, bucket)`` — repeats of the first doc fill a
    short final chunk, and the pin doc makes the tokenizer pad every sequence to the bucket
    length. ``pin`` None (CPU): the real docs alone, since only the Metal driver needs a finite
    shape set and the padding is otherwise just extra forward passes (see the _BUCKETS comment).
    Either way the caller keeps the first ``len(batch_docs)`` vectors."""
    if pin is None:
        return batch_docs
    return batch_docs + [batch_docs[0]] * (n - len(batch_docs)) + [pin]


def make_pin_doc(tokenizer, bucket: int) -> str:
    """A doc of exactly ``bucket`` tokens — riding in every batch, it makes the tokenizer pad
    the whole batch to the bucket length, pinning the batch shape. MPS only."""

    def measure(text: str) -> int:
        return len(
            tokenizer(text, truncation=True, max_length=_MAX_SEQ_TOKENS)["input_ids"]
        )

    doc = "a " * bucket  # "a" is one token; specials add a couple more
    n = measure(doc)
    while n > bucket:
        doc = doc[: -2 * (n - bucket)]  # drop one "a " per excess token
        n = measure(doc)
    while n < bucket:
        doc += "a " * (bucket - n)
        n = measure(doc)
    return doc


class EmbeddingStore:
    """Crash-safe, resumable vector store — mirrors ``JobWriter`` (ADR-0004).

    Each batch's vectors append to ``embeddings.f32`` (raw float32, ``dim`` floats per row) and
    are flushed, then its metadata appends to ``meta.jsonl`` and is flushed. Vectors are written
    *before* their metadata, so after any crash the vector file is at least as long as the
    metadata; ``meta.jsonl`` is the authority, and on resume the vector file is truncated back to
    match it — so an interruption costs only the in-flight batch. ``manifest.json`` is written
    last by :meth:`close`, as the run-finished marker.
    """

    def __init__(self, outdir: Path, dim: int, *, resume: bool) -> None:
        self._dir = outdir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dim = dim
        self._vec_path = self._dir / "embeddings.f32"
        self._meta_path = self._dir / "meta.jsonl"
        self.done: set[str] = set()
        if resume and self._meta_path.exists():
            self.done = self._reconcile()
            self._vf = self._vec_path.open("ab")
            self._mf = self._meta_path.open("a", encoding="utf-8")
        else:
            self._vf = self._vec_path.open("wb")  # fresh run truncates both files
            self._mf = self._meta_path.open("w", encoding="utf-8")

    def _reconcile(self) -> set[str]:
        """Align the store after a possible crash: drop any partial metadata tail, then truncate
        the vector file to one row per surviving metadata line. Returns the set of done ids."""
        ids: list[str] = []
        good: list[str] = []
        with self._meta_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    break  # a half-written final line from a crash — stop; treat the rest as gone
                ids.append(record["id"])
                good.append(line)
        # Rewrite metadata with only fully-parsed lines, then size the vector file to match.
        self._meta_path.write_text("".join(s + "\n" for s in good), encoding="utf-8")
        want_bytes = len(good) * self._dim * _FLOAT_BYTES
        if self._vec_path.exists():
            with self._vec_path.open("r+b") as vf:
                vf.truncate(want_bytes)
        else:
            self._vec_path.write_bytes(b"")
        return set(ids)

    def add(self, vectors: np.ndarray, metas: list[dict]) -> None:
        """Append one batch: vectors first (flushed), then their aligned metadata (flushed)."""
        self._vf.write(np.ascontiguousarray(vectors, dtype=np.float32).tobytes())
        self._vf.flush()
        for meta in metas:
            self._mf.write(json.dumps(meta, ensure_ascii=False) + "\n")
        self._mf.flush()

    def close(self, manifest: dict) -> int:
        """Close handles and write manifest.json last (the commit marker). Returns total count."""
        self._vf.close()
        self._mf.close()
        count = sum(1 for _ in self._meta_path.open(encoding="utf-8"))
        (self._dir / "manifest.json").write_text(
            json.dumps({**manifest, "count": count}, indent=2), encoding="utf-8"
        )
        return count


def _load_model() -> tuple[SentenceTransformer, str, int, int]:
    """Load the encoder (MPS/fp16 when available, else CPU/fp32) and cap its sequence length.

    Returns ``(model, device, dim, attention_budget)``; CPU quarters the budget (fp32 doubles
    the attention memory of the MPS/fp16 path, and CI runners are small)."""
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    _log.info(f"loading {MODEL} on {device} ...")
    model = SentenceTransformer(MODEL, trust_remote_code=True, device=device)
    if device == "mps":
        model = model.half()  # fp16 on the GPU: ~2x faster + half the memory; vectors upcast to f32 on store
    model.max_seq_length = min(
        model.max_seq_length, _MAX_SEQ_TOKENS
    )  # see _MAX_SEQ_TOKENS
    dim = model.get_sentence_embedding_dimension()
    budget = _ATTN_BUDGET if device == "mps" else _ATTN_BUDGET // 4
    return model, device, dim, budget


def _manifest(device: str, source: str, dim: int) -> dict:
    """Provenance for the store (``store.close`` appends the final ``count``)."""
    return {
        "model": MODEL,
        "dim": int(dim),
        "doc_prefix": DOC_PREFIX,
        "normalized": True,
        "device": device,
        "compute_dtype": "float16" if device == "mps" else "float32",
        "source": str(source),
        "vectors_file": "embeddings.f32",
        "dtype": "float32",
    }


def _length_sorted(idxs: list[int], tokens: list[int], batch: int) -> list[int]:
    """Reorder a bucket's indices so each batch holds Docs of similar length (ADR-0029).

    A batch is padded to its longest member, so mixing a 1,030-token Doc with a 2,040-token one
    in the same batch pays for the longer twice. The indices arrive in board-priority order
    (ADR-0022), which is uncorrelated with length, so that waste is the common case.

    Fully sorting by length would erase the priority order that lets a time-boxed shard bank its
    highest-value Docs first, so the sort is confined to *windows* of ``batch × _SORT_WINDOW``:
    priority survives to within one window while nearly all the padding is recovered. ADR-0029
    has the measured overhead at each window size and why 8 was chosen.
    """
    window = batch * _SORT_WINDOW
    out: list[int] = []
    for s in range(0, len(idxs), window):
        out.extend(sorted(idxs[s : s + window], key=tokens.__getitem__))
    return out


def _encode_groups(
    model,
    device: str,
    docs: list[str],
    metas: list[dict],
    groups: dict,
    store,
    budget: int,
    tokens: list[int],
) -> tuple[int, int]:
    """Encode bucket-by-bucket, shapes pinned on MPS only (see the _BUCKETS comment), isolating
    per-batch failures (A3) and persisting per batch (A1). Smallest bucket first: under the CI
    time budget (pipeline.yml wraps this in ``timeout``), short docs embed at docs/sec while
    4096-token docs cost minutes each on CPU — ascending order banks the most docs before the
    budget expires (heaviest-first once burned a 98-min budget on ~325 docs). Within each bucket
    the indices arrive in board-priority order (ADR-0022) and are then length-sorted within
    windows to cut padding waste (:func:`_length_sorted`, ADR-0029); ordering stays irrelevant
    downstream — meta carries the id and stays row-aligned with the vectors. Returns
    ``(embedded, failed)``."""
    total = sum(len(idxs) for idxs in groups.values())
    done = failed = consec_failed = 0
    start = time.monotonic()
    wedged = False
    pin_shapes = device == "mps"  # only the Metal driver needs a finite shape set
    for bucket in _BUCKETS:
        idxs = groups[bucket]
        if not idxs or wedged:
            continue
        n = batch_size_for(bucket, budget)
        idxs = _length_sorted(idxs, tokens, n)
        pin = make_pin_doc(model.tokenizer, bucket) if pin_shapes else None
        _log.info(f"bucket ≤{bucket} tokens: {len(idxs)} docs in batches of {n}")
        for s in range(0, len(idxs), n):
            chunk = idxs[s : s + n]
            batch_metas = [metas[j] for j in chunk]
            batch_docs = [docs[j] for j in chunk]
            batch = encode_batch(batch_docs, n, pin)
            for attempt in (0, 1):
                try:
                    vectors = model.encode(
                        batch,
                        normalize_embeddings=True,
                        batch_size=len(batch),
                        show_progress_bar=False,
                    )[: len(chunk)]  # drop the count-padding and the pin (no-op on CPU)
                except Exception as exc:  # noqa: BLE001 - isolate the batch; its ids retry on the next --resume
                    if (
                        attempt == 0
                        and device == "mps"
                        and "out of memory" in str(exc).lower()
                    ):
                        torch.mps.synchronize()  # drain queued work so its buffers release
                        torch.mps.empty_cache()
                        continue  # retry once — same shape, so no new driver-cache entry
                    failed += len(chunk)
                    consec_failed += len(chunk)
                    bad = [m["id"] for m in batch_metas]
                    _log.warning(
                        f"batch FAILED ({type(exc).__name__}: {exc}) — skipped "
                        f"{len(bad)} (e.g. {bad[:2]}); retry with --resume"
                    )
                else:
                    store.add(vectors, batch_metas)
                    done += len(chunk)
                    consec_failed = 0
                break
            rate = done / (time.monotonic() - start)
            msg = f"{done}/{total} | {rate:0.0f} jobs/s"
            _log.info(msg + (f" | {failed} failed" if failed else ""))
            # A wedged accelerator fails every allocation no matter how small — stop instead
            # of marching through the queue marking everything failed; --resume resumes here.
            if consec_failed >= 64:
                _log.warning(
                    f"{consec_failed} consecutive failures — allocator looks wedged; "
                    "stopping (re-run with --resume)"
                )
                wedged = True
                break
    return done, failed


def _run_assignment(
    model, device: str, dim: int, budget: int, path: Path, outdir: Path
) -> None:
    """Embed a planner-built shard assignment (ADR-0025): a JSONL of ``{doc, bucket, tokens, meta}``.

    The planner already did the dedup, English gate, doc build, metadata, and tokenization, so a
    shard is stateless — it reads neither the corpus nor the prior store, and writes a fresh
    fragment the merge job concatenates. Grouping preserves the file's order within each bucket
    (the planner ordered it priority-first, ADR-0022); ``_encode_groups`` then length-sorts within
    windows of that order so a time-boxed shard still banks the highest-value Docs first while
    paying far less padding (ADR-0029).

    ``tokens`` is the planner's exact count for the Doc — reused rather than re-derived, since a
    character-length proxy misorders token-dense text (the same reason the planner tokenizes for
    real). It falls back to the Bucket cap, which is a safe upper bound on the Doc's length."""
    docs: list[str] = []
    metas: list[dict] = []
    tokens: list[int] = []
    groups: dict[int, list[int]] = {b: [] for b in _BUCKETS}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            bucket = int(rec["bucket"])
            if (
                bucket not in groups
            ):  # a stray/out-of-range bucket -> top one, never dropped
                bucket = _BUCKETS[-1]
            groups[bucket].append(len(docs))
            tokens.append(int(rec.get("tokens") or bucket))
            docs.append(rec["doc"])
            metas.append(rec["meta"])
    _log.info(
        f"assignment: {len(docs)} docs from {path} | "
        + ", ".join(f"≤{b}:{len(groups[b])}" for b in _BUCKETS)
    )

    store = EmbeddingStore(
        outdir, dim, resume=False
    )  # a shard is always a fresh fragment
    done = failed = 0
    if docs:
        done, failed = _encode_groups(
            model, device, docs, metas, groups, store, budget, tokens
        )
    count = store.close(_manifest(device, str(path), dim))
    _log.info(
        f"done: shard embedded {done} ({failed} failed) -> {outdir} ({count} vectors)"
    )

def main() -> None:
    log.setup()
    run_report.context("embed")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        default=str(_SOURCE),
        help="corpus source: a {ats}.jsonl directory or a Wellfound CSV (default: data/jobs/tech)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="embed only the first N new English rows (0 = all)",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="skip Jobs already in meta.jsonl and append new ones (default: rebuild from scratch)",
    )
    ap.add_argument(
        "--assignment",
        help="embed a planner-built shard (JSONL of {doc,bucket,meta}); skips corpus read, "
        "English gate, and tokenize — the planner did them (ADR-0025)",
    )
    ap.add_argument(
        "--outdir",
        default=str(_OUTDIR),
        help="store output dir (default: data/embeddings/jobs; an embed shard writes its own fragment)",
    )
    args = ap.parse_args()

    model, device, dim, budget = _load_model()
    outdir = Path(args.outdir)

    if (
        args.assignment
    ):  # ADR-0025 shard mode — the planner already selected/prepped these docs
        _run_assignment(model, device, dim, budget, Path(args.assignment), outdir)
        return

    store = EmbeddingStore(outdir, dim, resume=args.resume)
    if store.done:
        _log.info(f"resume: {len(store.done)} Jobs already embedded — will skip those")

    # Steps 1-4: select rows, skip already-done ids (A2), English-gate, build doc text + metadata.
    docs: list[str] = []
    metas: list[dict] = []
    scanned = already = dropped = 0
    for job in iter_jobs(args.source):
        scanned += 1
        if (job.get("id") or "") in store.done:
            already += 1
            continue
        if not is_english(job.get("title") or "", job.get("description") or ""):
            dropped += 1
            continue
        docs.append(build_doc(job))
        metas.append(to_meta(job))
        if args.limit and len(docs) >= args.limit:
            break
    _log.info(
        f"to embed: {len(docs)} (scanned {scanned}, already-done {already}, non-English {dropped})"
    )

    manifest = _manifest(device, args.source, dim)
    if not docs:
        count = store.close(manifest)
        _log.info(f"nothing new to embed — store holds {count} vectors.")
        return

    # Group docs into token-length buckets, measured with the real tokenizer — a char-based
    # estimate undershoots on tokenizer-dense docs (a bilingual description whose English head
    # passes the language gate but whose CJK tail tokenizes at ~1 token/char).
    _log.info("measuring token lengths ...")
    tok_lens: list[int] = []
    for s in range(0, len(docs), 1024):
        enc = model.tokenizer(
            docs[s : s + 1024], truncation=True, max_length=_MAX_SEQ_TOKENS
        )
        tok_lens.extend(len(ids) for ids in enc["input_ids"])
        _log.info(f"tokenized {len(tok_lens)}/{len(docs)}")
    groups: dict[int, list[int]] = {b: [] for b in _BUCKETS}
    for idx, n_tok in enumerate(tok_lens):
        groups[bucket_for(n_tok)].append(idx)
    scores = load_scores(_PRIORITY)
    if scores:
        for b in _BUCKETS:
            groups[b] = order_by_priority(groups[b], metas, scores)
        _log.info("priority ordering applied within buckets")

    done, failed = _encode_groups(
        model, device, docs, metas, groups, store, budget, tok_lens
    )

    count = store.close(manifest)
    _log.info(
        f"done: embedded {done} this run ({failed} failed) — store now holds {count} vectors "
        f"of dim {dim} -> {outdir}"
    )
    run_report.summary(
        "Embed shard",
        [
            f"- embedded **{done:,}** this run ({failed} failed)",
            f"- store holds **{count:,}** vectors of dim {dim}",
        ],
    )


if __name__ == "__main__":
    main()
