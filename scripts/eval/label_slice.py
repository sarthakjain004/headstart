"""Hand-label a validation slice — the linchpin that earns the judge your trust (Lesson 6, step 4).

Picks a fixed, **judge-blind** slice of the pool: a handful of rank positions from every query so
the slice spans strong matches (rank 1–2) and weak ones (deep ranks), plus each query's known-
relevant job. It shows you one (query, job) pair at a time — the *same* job_text (full description
included) the judge graded — and records YOUR 0–3 grade against the same rubric. Writes
``data/eval/human_labels.jsonl`` incrementally and **resumably** — quit any time with ``q`` and pick
up where you left off.

These labels are the gold set ``validate_judge.py`` measures the judge against with Cohen's kappa.
Grading blind (you never see the judge's grade here) is what keeps the check honest.

Run: ``.venv/bin/python scripts/eval/label_slice.py``
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_POOL = _ROOT / "data" / "eval" / "pool.jsonl"
_OUT = _ROOT / "data" / "eval" / "human_labels.jsonl"
_SLICE_RANKS = {
    1,
    2,
    4,
    7,
    11,
    15,
}  # 6 depths per query -> ~60 pairs; spans obvious hits to long-shots

_SCALE = """  3 — Perfect: exactly the role the query describes (function, seniority, domain).
  2 — Relevant: right kind of role, one facet off (adjacent domain / seniority).
  1 — Marginal: same broad area but you'd probably skip it.
  0 — Not relevant: different role, function, or field."""


def _slice(pool: list[dict]) -> list[dict]:
    """Deterministic, judge-blind: the chosen rank depths from each query + every unioned relevant job."""
    return [p for p in pool if p["rank"] in _SLICE_RANKS or p["rank"] is None]


def _done_keys() -> set[tuple[str, str]]:
    if not _OUT.exists():
        return set()
    return {
        (r["query"], r["job_id"])
        for r in (json.loads(line) for line in _OUT.open(encoding="utf-8"))
    }


def main() -> None:
    pool = [json.loads(line) for line in _POOL.open(encoding="utf-8")]
    todo = [p for p in _slice(pool) if (p["query"], p["job_id"]) not in _done_keys()]
    total = len(_slice(pool))

    if not todo:
        print(f"All {total} slice pairs already labeled -> {_OUT.relative_to(_ROOT)}")
        print("Next: .venv/bin/python scripts/eval/validate_judge.py")
        return

    print(
        f"Labeling {len(todo)} of {total} slice pairs. Enter 0/1/2/3, s to skip, q to quit.\n"
    )
    with _OUT.open("a", encoding="utf-8") as out:
        for i, p in enumerate(todo, 1):
            print("=" * 78)
            print(f"[{i}/{len(todo)}]  QUERY:  {p['query']}\n")
            print(p["job_text"])
            print("\n" + _SCALE)
            while True:
                ans = input("\n  grade (0/1/2/3, s, q) > ").strip().lower()
                if ans == "q":
                    print(
                        f"\nStopped. {i - 1} labeled this run -> {_OUT.relative_to(_ROOT)} (resumable)."
                    )
                    return
                if ans == "s":
                    break
                if ans in {"0", "1", "2", "3"}:
                    out.write(
                        json.dumps(
                            {
                                "query": p["query"],
                                "job_id": p["job_id"],
                                "grade": int(ans),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    out.flush()
                    break
                print("  ? enter 0, 1, 2, 3, s (skip), or q (quit)")
            print()

    print(f"\nDone -> {_OUT.relative_to(_ROOT)}")
    print("Next: .venv/bin/python scripts/eval/validate_judge.py")


if __name__ == "__main__":
    main()
