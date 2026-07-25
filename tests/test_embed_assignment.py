"""Tests for headstart.ingest.embed_jobs's shard (``--assignment``) mode (ADR-0025 Phase 1).

Exercises the shard plumbing with a fake encoder — no model download, no real embedding: an
assignment of pre-built {doc, bucket, meta} records must produce a fresh fragment whose vectors are
row-aligned with the given metadata, grouped by the planner's bucket. Real encoding is CI-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("sentence_transformers")
pytest.importorskip("langdetect")
np = pytest.importorskip(
    "numpy"
)  # rides with the ML stack; skip cleanly when it's absent

# Imported after the gates above, not at the top: the module pulls the ML stack, which
# the quality CI job does not install — this must skip rather than error.
import headstart.ingest.embed_jobs as ej  # noqa: E402

_DIM = 8


class _FakeTokenizer:
    """Token count ≈ word count, so make_pin_doc's 'a a a…' string converges to the bucket length."""

    def __call__(self, text, truncation=True, max_length=4096):
        if isinstance(text, str):
            return {"input_ids": list(range(min(len(text.split()), max_length)))}
        return {
            "input_ids": [list(range(min(len(t.split()), max_length))) for t in text]
        }


class _FakeModel:
    """Encodes to deterministic unit rows — enough to verify plumbing and row alignment."""

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self.tokenizer = _FakeTokenizer()

    def encode(
        self, texts, normalize_embeddings=True, batch_size=None, show_progress_bar=False
    ):
        return np.ones((len(texts), self._dim), dtype="float32")

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


def _write_assignment(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def _rec(job_id: str, bucket: int) -> dict:
    return {
        "doc": f"search_document: role {job_id}",
        "bucket": bucket,
        "meta": {"id": job_id},
    }


def test_assignment_writes_row_aligned_fragment(tmp_path):
    path = tmp_path / "shard-0.jsonl"
    recs = [_rec("a:1", 512), _rec("a:2", 1024), _rec("a:3", 512), _rec("a:4", 2048)]
    _write_assignment(path, recs)
    outdir = tmp_path / "frag"

    ej._run_assignment(
        _FakeModel(_DIM), "cpu", _DIM, ej._ATTN_BUDGET // 4, path, outdir
    )

    metas = [
        json.loads(line) for line in (outdir / "meta.jsonl").read_text().splitlines()
    ]
    vecs = np.fromfile(outdir / "embeddings.f32", dtype="float32")
    assert len(metas) == 4
    assert vecs.size == 4 * _DIM
    assert {m["id"] for m in metas} == {
        "a:1",
        "a:2",
        "a:3",
        "a:4",
    }  # every assigned Doc embedded
    assert json.loads((outdir / "manifest.json").read_text())["count"] == 4


def test_empty_assignment_yields_empty_fragment(tmp_path):
    path = tmp_path / "shard-0.jsonl"
    _write_assignment(path, [])
    outdir = tmp_path / "frag"

    ej._run_assignment(
        _FakeModel(_DIM), "cpu", _DIM, ej._ATTN_BUDGET // 4, path, outdir
    )

    assert (outdir / "meta.jsonl").read_text() == ""
    assert (outdir / "embeddings.f32").stat().st_size == 0
    assert json.loads((outdir / "manifest.json").read_text())["count"] == 0
