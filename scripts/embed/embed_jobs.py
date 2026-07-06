"""Embed a job corpus with nomic-embed-text-v1.5 (ADR-0005) — the production embed step (ADR-0019).

Read canonical Job dicts via ``corpus.iter_jobs`` (default source: ``data/jobs/tech/``, the
authoritative tech corpus per ADR-0017), keep English rows (langdetect gate, per CLAUDE.md),
build each Job's document text (title + markdown-stripped description), prefix it with
``search_document:`` (ADR-0005), and encode on the Apple GPU (MPS) into 768-dim,
L2-normalized vectors. Structured fields ride alongside as metadata, never embedded (ADR-0006);
the required-experience numbers (``min_years`` / ``max_years`` / ``experience_source``) are
computed inline into the metadata via ``experience.extract`` (ADR-0019 — no separate enrich join).

Output under ``data/embeddings/jobs/``:
- ``embeddings.f32`` — raw float32 vectors, row-major, appended as each batch finishes.
  Load with ``np.fromfile("embeddings.f32", dtype="float32").reshape(-1, dim)`` (``dim`` in manifest).
- ``meta.jsonl`` — one metadata record per vector, row-aligned with the vectors; the authority for resume.
- ``manifest.json`` — provenance, written last as the "this run finished" marker.

Crash-safe and resumable, mirroring the ``JobWriter`` pattern in ``src/headstart/pipeline.py``:
vectors and metadata stream to disk in lockstep (A1), a failed batch is isolated and retried on the
next run (A3), and ``--resume`` skips Jobs already embedded so you only encode the delta (A2).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch
from langdetect import DetectorFactory, LangDetectException, detect
from sentence_transformers import SentenceTransformer

from headstart.corpus import iter_jobs
from headstart.experience import extract
from headstart.search import DOC_PREFIX, MODEL

DetectorFactory.seed = 0  # make langdetect deterministic

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _ROOT / "data" / "jobs" / "tech"
_OUTDIR = _ROOT / "data" / "embeddings" / "jobs"

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text
_MD_SYNTAX = re.compile(
    r"[*`#>]+"
)  # emphasis / heading / quote markers (keep `_`: tech terms)
_WS = re.compile(r"\s+")

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
# Sequences are hard-capped at 4,096 tokens (the top bucket): a single full-context 8,192-token
# doc transiently demands ~50 GB on this stack. 4,096 is inside the envelope the Wellfound run
# proved safe, and only ~0.01% of tech-corpus docs are longer (their boilerplate tails get
# truncated). This consciously narrows ADR-0005's "no truncation" to "up to 4k tokens".
_ATTN_BUDGET = 128_000_000  # tokens²; ~2/3 of the observed 8 × 4800² ≈ 9 GB anchor
_BUCKETS = (512, 1024, 2048, 4096)
_MAX_SEQ_TOKENS = _BUCKETS[-1]
_BATCH_CAP = 32


def bucket_for(n_tokens: int) -> int:
    """The smallest bucket that holds a doc of ``n_tokens`` (over-cap docs go to the top one)."""
    for bucket in _BUCKETS:
        if n_tokens <= bucket:
            return bucket
    return _BUCKETS[-1]


def batch_size_for(bucket: int, budget: int = _ATTN_BUDGET) -> int:
    """Fixed docs-per-batch for a bucket, so every batch in it presents one identical shape."""
    return max(1, min(_BATCH_CAP, budget // (bucket * bucket)))


def make_pin_doc(tokenizer, bucket: int) -> str:
    """A doc of exactly ``bucket`` tokens — riding in every batch, it makes the tokenizer pad
    the whole batch to the bucket length, pinning the batch shape."""

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


# The canonical typed metadata that rides next to each vector (ADR-0007); the corpus reader
# already yields canonical Job dicts, so this is pure selection — no per-source adapting.
_META_FIELDS = (
    "id",
    "ats",
    "company",
    "title",
    "location",
    "remote",
    "employment_type",
    "experience",
    "salary",
    "department",
    "url",
    "posted_at",
)


def clean_markdown(text: str) -> str:
    """Strip markdown syntax to plain text and collapse whitespace."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_SYNTAX.sub(" ", text)
    return _WS.sub(" ", text).strip()


def is_english(title: str, description: str) -> bool:
    """English gate. Detect on title + a description sample (full text is needless and slow)."""
    try:
        return detect(f"{title} {description[:500]}") == "en"
    except LangDetectException:
        return False  # undetectable -> held out of the English index


def build_doc(job: dict) -> str:
    title = (job.get("title") or "").strip()
    body = clean_markdown(job.get("description") or "")
    return f"{DOC_PREFIX}{title}\n\n{body}"


def to_meta(job: dict) -> dict:
    """Canonical typed metadata (ADR-0007) + the inline experience numbers (ADR-0019).

    ``min_years`` / ``max_years`` come from the extraction cascade (field, then description,
    then seniority floor — ADR-0018) with the ``experience_source`` tier tag carried alongside;
    all three are None when nothing matched. ``employment_type`` / ``salary`` stay raw strings —
    display-only until normalized (ADR-0019).
    """
    meta = {field: job.get(field) for field in _META_FIELDS}
    span = extract(job.get("experience"), job.get("description"), job.get("title"))
    meta["min_years"] = span.min_years if span else None
    meta["max_years"] = span.max_years if span else None
    meta["experience_source"] = span.source if span else None
    return meta


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


def main() -> None:
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
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"loading {MODEL} on {device} ...", file=sys.stderr, flush=True)
    model = SentenceTransformer(MODEL, trust_remote_code=True, device=device)
    if device == "mps":
        model = model.half()  # fp16 on the GPU: ~2x faster + half the memory; vectors upcast to f32 on store
    model.max_seq_length = min(
        model.max_seq_length, _MAX_SEQ_TOKENS
    )  # see _MAX_SEQ_TOKENS
    dim = model.get_sentence_embedding_dimension()
    # CPU runs fp32 (double the attention memory of MPS fp16) on small CI runners — shrink the
    # batch budget; CPU throughput is compute-bound, so the smaller batches cost little.
    budget = _ATTN_BUDGET if device == "mps" else _ATTN_BUDGET // 4

    store = EmbeddingStore(_OUTDIR, dim, resume=args.resume)
    if store.done:
        print(
            f"resume: {len(store.done)} Jobs already embedded — will skip those",
            file=sys.stderr,
            flush=True,
        )

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
    print(
        f"to embed: {len(docs)} (scanned {scanned}, already-done {already}, non-English {dropped})",
        file=sys.stderr,
        flush=True,
    )

    manifest = {
        "model": MODEL,
        "dim": int(dim),
        "doc_prefix": DOC_PREFIX,
        "normalized": True,
        "device": device,
        "compute_dtype": "float16" if device == "mps" else "float32",
        "source": str(args.source),
        "vectors_file": "embeddings.f32",
        "dtype": "float32",
    }
    if not docs:
        count = store.close(manifest)
        print(
            f"nothing new to embed — store holds {count} vectors.",
            file=sys.stderr,
            flush=True,
        )
        return

    # Group docs into token-length buckets, measured with the real tokenizer — a char-based
    # estimate undershoots on tokenizer-dense docs (a bilingual description whose English head
    # passes the language gate but whose CJK tail tokenizes at ~1 token/char).
    print("measuring token lengths ...", file=sys.stderr, flush=True)
    tok_lens: list[int] = []
    for s in range(0, len(docs), 1024):
        enc = model.tokenizer(
            docs[s : s + 1024], truncation=True, max_length=_MAX_SEQ_TOKENS
        )
        tok_lens.extend(len(ids) for ids in enc["input_ids"])
        print(f"[tokenize] {len(tok_lens)}/{len(docs)}", file=sys.stderr, flush=True)
    groups: dict[int, list[int]] = {b: [] for b in _BUCKETS}
    for idx, n_tok in enumerate(tok_lens):
        groups[bucket_for(n_tok)].append(idx)

    # Step 5: encode bucket-by-bucket with pinned shapes (see the _BUCKETS comment), isolating
    # per-batch failures (A3) and persisting per batch (A1). Smallest bucket first: under the
    # CI time budget (pipeline.yml wraps this in `timeout`), short docs embed at docs/sec while
    # 4096-token docs cost minutes each on CPU — ascending order banks the most docs before the
    # budget expires (heaviest-first once burned a 98-min budget on ~325 docs). Order is
    # irrelevant downstream — meta carries the id and stays row-aligned with the vectors.
    total = len(docs)
    done = failed = consec_failed = 0
    start = time.monotonic()
    wedged = False
    for bucket in _BUCKETS:
        idxs = groups[bucket]
        if not idxs or wedged:
            continue
        n = batch_size_for(bucket, budget)
        pin = make_pin_doc(model.tokenizer, bucket)
        print(
            f"[embed] bucket ≤{bucket} tokens: {len(idxs)} docs in batches of {n}",
            file=sys.stderr,
            flush=True,
        )
        for s in range(0, len(idxs), n):
            chunk = idxs[s : s + n]
            batch_metas = [metas[j] for j in chunk]
            batch_docs = [docs[j] for j in chunk]
            # pad the count with repeats of the first doc, and add the pin doc so the batch
            # pads to the bucket length — every batch in the bucket is one identical shape
            padded = batch_docs + [batch_docs[0]] * (n - len(chunk)) + [pin]
            for attempt in (0, 1):
                try:
                    vectors = model.encode(
                        padded,
                        normalize_embeddings=True,
                        batch_size=len(padded),
                        show_progress_bar=False,
                    )[: len(chunk)]  # drop the count-padding and the pin
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
                    print(
                        f"[embed] batch FAILED ({type(exc).__name__}: {exc}) — skipped "
                        f"{len(bad)} (e.g. {bad[:2]}); retry with --resume",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    store.add(vectors, batch_metas)
                    done += len(chunk)
                    consec_failed = 0
                break
            rate = done / (time.monotonic() - start)
            msg = f"[embed] {done}/{total} | {rate:0.0f} jobs/s"
            print(
                msg + (f" | {failed} failed" if failed else ""),
                file=sys.stderr,
                flush=True,
            )
            # A wedged accelerator fails every allocation no matter how small — stop instead
            # of marching through the queue marking everything failed; --resume resumes here.
            if consec_failed >= 64:
                print(
                    f"[embed] {consec_failed} consecutive failures — allocator looks wedged; "
                    "stopping (re-run with --resume)",
                    file=sys.stderr,
                    flush=True,
                )
                wedged = True
                break

    count = store.close(manifest)
    print(
        f"done: embedded {done} this run ({failed} failed) — store now holds {count} vectors "
        f"of dim {dim} -> {_OUTDIR}",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
