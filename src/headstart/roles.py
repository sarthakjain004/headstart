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
    """Nearest-centroid cluster per row — cosine via one matmul (both sides unit-normalized)."""
    return np.argmax(vectors @ centroids.T, axis=1)


NON_TECH = "non-tech"  # the reserved family: counted as a diagnostic, never charted


def load_families(path: Path, manifest: dict[str, Any]) -> dict[int, str | None]:
    """The curated cluster → family map: ``{cluster_id: family_name}``, None where the cluster
    is non-tech (ADR-0040).

    k-means clusters are raw material, not the taxonomy: a fit splits one role family across
    several clusters by seniority or phrasing, and concentrates the tech filter's non-tech
    creep (retail "front end", data-entry spam, manufacturing/civil engineering) into clusters
    of its own. The map is curated and lives in git — it is reviewable content, unlike the
    generated centroids.

    Validated hard, because both failure modes are silent: a cluster missing from the map
    would drop out of every chart unnoticed, and a map written against a different fit would
    label rows with another fit's families.
    """
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec["centroid_version"] != manifest["version"]:
        raise ValueError(
            f"{path} maps centroid version {spec['centroid_version']}, but the store holds "
            f"version {manifest['version']} — re-curate the map after a refit (ADR-0040)"
        )
    mapping: dict[int, str | None] = {}
    for family in spec["families"]:
        if family["name"] == NON_TECH:
            raise ValueError(
                f"{path}: '{NON_TECH}' is reserved for the diagnostic series — a family of "
                "that name would collide with it in the ledger"
            )
        for cluster in family["clusters"]:
            if cluster in mapping:
                raise ValueError(f"{path}: cluster {cluster} mapped twice")
            mapping[cluster] = family["name"]
    for cluster in spec["non_tech"]["clusters"]:
        if cluster in mapping:
            raise ValueError(f"{path}: cluster {cluster} mapped twice")
        mapping[cluster] = None
    missing = sorted(set(range(manifest["k"])) - mapping.keys())
    if missing:
        raise ValueError(
            f"{path} leaves cluster(s) {missing} unmapped — every cluster must land in a "
            "family or in non_tech, or its rows vanish from the chart"
        )
    return mapping


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


WATCH_PREFIX = "watch:"  # ledger namespace for watched roles, so they can never collide with a family


class WatchRole:
    """One curated role tracked by title pattern (ADR-0051) — compiled once, matched per row.

    Title patterns rather than centroids, deliberately: a role this specific (~1% of the corpus)
    does not earn its own cluster at any practical k, and a pattern is explainable — you can say
    exactly why a Job counted — and survives a centroid refit unchanged.
    """

    __slots__ = ("name", "label", "parent", "_patterns")

    def __init__(self, name: str, label: str, parent: str, patterns: list[str]) -> None:
        self.name, self.label, self.parent = name, label, parent
        self._patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

    def matches(self, title: str | None) -> bool:
        return bool(title) and any(p.search(title) for p in self._patterns)


def load_watchlist(path: Path, family_names: set[str]) -> list[WatchRole]:
    """The curated watchlist, validated hard — the same posture as :func:`load_families`,
    because the failure modes are as silent: a bad parent orphans the role from every drill,
    and a bad pattern would either crash the pipeline step or quietly count nothing.

    Missing file is an empty list, not an error: the watchlist is optional by design.
    """
    if not path.exists():
        return []
    spec = json.loads(path.read_text(encoding="utf-8"))
    watched: list[WatchRole] = []
    seen: set[str] = set()
    for entry in spec["roles"]:
        name = entry["name"]
        if name in seen:
            raise ValueError(f"{path}: watch role '{name}' defined twice")
        seen.add(name)
        if entry["parent"] not in family_names:
            raise ValueError(
                f"{path}: watch role '{name}' names parent '{entry['parent']}', which is not "
                "a family in role_families.json — the drill it should appear under does not exist"
            )
        try:
            watched.append(
                WatchRole(
                    name, entry.get("label", name), entry["parent"], entry["match"]
                )
            )
        except re.error as exc:
            raise ValueError(
                f"{path}: watch role '{name}' has a bad pattern: {exc}"
            ) from exc
    return watched
