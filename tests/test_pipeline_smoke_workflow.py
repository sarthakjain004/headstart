"""Tests for .github/workflows/pipeline-smoke.yml: it installs and caches as pipeline.yml does, so a
pin bumped there cannot leave the smoke run on the old one."""

from __future__ import annotations

import re
from pathlib import Path

from headstart import embedding_conventions as ec

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _uv_version(workflow: str) -> str:
    text = (_WORKFLOWS / workflow).read_text("utf-8")
    return re.search(r'^  UV_VERSION: "([^"]+)"$', text, re.MULTILINE).group(1)


def test_the_smoke_run_pins_the_pipelines_uv():
    assert _uv_version("pipeline-smoke.yml") == _uv_version("pipeline.yml")


def test_the_smoke_model_cache_key_names_the_pinned_revision():
    text = (_WORKFLOWS / "pipeline-smoke.yml").read_text("utf-8")
    assert f"key: hf-model-nomic-embed-text-v1.5-{ec.MODEL_REVISION[:12]}" in text
