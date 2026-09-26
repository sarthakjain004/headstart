"""Tests for headstart.embedding_conventions: the nomic load is pinned, and the pipeline's model
cache is keyed on the pin."""

from __future__ import annotations

import sys
import types
from pathlib import Path

from headstart import embedding_conventions as ec


def test_the_model_opens_at_its_pinned_revisions(monkeypatch):
    seen = {}

    def fake(model, **kwargs):
        seen.update(kwargs, model=model)
        return "model"

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=fake),
    )

    assert ec.open_model("cpu") == "model"
    assert seen["model"] == ec.MODEL
    assert seen["revision"] == ec.MODEL_REVISION
    assert seen["model_kwargs"] == {"code_revision": ec.MODEL_CODE_REVISION}
    assert seen["config_kwargs"] == {"code_revision": ec.MODEL_CODE_REVISION}


def test_the_embed_jobs_model_cache_key_names_the_pinned_revision():
    """A new pin must change the cache key: otherwise every hit restores the old commit, and the
    embed step, offline on a hit, cannot fetch the new one."""
    repo = Path(__file__).resolve().parent.parent
    workflow = (repo / ".github" / "workflows" / "pipeline.yml").read_text("utf-8")
    assert f"key: hf-model-nomic-embed-text-v1.5-{ec.MODEL_REVISION[:12]}" in workflow
