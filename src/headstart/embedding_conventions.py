"""The conventions a Doc vector and a Query vector must share (ADR-0005, ADR-0008, ADR-0194).

The model id, the load-bearing task prefixes, the encoder factory and the served LanceDB table's
name live here once. The ingest stages that write vectors (``embed_plan``, ``embed_run``,
``embed_merge``, ``doc_prep``, ``index``) and :mod:`headstart.search`, which reads them, import
these instead of re-declaring their own copies, so a mismatched prefix or model id can't drift
into one side and silently degrade ranking (ADR-0005 warns a wrong prefix throws no error).

Moved out of :mod:`headstart.search` (ADR-0194) so the pipeline no longer imports the serving
path to learn a model id. Only the encoder helpers need torch/sentence-transformers; they import
lazily so the constants stay importable without the ML stack.
"""

from __future__ import annotations

from typing import Any

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DOC_PREFIX = "search_document: "  # index time (ADR-0005)
QUERY_PREFIX = "search_query: "  # query time (ADR-0005)
PROD_TABLE = "jobs"  # the product's tech corpus (ADR-0019)


def load_encoder() -> Any:
    """The nomic bi-encoder, on the Apple GPU (MPS, fp16) when available else CPU."""
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, trust_remote_code=True, device=device)
    return model.half() if device == "mps" else model


def encode_query(model: Any, text: str) -> Any:
    """Encode one search query: query prefix, L2-normalized, float32 — ready for cosine search."""
    return model.encode([QUERY_PREFIX + text], normalize_embeddings=True)[0].astype(
        "float32"
    )
