"""A served row's role family, from its title and its description, by a trained classifier
(ADR-0220, ADR-0222).

The head is linear over two inputs. The title is embedded with JobBERT-v2's title ("anchor")
branch, a model trained to place job titles that name the same occupation near each other. The
description arrives as the row's own served ``vector`` (nomic, title plus cleaned description),
which the index already holds, so it costs no encoding. When the head's top probability is below
the manifest's cutoff, the row is ``unclassified-tech``: a family forced onto a row the head cannot
place would count as a trend in the wrong line.

Because the head is linear, its logits split into a **title part** (``JobBERT(title) @ W_title``)
and a **row part** (``vector @ W_row + bias``). The title part is what costs an encoding, so
``role_trends`` caches it per normalised title in ``data/state`` and embeds only titles the cache
has not seen under the current head; the row part is one matrix product over the served vectors
each run. A new head starts with an empty cache, and the served table holds about 290,000 distinct
titles, too many for one run. So each run spends a fixed time budget filling the cache, saving
after every chunk, and ``role_trends`` counts nothing until the cache covers the table: Trends
pauses for a few runs rather than charting a backlog as "unclassified".

Copies of a posting share a title and nearly always a family; the description moves a row only
where it contradicts its title (a "Systems Engineer" at a utility is non-tech). So a re-embedded
description can move a row, which the ADR-0057 transition ledger records.

Everything the head decides is fixed by ``config/role_family_classifier/``: the manifest (title
model and its pinned revision, the row vector's model and width, the families, the cutoff, the
head's version) and the weights. A new head is a new version, and a new version re-bases every
Trends series.
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
        self.row_vector_model: str = manifest["row_vector"]["model"]
        self.row_vector_dim: int = manifest["row_vector"]["dim"]
        self.families: list[str] = manifest["families"]
        self.cutoff: float = manifest["cutoff"]
        self._title_weights = weights["title_weights"].astype(
            np.float32
        )  # families x dim
        self._row_weights = weights["row_weights"].astype(
            np.float32
        )  # families x row dim
        self._bias = weights["bias"].astype(np.float32)
        if (
            not (
                len(self._title_weights) == len(self._row_weights) == len(self.families)
            )
            or self._row_weights.shape[1] != self.row_vector_dim
        ):
            raise ValueError(
                f"{directory}: weights {self._title_weights.shape} and "
                f"{self._row_weights.shape} for {len(self.families)} families and a "
                f"{self.row_vector_dim}-wide row vector — the manifest and head.npz disagree"
            )
        if UNCLASSIFIED in self.families:
            raise ValueError(
                f"{directory}: '{UNCLASSIFIED}' is what the cutoff produces, never a trained class"
            )

    def title_logits(self, title_vectors: np.ndarray) -> np.ndarray:
        """The title part of each row's logits: what the cache keeps per title."""
        return title_vectors @ self._title_weights.T

    def row_logits(self, row_vectors: np.ndarray) -> np.ndarray:
        """The row part of each row's logits, bias included, from its served ``vector``."""
        return row_vectors @ self._row_weights.T + self._bias

    def decide(
        self, title_logits: np.ndarray, row_logits: np.ndarray
    ) -> list[tuple[str, float]]:
        """``(family, top probability)`` per row; below the cutoff the family is ``UNCLASSIFIED``."""
        return choose_families(
            softmax(title_logits + row_logits), self.families, self.cutoff
        )

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

    Titles are batched fewest tokens first. A batch pads to its longest title, and served titles
    average ~10 tokens with a tail to 45, so in arrival order most of the work was padding.
    Measured 2026-09-25 on 4,096 served titles with 4 CPU threads (three alternated repeats):
    this path encodes 589 titles/s against 269 in arrival order, with vectors equal to within
    1.5e-5. Sorting by characters instead reached only 329: characters are a weak proxy for tokens
    (rank correlation 0.77), so the titles are tokenized once to sort them."""
    import torch

    if not titles:
        return np.zeros((0, 0), np.float32)
    encoder = _encoder(model, revision)
    token_counts = [len(ids) for ids in encoder.tokenizer(titles)["input_ids"]]
    order = np.argsort(token_counts, kind="stable")
    out = []
    with torch.inference_mode():
        for start in range(0, len(titles), _ENCODE_BATCH):
            batch = [titles[i] for i in order[start : start + _ENCODE_BATCH]]
            features = encoder.tokenize(batch)
            features["text_keys"] = ["anchor"]
            out.append(encoder.forward(features)["sentence_embedding"].cpu().numpy())
    sorted_vectors = np.concatenate(out).astype(np.float32)
    vectors = np.empty_like(sorted_vectors)
    vectors[order] = sorted_vectors
    return vectors


@dataclass
class Cache:
    """``normalised title -> the head's title logits`` (one per family), valid for one head
    version. Mutable: :func:`fill` adds to it."""

    version: int
    title_logits: dict[str, np.ndarray]


def load_cache(path: Path, version: int) -> Cache:
    """The cache for ``version``, or an empty one when the file is absent, unreadable or written
    under another head. A cache from another head holds another head's logits, so it is
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
        logits = table["logits"].combine_chunks()
        width = logits.type.list_size
        matrix = logits.flatten().to_numpy().reshape(-1, width)
        return Cache(
            version, dict(zip(table["title"].to_pylist(), matrix, strict=True))
        )
    except Exception as exc:  # noqa: BLE001 - a corrupt cache is rebuilt, never fatal
        _log.warning(
            f"title cache {path} unreadable ({type(exc).__name__}: {exc}): starting empty"
        )
        return empty


def save_cache(path: Path, cache: Cache) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    titles = sorted(cache.title_logits)
    matrix = np.array([cache.title_logits[t] for t in titles], dtype=np.float32)
    width = matrix.shape[1] if titles else 0
    table = pa.table(
        {
            "title": titles,
            "logits": pa.FixedSizeListArray.from_arrays(matrix.reshape(-1), width),
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
    """Encode the titles the cache lacks, in chunks, until done or out of time; returns how many
    were added. ``checkpoint`` runs after every chunk, so a run killed mid-fill keeps its work."""
    missing = sorted({normalise(t) for t in titles} - cache.title_logits.keys())
    started, added = time.monotonic(), 0
    for start in range(0, len(missing), _FILL_CHUNK):
        if time.monotonic() - started > budget_seconds:
            _log.info(
                f"classifier budget spent: {len(missing) - added} titles left for later runs"
            )
            break
        chunk = missing[start : start + _FILL_CHUNK]
        logits = head.title_logits(encode(chunk, head.model, head.model_revision))
        cache.title_logits.update(zip(chunk, logits, strict=True))
        added += len(chunk)
        checkpoint(cache)
        _log.info(f"classified {added}/{len(missing)} new titles")
    return added


def coverage(cache: Cache, titles: Iterable[str | None]) -> float:
    """The share of ``titles`` (one per served row) whose normalised title the cache holds."""
    keys = [normalise(t) for t in titles]
    return sum(k in cache.title_logits for k in keys) / len(keys) if keys else 1.0


def decide_rows(
    cache: Cache, head: Head, titles: list[str | None], row_logits: np.ndarray
) -> list[str]:
    """Each served row's family, from its title's cached logits plus its own row logits. A row
    whose title no run has encoded yet is ``UNCLASSIFIED``; the warm-up gate keeps a table with
    many of those out of the ledger."""
    families = [UNCLASSIFIED] * len(titles)
    known = [
        (i, logits)
        for i, title in enumerate(titles)
        if (logits := cache.title_logits.get(normalise(title))) is not None
    ]
    if known:
        rows = [i for i, _ in known]
        decided = head.decide(np.stack([l for _, l in known]), row_logits[rows])
        for i, (family, _) in zip(rows, decided, strict=True):
            families[i] = family
    return families
