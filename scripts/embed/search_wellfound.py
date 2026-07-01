"""Semantic + structured search over the Wellfound LanceDB index (ADR-0008) — filter-then-rank.

Encode the query (``search_query:`` prefix, ADR-0005), optionally pre-filter on the typed metadata
(``--remote`` / ``--type``), then rank the survivors by cosine similarity in LanceDB. Pre-filtering
narrows the candidate set *before* ranking — the hybrid retrieval pattern (semantic intent ranked,
hard constraints filtered).

Usage:
  python scripts/embed/search_wellfound.py "backend engineer at a climate startup" -k 10
  python scripts/embed/search_wellfound.py "machine learning engineer" --remote --type full-time
"""

from __future__ import annotations

import argparse
from pathlib import Path

import lancedb
import torch
from sentence_transformers import SentenceTransformer

_DB = Path(__file__).resolve().parents[2] / "data" / "lancedb"
_TABLE = "wellfound"
_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_QUERY_PREFIX = "search_query: "  # ADR-0005: queries get this prefix at search time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", help="natural-language search")
    ap.add_argument("-k", type=int, default=10, help="how many results")
    ap.add_argument("--remote", action="store_true", help="only remote jobs")
    ap.add_argument(
        "--type", dest="employment_type", help="employment_type, e.g. full-time"
    )
    ap.add_argument(
        "--max-years",
        type=int,
        dest="max_years",
        help="jobs requiring at most N years (keeps jobs with unknown experience)",
    )
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(_MODEL, trust_remote_code=True, device=device)
    if device == "mps":
        model = model.half()
    query_vec = model.encode([_QUERY_PREFIX + args.query], normalize_embeddings=True)[
        0
    ].astype("float32")

    table = lancedb.connect(_DB).open_table(_TABLE)
    search = table.search(query_vec).metric("cosine")
    filters = []
    if args.remote:
        filters.append("remote = true")
    if args.employment_type:
        filters.append(f"employment_type = '{args.employment_type}'")
    if args.max_years is not None:
        # unknown experience (min_years IS NULL) is kept — "unknown" isn't "too senior"
        filters.append(f"(min_years <= {args.max_years} OR min_years IS NULL)")
    if filters:
        search = search.where(
            " AND ".join(filters), prefilter=True
        )  # filter first, then rank survivors
    rows = search.limit(args.k).to_list()

    label = f"  [filter: {' AND '.join(filters)}]" if filters else ""
    print(f'\nquery: "{args.query}"{label}  ({len(rows)} hits)\n')
    for rank, r in enumerate(rows, 1):
        sim = 1 - r["_distance"]  # cosine distance -> similarity
        yrs = r.get("min_years")
        print(
            f"{rank:2}. {sim:.3f}  {r['title']} — {r['company']}  "
            f"[{r.get('location') or '?'} · remote={r['remote']} · {r.get('employment_type') or '?'}"
            f" · min_years={yrs if yrs is not None else '?'}]"
        )


if __name__ == "__main__":
    main()
