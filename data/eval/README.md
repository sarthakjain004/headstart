# Eval data — retrieval-eval harness ground truth

Ground truth and labels for scoring the semantic search. See `scripts/eval/` and
[ADR-0011](../../docs/adr/0011-retrieval-eval-harness.md). Run everything with `.venv/bin/python`.

## Pipeline

```
pool_candidates.py  → pool.jsonl          candidate (query, job) pairs: top-15 per query + seeds
judge_pool.py       → judge_labels.jsonl  LLM-judge 0–3 grades over the pool (or graded interactively)
label_slice.py      → human_labels.jsonl  62 hand-labeled pairs, judge-blind (the gold set)
validate_judge.py   → Cohen's kappa       judge vs human — weighted κ = 0.64 (substantial)
score_graded.py     → nDCG@10 = 0.90      ranx, graded qrels
```

Run order: `pool` → (`judge`, `label`) → `validate` → `score`.

## Files

- `qrels.jsonl` — 10 seed `(query, known-relevant job)` pairs. The search test set.
- `human_labels.jsonl` — your gold labels: `{query, job_id, grade}`.
- `judge_labels.jsonl` — the judge's grades over the whole pool: `{query, job_id, grade, reason}`.
- `pool.jsonl` — **not committed** (large, full job descriptions); regenerate with
  `pool_candidates.py`. Everything else is reproducible from it plus the labels.
