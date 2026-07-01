# ADR-0011: Retrieval-eval harness — a validated LLM judge and graded nDCG

- Status: Accepted
- Date: 2026-07-01

## Context

The semantic search (ADR-0005, ADR-0008) shipped with no way to measure ranking quality. Two things
made that hard: hand-judging relevance for 6,360 jobs per query is infeasible, and a naive
single-relevant test set (one known-good job per query) *understates* quality whenever a query has
many valid answers — which most do.

## Decision

A five-stage harness in `scripts/eval/`, with ground truth in `data/eval/`:

1. **Pool** (`pool_candidates.py`) — judge only the union of the search's top-15 per query plus each
   query's known-relevant seed (classic TREC pooling), not the whole corpus.
2. **Judge** (`judge_pool.py`) — an LLM (Claude) grades each (query, job) pair `0–3` via a
   forced-tool-call schema so output is always a clean integer. Runs against the API, or the pool can
   be graded interactively (no key) to bill a Max plan instead.
3. **Human gold** (`label_slice.py`) — hand-label a fixed, judge-blind 62-pair slice.
4. **Validate** (`validate_judge.py`) — Cohen's kappa, **quadratic-weighted** (the grade scale is
   ordinal), judge vs human. ≥ 0.61 "substantial" is the bar to trust the judge; we measured 0.64.
5. **Score** (`score_graded.py`) — graded qrels + the run → `ranx` nDCG@10 = 0.90 (and 0.89 with the
   human labels overlaid, a robustness check).

## Rejected alternatives

- **Single-relevant synthetic qrels** (the first-pass harness, `eval_search.py`) — structurally
  understates search when a query has many valid answers; kept only as a first smoke number, not the
  headline.
- **Trust the LLM judge unvalidated** — an LLM judge caps measured quality at its own level and runs
  lenient; the kappa gate against human labels is the whole point, not an optional extra.
- **Hand-judge the full corpus** — infeasible at 6,360 × queries.

## Consequences

A reproducible, per-query nDCG@10 that works as a regression/diagnostic tool — it flags the weak
(generic) queries the search orders badly. Honest limit: this is a **single-system pool** (only this
search's own top-15 were judged), so nDCG is self-referential and optimistic — it measures how well
the search orders its own picks, not corpus-wide recall. Pooling a second system (e.g. BM25) would
de-bias it; a natural next step. `pool.jsonl` is large and regenerable, so it is gitignored; the
small qrels + labels are versioned. Adds an `eval` dependency group (anthropic, ranx, scikit-learn).
