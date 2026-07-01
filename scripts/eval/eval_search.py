"""Evaluate HeadStart's semantic search against a qrels set — the first eval harness.

For each (query, known-relevant job) in ``data/eval/qrels.jsonl``: encode the query (search_query:
prefix, ADR-0005), run the LanceDB vector search, find the rank of the known-relevant job, and
aggregate the standard order-aware metrics. Single relevant doc per query (synthetic qrels), so
recall@k / MRR / nDCG@k all reduce to "how high did the source job come back?".

Metrics are computed by hand here so each is legible; ranx is the production swap once qrels carry
multiple graded-relevant docs. Run: python scripts/eval/eval_search.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import lancedb
import torch
from sentence_transformers import SentenceTransformer

_ROOT = Path(__file__).resolve().parents[2]
_QRELS = _ROOT / "data" / "eval" / "qrels.jsonl"
_DB = _ROOT / "data" / "lancedb"
_TABLE = "wellfound"
_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_QUERY_PREFIX = "search_query: "
_K = 10
_DEPTH = 50  # search this deep so we can report a rank even when it falls outside k


def main() -> None:
    qrels = [json.loads(line) for line in _QRELS.open(encoding="utf-8")]
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(_MODEL, trust_remote_code=True, device=device)
    if device == "mps":
        model = model.half()
    table = lancedb.connect(_DB).open_table(_TABLE)

    rr, recall, ndcg = [], [], []
    print(f"scoring {len(qrels)} queries against the {table.count_rows()}-job index\n")
    for q in qrels:
        vec = model.encode([_QUERY_PREFIX + q["query"]], normalize_embeddings=True)[
            0
        ].astype("float32")
        hits = [
            r["id"] for r in table.search(vec).metric("cosine").limit(_DEPTH).to_list()
        ]
        rank = hits.index(q["relevant_id"]) + 1 if q["relevant_id"] in hits else None
        rr.append(1 / rank if rank else 0.0)
        recall.append(1.0 if rank and rank <= _K else 0.0)
        ndcg.append(
            1 / math.log2(rank + 1) if rank and rank <= _K else 0.0
        )  # IDCG=1 for one relevant
        print(f"  rank {str(rank) if rank else '>' + str(_DEPTH):>4}   «{q['query']}»")

    n = len(qrels)
    print(f"\n  recall@{_K} = {sum(recall) / n:.3f}   (source job in the top {_K})")
    print(f"  MRR        = {sum(rr) / n:.3f}   (how high the source job ranks)")
    print(f"  nDCG@{_K}  = {sum(ndcg) / n:.3f}   (position-discounted)")


if __name__ == "__main__":
    main()
