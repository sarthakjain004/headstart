"""Shared conventions for the embed/search/eval layer (ADR-0005, ADR-0008).

The model id, the load-bearing task prefixes, the LanceDB table name, the encoder
factory, and the filter-then-rank where-clause builder live here once. The six
embed/search/eval scripts import them instead of re-declaring their own copies, so a
mismatched prefix or model id can't drift into one script and silently degrade ranking
(ADR-0005 warns a wrong prefix throws no error), and every caller escapes filter input
the same way.

Only the encoder helpers need torch/sentence-transformers; they import lazily so the
constants and :func:`build_filter` stay importable (and unit-testable) without the ML stack.
"""

from __future__ import annotations

from typing import Any

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DOC_PREFIX = "search_document: "  # index time (ADR-0005)
QUERY_PREFIX = "search_query: "  # query time (ADR-0005)
TABLE = "wellfound"

# employment_type is a fixed vocabulary (the UI <select>); an unrecognized value is
# rejected rather than interpolated into the LanceDB where-clause.
EMPLOYMENT_TYPES = frozenset({"full-time", "contract", "internship", "cofounder"})


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


def build_filter(
    *,
    remote: bool = False,
    employment_type: str | None = None,
    max_years: int | None = None,
) -> str | None:
    """Build the filter-then-rank where-clause (ADR-0008), or None if nothing is filtered.

    ``employment_type`` is validated against :data:`EMPLOYMENT_TYPES` — an unknown value
    raises ``ValueError`` instead of being interpolated into the clause. ``max_years`` must
    already be an int; jobs with unknown experience (``min_years IS NULL``) are kept, since
    "unknown" is not "too senior" (ADR-0009).
    """
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if employment_type:
        if employment_type not in EMPLOYMENT_TYPES:
            raise ValueError(f"unknown employment_type {employment_type!r}")
        filters.append(f"employment_type = '{employment_type}'")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    return " AND ".join(filters) if filters else None
