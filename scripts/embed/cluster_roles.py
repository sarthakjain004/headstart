#!/usr/bin/env python3
"""Fit the frozen role-family centroids for the trends ledger (ADR-0040) — a one-off.

Reads the embedding store (``data/embeddings/jobs/``: row-aligned ``meta.jsonl`` +
``embeddings.f32``), MiniBatch-k-means the L2-normalized vectors into role families, and
writes ``data/state/role_centroids/``:

- ``centroids.f32`` — K x dim float32, L2-normalized (same raw layout as ``embeddings.f32``),
  so runtime assignment is one matmul + argmax (cosine on unit vectors).
- ``manifest.json`` — version, K, dim, fit provenance, and per-cluster ``label`` (a
  provisional token-derived name, meant to be hand- or LLM-polished before the ledger ships)
  plus ``top_titles`` (the 30 most common titles) so the naming pass needs nothing else.

K: ``--k N`` fixes it; the default sweeps ``--sweep`` (16,24,32,40) and keeps the best
silhouette on a 20k sample. The centroids are FROZEN once shipped — refitting re-bases every
trend series, so it bumps ``version`` and is an explicit decision (ADR-0040), not maintenance.

Run on CI (the store is ~850 MB on HF; heavy pulls stay off laptops — cluster-roles.yml):
    python -m headstart.ingest.state_fetch 'data/embeddings/jobs/*'
    python scripts/embed/cluster_roles.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import log, roles  # noqa: E402

_log = log.get("headstart.cluster_roles")

_STORE = Path("data/embeddings/jobs")
_OUT = Path("data/state/role_centroids")
_SAMPLE = 20_000  # silhouette sample: full-corpus silhouette is O(n^2) and buys nothing
_TOP_TITLES = 30

# Tokens that name a level or a workplace, not a role family — dropped from the provisional
# label so "senior backend engineer" and "backend engineer" pull toward the same name.
_LABEL_STOPWORDS = frozenset(
    "senior sr jr junior staff lead principal head chief intern trainee associate"
    " i ii iii iv v 1 2 3 4 the a an of and or remote hybrid onsite".split()
)


def load_store(store: Path) -> tuple[np.ndarray, list[str]]:
    """The store's vectors and row-aligned titles."""
    dim = json.loads((store / "manifest.json").read_text())["dim"]
    vectors = np.fromfile(store / "embeddings.f32", dtype="float32").reshape(-1, dim)
    titles = [
        json.loads(line).get("title") or ""
        for line in (store / "meta.jsonl").open(encoding="utf-8")
    ]
    if len(titles) != len(vectors):
        raise SystemExit(f"store torn: {len(vectors)} vectors, {len(titles)} meta rows")
    return vectors, titles


def fit(vectors: np.ndarray, k: int, seed: int = 0):
    from sklearn.cluster import MiniBatchKMeans

    km = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=10, batch_size=4096)
    assign = km.fit_predict(vectors)
    centroids = km.cluster_centers_.astype(np.float32)
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)  # cosine assignment
    return centroids, assign


def sampled_silhouette(vectors: np.ndarray, assign: np.ndarray, seed: int = 0) -> float:
    from sklearn.metrics import silhouette_score

    rng = np.random.default_rng(seed)
    idx = rng.choice(len(vectors), size=min(_SAMPLE, len(vectors)), replace=False)
    return float(silhouette_score(vectors[idx], assign[idx]))


def provisional_label(titles: list[str]) -> str:
    """A deterministic starter name: the 2 most common role tokens in the cluster's titles."""
    tokens = Counter()
    for title in titles:
        for token in re.findall(r"[a-z+#]+", title.lower()):
            if token not in _LABEL_STOPWORDS and len(token) > 1:
                tokens[token] += 1
    return " ".join(t for t, _ in tokens.most_common(2)) or "unnamed"


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", type=Path, default=_STORE)
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument("--k", type=int, default=0, help="fix K (default: sweep and pick)")
    ap.add_argument("--sweep", default="16,24,32,40")
    args = ap.parse_args()

    vectors, titles = load_store(args.store)
    _log.info(f"store: {len(vectors)} vectors of dim {vectors.shape[1]}")

    if args.k:
        candidates = [args.k]
    else:
        candidates = [int(x) for x in args.sweep.split(",")]
    best = None  # (silhouette, k, centroids, assign)
    for k in candidates:
        centroids, assign = fit(vectors, k)
        score = sampled_silhouette(vectors, assign)
        _log.info(f"K={k}: silhouette {score:.4f} (sampled {_SAMPLE})")
        if best is None or score > best[0]:
            best = (score, k, centroids, assign)
    score, k, centroids, assign = best
    _log.info(f"chosen K={k} (silhouette {score:.4f})")

    clusters = []
    for c in range(k):
        members = [titles[i] for i in np.flatnonzero(assign == c)]
        top = [t for t, _ in Counter(members).most_common(_TOP_TITLES)]
        clusters.append(
            {
                "id": c,
                "label": provisional_label(members),
                "count_at_fit": len(members),
                "top_titles": top,
            }
        )
        _log.info(f"cluster {c}: {len(members)} rows — {clusters[-1]['label']}")

    roles.save(
        args.out,
        centroids,
        {
            "version": 1,
            "k": k,
            "dim": int(centroids.shape[1]),
            "normalized": True,
            "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fitted_on_rows": len(vectors),
            "silhouette_sampled": round(score, 4),
            "clusters": clusters,
        },
    )
    _log.info(f"wrote {k} centroids -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
