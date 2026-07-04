"""Score the search with graded qrels — the payoff (Lesson 7).

Turns the validated judge grades (``judge_labels.jsonl``) into graded, multi-relevant qrels, runs
the *same* semantic search to produce a run, and scores it with ranx. This is the defensible
nDCG@10 that Lesson 4's single-relevant qrels could not give — the judge was validated at
quadratic-weighted κ ≈ 0.64 (substantial), so we trust its grades across the pool.

Two qrels variants are printed:
  * judge-graded (headline): every grade comes from the validated LLM judge.
  * human-gold where labeled: your 62 hand labels override the judge on the pairs you graded; the
    judge fills the rest — a robustness check that the number doesn't hinge on the judge alone.

Honest caveat (printed too): the pool is a *single-system* pool (only this search's top-15 were
judged), so nDCG measures how well the search rank-orders its own top results by graded relevance,
not how it compares to an ideal retriever over the whole corpus.

Run: ``.venv/bin/python scripts/eval/score_graded.py``
"""

from __future__ import annotations

import json
from pathlib import Path

import lancedb
from ranx import Qrels, Run, evaluate

from headstart.search import EVAL_TABLE, encode_query, load_encoder

_ROOT = Path(__file__).resolve().parents[2]
_JUDGE = _ROOT / "data" / "eval" / "judge_labels.jsonl"
_HUMAN = _ROOT / "data" / "eval" / "human_labels.jsonl"
_DB = _ROOT / "data" / "lancedb"
_DEPTH = 10  # nDCG@10 scores the top 10 the user would actually see
_METRICS = ["ndcg@10", "ndcg@5", "mrr"]


def _load(path: Path) -> dict[tuple[str, str], int]:
    return {
        (r["query"], r["job_id"]): int(r["grade"])
        for r in (json.loads(line) for line in path.open(encoding="utf-8"))
    }


def _qrels(
    grades: dict[tuple[str, str], int], qid: dict[str, str]
) -> dict[str, dict[str, int]]:
    """Graded qrels for ranx: {qid: {job_id: grade}} for every grade >= 1 (0 = not relevant)."""
    out: dict[str, dict[str, int]] = {}
    for (query, job_id), grade in grades.items():
        if grade >= 1:
            out.setdefault(qid[query], {})[job_id] = grade
    return out


def main() -> None:
    judge = _load(_JUDGE)
    human = _load(_HUMAN)
    queries: list[str] = []
    for query, _ in judge:
        if query not in queries:
            queries.append(query)
    qid = {q: f"q{i}" for i, q in enumerate(queries)}

    model = load_encoder()
    table = lancedb.connect(_DB).open_table(EVAL_TABLE)

    run: dict[str, dict[str, float]] = {}
    for q in queries:
        vec = encode_query(model, q)
        hits = table.search(vec).metric("cosine").limit(_DEPTH).to_list()
        run[qid[q]] = {
            h["id"]: float(_DEPTH - i) for i, h in enumerate(hits)
        }  # score = search order
    run_obj = Run(run)

    hybrid = dict(judge)
    hybrid.update(human)  # your hand labels win where they exist
    variants = [
        ("judge-graded qrels  (headline)", _qrels(judge, qid)),
        ("human-gold where you labeled  ", _qrels(hybrid, qid)),
    ]

    print(f"scored {len(queries)} queries against their top-{_DEPTH} run\n")
    for name, qd in variants:
        res = evaluate(Qrels(qd), run_obj, _METRICS)
        cells = "   ".join(f"{m} = {res[m]:.3f}" for m in _METRICS)
        print(f"  {name}   {cells}")

    print("\n  per-query nDCG@10 (judge-graded):")
    qrels_judge = _qrels(judge, qid)
    for q in queries:
        score = evaluate(
            Qrels({qid[q]: qrels_judge[qid[q]]}), Run({qid[q]: run[qid[q]]}), "ndcg@10"
        )
        print(f"      {score:.3f}  {q}")

    print(
        "\n  (single-system pool: nDCG reflects how well the search orders its own top-15 by grade,"
        "\n   not a comparison against an ideal retriever over the full corpus.)"
    )


if __name__ == "__main__":
    main()
