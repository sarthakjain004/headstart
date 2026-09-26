"""The conventions a Doc vector and a Query vector must share (ADR-0005, ADR-0008, ADR-0194).

The model id, the load-bearing task prefixes, the encoder factory and the served LanceDB table's
name live here once. The ingest stages that write or read the vectors and the served table, and
:mod:`headstart.serving.job_search`, which queries them, import these instead of re-declaring their own copies, so a mismatched prefix or model id can't drift
into one side and silently degrade ranking (ADR-0005 warns a wrong prefix throws no error).

Moved out of :mod:`headstart.search` (ADR-0194) so the pipeline no longer imports the serving
path to learn a model id. Only the encoder helpers need torch/sentence-transformers; they import
lazily so the constants stay importable without the ML stack.
"""

from __future__ import annotations

from typing import Any

MODEL = "nomic-ai/nomic-embed-text-v1.5"
# Pinned, because `trust_remote_code` runs Python fetched from the Hub: unpinned, a load runs
# whatever nomic last pushed (embed_plan's tokenizer load does not use the pin yet). Two repos, since the model's `auto_map` sends its modeling code to
# `nomic-ai/nomic-bert-2048`, which `revision` does not reach and `code_revision` does. Both are
# the commits `main` named, and the pipeline's cache held, on 2026-09-26. A new revision is a new
# model cache key in pipeline.yml (tests/test_embedding_conventions.py).
MODEL_REVISION = "e9b6763023c676ca8431644204f50c2b100d9aab"
MODEL_CODE_REVISION = "7710840340a098cfb869c4f65e87cf2b1b70caca"
DOC_PREFIX = "search_document: "  # index time (ADR-0005)
QUERY_PREFIX = "search_query: "  # query time (ADR-0005)
PROD_TABLE = "jobs"  # the product's tech corpus (ADR-0019)


def open_model(device: str) -> Any:
    """The nomic bi-encoder at its pinned revisions, on ``device``."""
    from sentence_transformers import SentenceTransformer

    pin = {"code_revision": MODEL_CODE_REVISION}
    return SentenceTransformer(
        MODEL,
        revision=MODEL_REVISION,
        trust_remote_code=True,
        device=device,
        model_kwargs=pin,
        config_kwargs=pin,
    )


def load_encoder() -> Any:
    """The nomic bi-encoder, on the Apple GPU (MPS, fp16) when available else CPU."""
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = open_model(device)
    return model.half() if device == "mps" else model


def encode_query(model: Any, text: str) -> Any:
    """Encode one search query: query prefix, L2-normalized, float32 — ready for cosine search."""
    return model.encode([QUERY_PREFIX + text], normalize_embeddings=True)[0].astype(
        "float32"
    )
