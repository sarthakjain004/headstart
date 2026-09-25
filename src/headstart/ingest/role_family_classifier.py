"""A served row's role family, from its title alone, by a trained classifier (ADR-0220).

The title is embedded with JobBERT-v2's title ("anchor") branch, a model trained to place job
titles that name the same occupation near each other, and a logistic-regression head trained on
labelled titles turns the embedding into a family. When the head's top probability is below the
manifest's cutoff, the row is ``unclassified-tech``: a family forced onto a title the head cannot
place would count as a trend in the wrong line.

A family depends only on the normalised title, so every copy of a posting agrees and a re-embed
of the description cannot move it. That also makes the answer cacheable. ``role_trends`` keeps a
title → family cache in ``data/state`` and embeds only titles the cache has not seen under the
current head. A new head starts with an empty cache, and the served table holds about 270,000
distinct titles, too many for one run. So each run spends a fixed time budget filling the cache,
saving after every chunk, and ``role_trends`` counts nothing until the cache covers the table:
Trends pauses for a few runs rather than charting a backlog as "unclassified".

Everything the head decides is fixed by ``config/role_family_classifier/``: the manifest (model
and its pinned revision, the families, the cutoff, the head's version) and the weights. A new
head is a new version, and a new version re-bases every Trends series.
"""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from headstart import log
from headstart.roles import NON_TECH

_log = log.get(__name__, __spec__)

# the abstain family: tech, but the head cannot place it
UNCLASSIFIED = "unclassified-tech"
_ENCODE_BATCH = 128
_FILL_CHUNK = 4096  # titles decided between two cache saves


def normalise(title: str | None) -> str:
    """The cache key and the head's input: lowercased, whitespace collapsed. Training and serving
    both go through this, so a title never meets the head in a form it was not trained on."""
    return " ".join((title or "").lower().split())


class Head:
    """The trained head and what it was trained for, as read from its directory."""

    def __init__(self, directory: Path) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        weights = np.load(directory / "head.npz")
        self.version: int = manifest["version"]
        self.model: str = manifest["model"]
        self.model_revision: str = manifest["model_revision"]
        self.families: list[str] = manifest["families"]
        self.cutoff: float = manifest["cutoff"]
        self._weights = weights["weights"].astype(np.float32)  # families x dim
        self._bias = weights["bias"].astype(np.float32)
        if self._weights.shape[0] != len(self.families):
            raise ValueError(
                f"{directory}: {self._weights.shape[0]} weight rows for "
                f"{len(self.families)} families — the manifest and head.npz disagree"
            )
        if UNCLASSIFIED in self.families:
            raise ValueError(
                f"{directory}: '{UNCLASSIFIED}' is what the cutoff produces, never a trained class"
            )

    def probabilities(self, vectors: np.ndarray) -> np.ndarray:
        """Each row's probability over :attr:`families`, before the cutoff."""
        return softmax(vectors @ self._weights.T + self._bias)

    def decide(self, vectors: np.ndarray) -> list[tuple[str, float]]:
        """``(family, top probability)`` per row; below the cutoff the family is ``UNCLASSIFIED``."""
        return choose_families(self.probabilities(vectors), self.families, self.cutoff)

    def check_families(self, listed: list[str]) -> None:
        """Refuse a head that decides a family the curated list lacks, or a list without the
        abstain family: either would count rows under a name no chart knows."""
        unknown = sorted(set(self.families) - set(listed) - {NON_TECH})
        if unknown:
            raise ValueError(
                f"the classifier head decides {unknown}, which the family list does not list"
            )
        if UNCLASSIFIED not in listed:
            raise ValueError(
                f"the family list must list '{UNCLASSIFIED}', the family a title the head "
                "cannot place is counted in"
            )


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = np.exp(logits - logits.max(axis=1, keepdims=True))
    return shifted / shifted.sum(axis=1, keepdims=True)


def choose_families(
    probabilities: np.ndarray, families: list[str], cutoff: float
) -> list[tuple[str, float]]:
    """``(family, top probability)`` per row, ``UNCLASSIFIED`` below ``cutoff``. The trainer
    chooses the cutoff through this same function, so training and serving decide alike."""
    best = probabilities.argmax(axis=1)
    top = probabilities[np.arange(len(best)), best]
    return [
        (families[b] if p >= cutoff else UNCLASSIFIED, float(p))
        for b, p in zip(best, top, strict=True)
    ]


@functools.cache
def _encoder(model: str, revision: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model, revision=revision, device="cpu")


def encode(titles: list[str], model: str, revision: str) -> np.ndarray:
    """JobBERT-v2's title-branch embeddings, as its model card prescribes (the ``anchor`` key),
    in input order.

    Titles are batched shortest first. A batch pads to its longest title, and served titles
    average ~10 tokens with a tail to 45, so in arrival order most of the work was padding.
    Measured 2026-09-25 on 4,096 served titles with 4 CPU threads: 2.3x faster, with vectors
    equal to within 1e-6."""
    import torch

    encoder = _encoder(model, revision)
    order = np.argsort([len(title) for title in titles], kind="stable")
    out = []
    with torch.inference_mode():
        for start in range(0, len(titles), _ENCODE_BATCH):
            batch = [titles[i] for i in order[start : start + _ENCODE_BATCH]]
            features = encoder.tokenize(batch)
            features["text_keys"] = ["anchor"]
            out.append(encoder.forward(features)["sentence_embedding"].cpu().numpy())
    if not out:
        return np.zeros((0, 0), np.float32)
    shortest_first = np.concatenate(out).astype(np.float32)
    vectors = np.empty_like(shortest_first)
    vectors[order] = shortest_first
    return vectors


@dataclass
class Cache:
    """``normalised title -> (family, top probability)``, valid for one head version. Mutable:
    :func:`fill` adds to it."""

    version: int
    decisions: dict[str, tuple[str, float]]


def load_cache(path: Path, version: int) -> Cache:
    """The cache for ``version``, or an empty one when the file is absent, unreadable or written
    under another head. A cache from another head holds another head's answers, so it is
    discarded rather than trusted."""
    empty = Cache(version, {})
    if not path.exists():
        return empty
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        stamped = (table.schema.metadata or {}).get(b"head_version")
        if stamped is None or int(stamped) != version:
            _log.info(
                f"title cache {path} is for head {stamped!r}, not {version}: starting empty"
            )
            return empty
        return Cache(
            version,
            {
                title: (family, confidence)
                for title, family, confidence in zip(
                    table["title"].to_pylist(),
                    table["family"].to_pylist(),
                    table["confidence"].to_pylist(),
                    strict=True,
                )
            },
        )
    except Exception as exc:  # noqa: BLE001 - a corrupt cache is rebuilt, never fatal
        _log.warning(
            f"title cache {path} unreadable ({type(exc).__name__}: {exc}): starting empty"
        )
        return empty


def save_cache(path: Path, cache: Cache) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    titles = sorted(cache.decisions)
    table = pa.table(
        {
            "title": titles,
            "family": [cache.decisions[t][0] for t in titles],
            "confidence": pa.array(
                [cache.decisions[t][1] for t in titles], pa.float32()
            ),
        },
        metadata={b"head_version": str(cache.version).encode()},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, staged, compression="zstd")
    staged.replace(path)


def fill(
    cache: Cache,
    head: Head,
    titles: Iterable[str | None],
    budget_seconds: float,
    checkpoint: Callable[[Cache], None],
) -> int:
    """Decide the titles the cache lacks, in chunks, until done or out of time; returns how many
    were added. ``checkpoint`` runs after every chunk, so a run killed mid-fill keeps its work."""
    missing = sorted({normalise(t) for t in titles} - cache.decisions.keys())
    started, added = time.monotonic(), 0
    for start in range(0, len(missing), _FILL_CHUNK):
        if time.monotonic() - started > budget_seconds:
            _log.info(
                f"classifier budget spent: {len(missing) - added} titles left for later runs"
            )
            break
        chunk = missing[start : start + _FILL_CHUNK]
        decided = head.decide(encode(chunk, head.model, head.model_revision))
        cache.decisions.update(zip(chunk, decided, strict=True))
        added += len(chunk)
        checkpoint(cache)
        _log.info(f"classified {added}/{len(missing)} new titles")
    return added


def coverage(cache: Cache, titles: Iterable[str | None]) -> float:
    """The share of ``titles`` (one per served row) whose normalised title the cache has decided."""
    keys = [normalise(t) for t in titles]
    return sum(k in cache.decisions for k in keys) / len(keys) if keys else 1.0


def family(cache: Cache, title: str | None) -> str:
    """The cached family for ``title``, or ``UNCLASSIFIED`` for one no run has decided yet."""
    decided = cache.decisions.get(normalise(title))
    return decided[0] if decided else UNCLASSIFIED
