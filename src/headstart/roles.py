"""Role-trend taxonomy seam (ADR-0040): frozen family centroids × experience bands.

The contract two very different callers must agree on, held once — mirroring
``ingest.doc_prep``: ``scripts/embed/cluster_roles.py`` (the one-off fit) writes the centroid
store through :func:`save`, and the pipeline's per-run trends step reads it back with
:func:`load` and buckets rows via :func:`assign` + :func:`band`. The store layout is
``centroids.f32`` (K × dim float32, L2-normalized — the ``embeddings.f32`` idiom) plus a
``manifest.json`` carrying ``version``, per-cluster ``label``/``top_titles``, and fit
provenance.

Bands come from the experience columns the table already carries (ADR-0009/0018) — banding
stored numbers, never re-extracting — with intern detected from the title or
``employment_type`` since interns rarely carry a years figure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

_INTERN = re.compile(r"\bintern(ship)?\b|\btrainee\b", re.IGNORECASE)

# min_years -> band edges; None (no signal) is "unspecified", kept as its own honest series.
BANDS = ("unspecified", "intern", "entry", "mid", "senior", "staff")


def load(store: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """The centroid matrix (K × dim, unit rows) and its manifest."""
    manifest = json.loads((store / "manifest.json").read_text(encoding="utf-8"))
    centroids = np.fromfile(store / "centroids.f32", dtype="float32").reshape(
        manifest["k"], manifest["dim"]
    )
    return centroids, manifest


def save(store: Path, centroids: np.ndarray, manifest: dict[str, Any]) -> None:
    """Write the centroid store (the fit's only output contract)."""
    store.mkdir(parents=True, exist_ok=True)
    np.ascontiguousarray(centroids, dtype=np.float32).tofile(store / "centroids.f32")
    (store / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def assign(vectors: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """Nearest-centroid family per row — cosine via one matmul (both sides unit-normalized)."""
    return np.argmax(vectors @ centroids.T, axis=1)


def band(min_years: int | None, title: str | None, employment_type: str | None) -> str:
    """The seniority band for one row, from fields the served table already carries."""
    if _INTERN.search(title or "") or _INTERN.search(employment_type or ""):
        return "intern"
    if min_years is None:
        return "unspecified"
    if min_years <= 1:
        return "entry"
    if min_years <= 4:
        return "mid"
    if min_years <= 7:
        return "senior"
    return "staff"
