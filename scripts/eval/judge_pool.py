"""LLM-judge the candidate pool — grade every (query, job) pair 0–3 (Lesson 6, step 3).

Reads ``data/eval/pool.jsonl`` and asks Claude to grade how well each job answers each query on the
TREC 0–3 scale — reason first, then a grade forced through a tool schema so the output is always a
clean integer, never parsed from prose. Writes ``data/eval/judge_labels.jsonl`` incrementally and
**resumably**: already-judged (query, job_id) pairs are skipped, so a rate-limit stop or crash loses
nothing (repo streaming rule).

You do not trust these grades until ``validate_judge.py`` shows the judge agrees with your own hand
labels (Cohen's kappa ≥ ~0.61). This script only produces the judge's opinion; step 4 earns the
trust.

Needs ``ANTHROPIC_API_KEY`` in the environment. Model defaults to Claude Sonnet 5 (a strong, cheap
judge); override with ``HEADSTART_JUDGE_MODEL`` (e.g. ``claude-haiku-4-5-20251001`` for less cost,
``claude-opus-4-8`` for max quality). Run: ``.venv/bin/python scripts/eval/judge_pool.py``
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic

_ROOT = Path(__file__).resolve().parents[2]
_POOL = _ROOT / "data" / "eval" / "pool.jsonl"
_OUT = _ROOT / "data" / "eval" / "judge_labels.jsonl"
_MODEL = os.environ.get("HEADSTART_JUDGE_MODEL", "claude-sonnet-5")

# The rubric IS the instrument. Human and judge grade against these exact words so the two are
# comparable — validate_judge.py's kappa only means something if both raters used the same scale.
_RUBRIC = """You are a careful hiring-search relevance judge. You are given a candidate's SEARCH QUERY and one JOB posting. Grade how well the job answers the query on this scale:

3 — Perfect: exactly the role the query describes (right function, seniority, and domain).
2 — Relevant: clearly the right kind of role a searcher would want to see, though one facet is off (e.g. adjacent domain, seniority slightly high/low).
1 — Marginal: same broad area but a searcher would probably skip it (e.g. neighbouring function or a different specialization).
0 — Not relevant: a different role, function, or field.

The query describes ONLY the role. Ignore structured constraints (years of experience, salary, remote) unless the query itself states them. Reason in one sentence, then record the grade."""

_TOOL = {
    "name": "record_grade",
    "description": "Record the 0–3 relevance grade for this (query, job) pair.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One sentence justifying the grade.",
            },
            "grade": {"type": "integer", "enum": [0, 1, 2, 3]},
        },
        "required": ["reason", "grade"],
    },
}


def _done_keys() -> set[tuple[str, str]]:
    if not _OUT.exists():
        return set()
    return {
        (r["query"], r["job_id"])
        for r in (json.loads(line) for line in _OUT.open(encoding="utf-8"))
    }


def _grade(client: Anthropic, query: str, job_text: str) -> tuple[int, str]:
    msg = client.messages.create(
        model=_MODEL,
        max_tokens=400,
        system=_RUBRIC,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_grade"},
        messages=[
            {"role": "user", "content": f"SEARCH QUERY:\n{query}\n\nJOB:\n{job_text}"}
        ],
    )
    block = next(b for b in msg.content if b.type == "tool_use")
    return int(block.input["grade"]), block.input["reason"]


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is not set. Export it, then re-run (see the Lesson 6 card)."
        )

    pool = [json.loads(line) for line in _POOL.open(encoding="utf-8")]
    done = _done_keys()
    todo = [p for p in pool if (p["query"], p["job_id"]) not in done]
    client = Anthropic(
        max_retries=4
    )  # SDK backs off on rate limits / transient errors itself

    print(
        f"judging {len(todo)} pairs with {_MODEL}  ({len(done)} already done)\n",
        flush=True,
    )
    with _OUT.open("a", encoding="utf-8") as out:
        for i, p in enumerate(todo, 1):
            grade, reason = _grade(client, p["query"], p["job_text"])
            out.write(
                json.dumps(
                    {
                        "query": p["query"],
                        "job_id": p["job_id"],
                        "grade": grade,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out.flush()
            print(
                f"  [{i}/{len(todo)}] grade {grade}  {p['query'][:38]:38}  {p['job_id']}",
                flush=True,
            )

    print(f"\nwrote grades -> {_OUT.relative_to(_ROOT)}", flush=True)


if __name__ == "__main__":
    main()
