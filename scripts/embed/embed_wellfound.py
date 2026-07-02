"""Embed Wellfound Jobs with nomic-embed-text-v1.5 (ADR-0005) — Steps 1-5 of the index.

Read ``data/jobs/wellfound.csv``, keep English rows (langdetect gate, per CLAUDE.md),
build each Job's document text (title + markdown-stripped description), prefix it with
``search_document:`` (ADR-0005), and encode on the Apple GPU (MPS) into 768-dim,
L2-normalized vectors. Structured fields ride alongside as metadata, never embedded (ADR-0006).

Output under ``data/embeddings/wellfound/``:
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
import csv
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch
from langdetect import DetectorFactory, LangDetectException, detect
from sentence_transformers import SentenceTransformer

from headstart.search import DOC_PREFIX, MODEL

DetectorFactory.seed = 0  # make langdetect deterministic
csv.field_size_limit(10**8)  # wellfound descriptions are large multi-line markdown

_ROOT = Path(__file__).resolve().parents[2]
_INPUT = _ROOT / "data" / "jobs" / "wellfound.csv"
_OUTDIR = _ROOT / "data" / "embeddings" / "wellfound"

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url) -> text
_MD_SYNTAX = re.compile(
    r"[*`#>]+"
)  # emphasis / heading / quote markers (keep `_`: tech terms)
_WS = re.compile(r"\s+")

_FLOAT_BYTES = 4  # float32


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


def build_doc(row: dict) -> str:
    title = (row.get("title") or "").strip()
    body = clean_markdown(row.get("description") or "")
    return f"{DOC_PREFIX}{title}\n\n{body}"


def to_meta(row: dict) -> dict:
    """Map a raw Wellfound CSV row to canonical, typed search metadata (ADR-0007).

    The structured fields ride next to the vector for the filter half — never embedded (ADR-0006).
    Wellfound's one-off scraper emits non-canonical column names; the real ATS scrapers already
    build ``Job`` records (``employment_type`` / ``experience`` / ``salary`` / bool ``remote``), so
    this adapter only exists until the Wellfound scraper is updated to do the same. Clean fields get
    real types here; the messy ``experience`` / ``salary`` stay raw strings — the extraction
    component normalizes those to numbers later (B1 stops at deterministic typing).
    """

    def text(key: str) -> str | None:
        return (row.get(key) or "").strip() or None

    remote = (row.get("remote") or "").strip().lower()
    return {
        "id": text("id"),
        "ats": text("ats"),
        "company": text("company"),
        "title": text("title"),
        "location": text("location"),
        "remote": {"true": True, "false": False}.get(
            remote
        ),  # canonical bool; None if blank/unknown
        "employment_type": text(
            "job_type"
        ),  # Wellfound job_type -> Job employment_type
        "experience": text(
            "years_experience"
        ),  # -> Job experience (raw "N+"; enriched to a number later)
        "salary": text(
            "compensation"
        ),  # -> Job salary (raw range, carries its symbol; parsed later)
        "department": text("department"),
        "url": text("url"),
        "posted_at": text("posted_at"),
    }


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
        "--limit",
        type=int,
        default=0,
        help="embed only the first N new English rows (0 = all)",
    )
    # Attention memory scales with batch x seq^2; with descriptions up to ~4,800 tokens a large
    # batch makes a multi-GB attention tensor per layer and thrashes. 8 keeps peak ~9 GB.
    ap.add_argument("--batch-size", type=int, default=8)
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
    dim = model.get_sentence_embedding_dimension()

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
    with _INPUT.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scanned += 1
            if (row.get("id") or "") in store.done:
                already += 1
                continue
            if not is_english(row.get("title") or "", row.get("description") or ""):
                dropped += 1
                continue
            docs.append(build_doc(row))
            metas.append(to_meta(row))
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
        "source": _INPUT.name,
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

    # Sort longest-first so each batch pads to a similar length (less wasted memory/compute) and
    # the heaviest batch runs first as a fail-fast canary. Order is irrelevant downstream — meta
    # carries the id and stays row-aligned with the vectors.
    order = sorted(range(len(docs)), key=lambda i: len(docs[i]), reverse=True)
    docs = [docs[i] for i in order]
    metas = [metas[i] for i in order]

    # Step 5: encode batch-by-batch, isolating per-batch failures (A3) and persisting per batch (A1).
    total = len(docs)
    done = failed = 0
    start = time.monotonic()
    for i in range(0, total, args.batch_size):
        batch_docs = docs[i : i + args.batch_size]
        batch_metas = metas[i : i + args.batch_size]
        try:
            vectors = model.encode(
                batch_docs,
                normalize_embeddings=True,
                batch_size=args.batch_size,
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001 - isolate the batch; its ids retry on the next --resume
            failed += len(batch_docs)
            bad = [m["id"] for m in batch_metas]
            print(
                f"[embed] batch FAILED ({type(exc).__name__}: {exc}) — skipped {len(bad)} "
                f"(e.g. {bad[:2]}); retry with --resume",
                file=sys.stderr,
                flush=True,
            )
            continue
        store.add(vectors, batch_metas)
        done += len(batch_docs)
        rate = done / (time.monotonic() - start)
        msg = f"[embed] {done}/{total} | {rate:0.0f} jobs/s"
        print(
            msg + (f" | {failed} failed" if failed else ""), file=sys.stderr, flush=True
        )

    count = store.close(manifest)
    print(
        f"done: embedded {done} this run ({failed} failed) — store now holds {count} vectors "
        f"of dim {dim} -> {_OUTDIR}",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
