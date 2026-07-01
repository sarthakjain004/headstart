"""Pool the candidates to judge — step 1 of building trustworthy graded qrels (Lessons 5–6).

For each query in ``data/eval/qrels.jsonl`` we run the *same* semantic search the harness uses and
take the top ``_POOL_DEPTH`` hits, then union in the qrels' known-relevant job so it is always
graded. Each resulting (query, job) pair is a candidate the LLM judge (``judge_pool.py``) and you
(``label_slice.py``) will grade 0–3. The LanceDB index stores only metadata, so we render each job's
text — including its **description** — from ``data/jobs/wellfound.csv`` (joined by ``id``), which is
what a judge actually needs to read.

Writes ``data/eval/pool.jsonl`` incrementally, one object per line:
  ``{"query": ..., "job_id": ..., "rank": int|null, "job_text": ...}``  (rank is null for a unioned
  relevant job the search missed).

Run: ``.venv/bin/python scripts/eval/pool_candidates.py``
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import lancedb
import torch
from sentence_transformers import SentenceTransformer

_ROOT = Path(__file__).resolve().parents[2]
_QRELS = _ROOT / "data" / "eval" / "qrels.jsonl"
_JOBS_CSV = _ROOT / "data" / "jobs" / "wellfound.csv"
_OUT = _ROOT / "data" / "eval" / "pool.jsonl"
_DB = _ROOT / "data" / "lancedb"
_TABLE = "wellfound"
_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_QUERY_PREFIX = "search_query: "
_POOL_DEPTH = 15  # judge the top-15 of each query: deep enough to hold the relevant docs, small enough to grade

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))  # descriptions can be long


def _load_rows() -> dict[str, dict]:
    """id -> full CSV row (title, company, location, description, ...)."""
    with _JOBS_CSV.open(encoding="utf-8") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def _render(row: dict) -> str:
    remote = str(row.get("remote", "")).strip().lower() == "true"
    parts = [
        f"Title: {row['title']}",
        f"Company: {row['company']}",
        f"Location: {row['location']}" + ("  (remote)" if remote else ""),
        f"Employment: {row.get('job_type') or 'n/a'}",
        f"Department: {row.get('department') or 'n/a'}",
    ]
    if row.get("years_experience"):
        parts.append(f"Experience: {row['years_experience']}")
    if row.get("compensation"):
        parts.append(f"Compensation: {row['compensation']}")
    desc = (row.get("description") or "").strip().replace("\r\n", "\n")
    if desc:
        parts.append(
            "Description:\n" + desc
        )  # full description — the judge grades on everything
    return "\n".join(parts)


def main() -> None:
    queries = [json.loads(line) for line in _QRELS.open(encoding="utf-8")]
    rows = _load_rows()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(_MODEL, trust_remote_code=True, device=device)
    if device == "mps":
        model = model.half()
    table = lancedb.connect(_DB).open_table(_TABLE)

    n_pairs = 0
    with _OUT.open("w", encoding="utf-8") as out:
        for q in queries:
            query = q["query"]
            vec = model.encode([_QUERY_PREFIX + query], normalize_embeddings=True)[
                0
            ].astype("float32")
            hits = table.search(vec).metric("cosine").limit(_POOL_DEPTH).to_list()

            seen = set()
            for rank, hit in enumerate(hits, 1):
                jid = hit["id"]
                seen.add(jid)
                if jid not in rows:
                    continue  # indexed id not in the CSV (shouldn't happen) — skip rather than guess
                out.write(
                    json.dumps(
                        {
                            "query": query,
                            "job_id": jid,
                            "rank": rank,
                            "job_text": _render(rows[jid]),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                out.flush()
                n_pairs += 1

            rid = q[
                "relevant_id"
            ]  # always grade the known-relevant job, even if search missed it
            if rid not in seen and rid in rows:
                out.write(
                    json.dumps(
                        {
                            "query": query,
                            "job_id": rid,
                            "rank": None,
                            "job_text": _render(rows[rid]),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                out.flush()
                n_pairs += 1

            print(f"  {query[:50]:50}  {len(hits)} hits", flush=True)

    print(
        f"\npool: {n_pairs} (query, job) pairs -> {_OUT.relative_to(_ROOT)}", flush=True
    )


if __name__ == "__main__":
    main()
