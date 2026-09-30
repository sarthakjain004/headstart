"""Tests for .github/workflows/restate-trends.yml: it installs and caches as pipeline.yml does, so a
pin bumped there cannot leave a Restatement on the old one."""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"


def _uv_version(workflow: str) -> str:
    text = (_WORKFLOWS / workflow).read_text("utf-8")
    return re.search(r'^  UV_VERSION: "([^"]+)"$', text, re.MULTILINE).group(1)


def test_a_restatement_pins_the_pipelines_uv():
    assert _uv_version("restate-trends.yml") == _uv_version("pipeline.yml")


def test_the_title_model_cache_key_names_the_heads_model_revision():
    manifest = json.loads(
        (_REPO / "config" / "role_family_classifier" / "manifest.json").read_text(
            "utf-8"
        )
    )
    text = (_WORKFLOWS / "restate-trends.yml").read_text("utf-8")
    assert f"key: hf-model-jobbert-v2-{manifest['model_revision'][:12]}" in text
