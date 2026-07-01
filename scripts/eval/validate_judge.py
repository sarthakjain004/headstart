"""Validate the judge against your hand labels with Cohen's kappa (Lesson 6, step 4 — the linchpin).

Loads your gold labels (``human_labels.jsonl``) and the judge's grades (``judge_labels.jsonl``),
lines them up on the (query, job_id) pairs *both* graded, and reports agreement:

  * exact-match rate — how often the grades are identical;
  * Cohen's kappa (unweighted) — agreement corrected for chance, treating grades as unordered;
  * Cohen's kappa (quadratic-weighted) — the right one for an ordinal 0–3 scale, since it penalizes
    a 3-vs-0 disagreement far more than a 3-vs-2 near-miss.

Read the weighted kappa: ≥ 0.61 is "substantial" — the bar from Lesson 5 to trust the judge to
grade the rest of the pool at scale. Below it, fix the rubric (``judge_pool.py``) and re-judge.

Run (after both label files exist): ``.venv/bin/python scripts/eval/validate_judge.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sklearn.metrics import cohen_kappa_score

_ROOT = Path(__file__).resolve().parents[2]
_HUMAN = _ROOT / "data" / "eval" / "human_labels.jsonl"
_JUDGE = _ROOT / "data" / "eval" / "judge_labels.jsonl"


def _load(path: Path) -> dict[tuple[str, str], int]:
    if not path.exists():
        sys.exit(
            f"missing {path.relative_to(_ROOT)} — run the step that produces it first."
        )
    return {
        (r["query"], r["job_id"]): int(r["grade"])
        for r in (json.loads(line) for line in path.open(encoding="utf-8"))
    }


def _band(k: float) -> str:
    for lo, label in (
        (0.81, "almost perfect"),
        (0.61, "substantial"),
        (0.41, "moderate"),
        (0.21, "fair"),
        (0.0, "slight"),
        (-1.0, "poor / worse than chance"),
    ):
        if k >= lo:
            return label
    return "poor"


def main() -> None:
    human, judge = _load(_HUMAN), _load(_JUDGE)
    keys = sorted(human.keys() & judge.keys())
    if not keys:
        sys.exit(
            "no overlapping (query, job) pairs — the judge and your labels cover different pairs."
        )

    h = [human[k] for k in keys]
    j = [judge[k] for k in keys]
    exact = sum(a == b for a, b in zip(h, j)) / len(keys)
    kappa = cohen_kappa_score(h, j)
    kappa_w = cohen_kappa_score(h, j, weights="quadratic")

    print(f"validated on {len(keys)} pairs both graded\n")
    print(f"  exact agreement       = {exact:.3f}")
    print(f"  Cohen's kappa         = {kappa:.3f}   ({_band(kappa)})")
    print(
        f"  quadratic-weighted κ  = {kappa_w:.3f}   ({_band(kappa_w)})   <- read this one (0–3 is ordinal)"
    )
    verdict = (
        "TRUST the judge to grade the rest of the pool at scale."
        if kappa_w >= 0.61
        else "DO NOT trust yet — tighten the rubric in judge_pool.py and re-judge."
    )
    print(f"\n  verdict: {verdict}")


if __name__ == "__main__":
    main()
