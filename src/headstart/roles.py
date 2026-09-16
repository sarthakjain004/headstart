"""Role-trend taxonomy seam (ADR-0040, ADR-0051): frozen family centroids × experience bands,
plus the watchlist of named roles tracked by title.

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

The **watchlist** (ADR-0051) is the one axis here that is deliberately *not* centroid-derived:
roles too small to earn a cluster, matched on title patterns instead, so membership is
explainable per Job and survives a refit that re-bases every centroid.
"""

from __future__ import annotations

import hashlib
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


def family_map_fingerprint(path: Path) -> str:
    """A short digest over what the family map *means* — which cluster lands in which family,
    and the non-tech set — not over its bytes.

    A curation edit (splitting a family, moving a cluster) changes what a trends chart's numbers
    mean without requiring a centroid refit, and today nothing records when that happened; this
    lets a caller detect it by comparing fingerprints tick over tick, rather than depending on
    someone remembering to bump a counter by hand (the exact failure CLAUDE.md's
    ``DERIVATIONS_VERSION`` rule already documents happening twice for a hand-maintained one).
    ``label``/``note`` are deliberately excluded: rewording a family's description is not a
    change to what it counts, and would otherwise churn the fingerprint on every doc polish.

    ``centroid_version`` is deliberately NOT part of the hash, even though it's right there in
    the spec: a caller already tracks that value as its own, separate signal (ADR-0164's epoch
    tuple carries both), and folding it in here would make a refit *always* also register as a
    "family map edited" event even when the curated content is untouched — coupling two things
    the tuple exists to keep independently detectable.
    """
    spec = json.loads(path.read_text(encoding="utf-8"))
    meaning = {
        "families": sorted(
            (family["name"], sorted(family["clusters"])) for family in spec["families"]
        ),
        "non_tech": sorted(spec["non_tech"]["clusters"]),
    }
    return hashlib.sha256(
        json.dumps(meaning, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


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
    """One curated role tracked by title pattern (ADR-0051, amended by ADR-0052) — compiled once,
    matched per row.

    Title patterns rather than centroids, deliberately, for two different reasons. A role like
    Forward Deployed Engineer is too small (under 0.2% of the rows the fit clustered) to earn its
    own cluster at any practical k. A domain role like Backend is the opposite — large, but still
    unclusterable here, because k-means split the SWE catch-all by seniority and phrasing rather
    than by domain (see role_families.json's note on `software-engineering`), so no cluster means
    "backend". Either way a pattern is explainable — you can say exactly why a Job counted — and
    survives a centroid refit unchanged.

    A watch role is an overlay, never a partition: it re-counts Jobs already counted in their
    family, and overlaps its siblings (a "Senior Backend QA Engineer" counts under both).
    """

    __slots__ = ("_patterns", "label", "name", "parent")

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
