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

from headstart.search import EVAL_TABLE, encode_query, eval_filter, load_encoder

_DB = Path(__file__).resolve().parents[2] / "data" / "lancedb"


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

    try:
        where = eval_filter(
            remote=args.remote,
            employment_type=args.employment_type,
            max_years=args.max_years,
        )
    except ValueError as exc:
        ap.error(str(exc))

    model = load_encoder()
    query_vec = encode_query(model, args.query)

    table = lancedb.connect(_DB).open_table(EVAL_TABLE)
    search = table.search(query_vec).metric("cosine")
    if where:
        search = search.where(
            where, prefilter=True
        )  # filter first, then rank survivors
    rows = search.limit(args.k).to_list()

    label = f"  [filter: {where}]" if where else ""
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
