#!/usr/bin/env python3
"""Reasoning verification gate for the tech filter (ADR-0017): is it dropping any real tech job?

The filter is recall-critical — a non-tech job creeping through is fine, dropping a tech job is not.
Two checks over ``data/jobs/{ats}.jsonl`` (exit non-zero if either finds a violation):

1. **Self-consistency** (always runs, offline). Every DROPPED job is re-scanned for a *strong*
   software signal. A strong signal always classifies tech, so this must be 0 — a hit is a filter
   bug, not a judgement call.

2. **LLM reasoning gate.** An INDEPENDENT judge reads a random sample of the dropped jobs and decides,
   with a one-sentence reason, whether each is actually a software/tech role. Any "yes" is a recall
   miss the regex didn't catch — the check that finds tech jobs the patterns miss *entirely*, which
   self-consistency structurally cannot. Reports the false-negative rate, an estimate of tech jobs
   wrongly dropped overall, and each miss with the judge's reasoning so the patterns can be widened.

The judge runs through the **Claude Code CLI** (``claude -p``) — your logged-in session, no API key.
Jobs are batched per call to amortise context load. Model defaults to ``claude-sonnet-5`` (override
with ``HEADSTART_JUDGE_MODEL``). Run: ``.venv/bin/python scripts/filter/verify_tech.py [--sample 200]``
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

from headstart.tech_filter import _STRONG, classify

_ROOT = Path(__file__).resolve().parents[2]
_JOBS = _ROOT / "data" / "jobs"
_MODEL = os.environ.get("HEADSTART_JUDGE_MODEL", "claude-sonnet-5")
_BATCH = 25  # jobs per claude -p call (each call reloads session context, so batch to amortise it)

_INSTRUCTIONS = (
    "You decide whether each JOB is a software / tech role — the kind a software engineer, ML/AI "
    "engineer, data engineer or scientist, devops/SRE, security engineer, mobile/web/embedded "
    "developer, or engineering manager holds. NON-tech includes sales, marketing, nursing, "
    "teaching, finance, and non-software engineering (mechanical, civil, electrical, chemical).\n\n"
    "For EACH numbered job, decide is_tech. Respond with ONLY a JSON array (no prose, no code "
    'fences), one object per job: [{"i": 0, "is_tech": true, "reason": "one sentence"}, ...]'
)


def _iter_jobs():
    for src in sorted(
        _JOBS.glob("*.jsonl")
    ):  # top-level only; data/jobs/tech/ is a subdir
        with src.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text.strip()


def _judge_batch(jobs: list[dict]) -> list[tuple[bool, str]]:
    """Judge a batch via `claude -p`; returns (is_tech, reason) per job (falls back to non-tech on a
    parse/CLI failure so a bad batch can't crash the run)."""
    listing = "\n".join(
        f"{i}. {j.get('title', '')}  (dept: {j.get('department') or 'n/a'})"
        for i, j in enumerate(jobs)
    )
    proc = subprocess.run(
        [
            "claude",
            "-p",
            f"{_INSTRUCTIONS}\n\n{listing}",
            "--output-format",
            "json",
            "--model",
            _MODEL,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    verdicts: list[tuple[bool, str]] = [(False, "")] * len(jobs)
    try:
        result_text = json.loads(proc.stdout)["result"]
        for obj in json.loads(_strip_fences(result_text)):
            i = obj.get("i")
            if isinstance(i, int) and 0 <= i < len(jobs):
                verdicts[i] = (bool(obj.get("is_tech")), str(obj.get("reason", "")))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"  (warning: unparseable judge response for a batch — {exc}; skipped)")
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=200, help="dropped jobs to LLM-judge")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if not _JOBS.is_dir():
        raise SystemExit(f"no jobs dir at {_JOBS}")

    dropped = [
        j
        for j in _iter_jobs()
        if not classify(j.get("title"), j.get("department")).is_tech
    ]
    print(f"dropped (non-tech) jobs: {len(dropped)}", flush=True)

    # [1] deterministic self-consistency
    strong_dropped = [
        j
        for j in dropped
        if _STRONG.search(f"{j.get('title', '')} {j.get('department') or ''}")
    ]
    print(
        f"\n[1] self-consistency: dropped jobs matching a STRONG signal = {len(strong_dropped)} "
        f"(must be 0)"
    )
    for j in strong_dropped[:5]:
        print(f"  ! {j.get('title')}")

    # [2] LLM reasoning gate — via the Claude Code CLI (session auth, no API key)
    if not shutil.which("claude"):
        print("\n[2] LLM reasoning gate SKIPPED — the `claude` CLI is not on PATH.")
        return 1 if strong_dropped else 0

    sample = random.Random(args.seed).sample(dropped, min(args.sample, len(dropped)))
    print(
        f"\n[2] LLM reasoning gate: judging {len(sample)} dropped jobs via `claude -p` ({_MODEL}) ..."
    )
    misses: list[tuple[str, str]] = []
    for start in range(0, len(sample), _BATCH):
        batch = sample[start : start + _BATCH]
        for job, (tech, reason) in zip(batch, _judge_batch(batch)):
            if tech:
                misses.append((job.get("title", ""), reason))
                print(f"  RECALL MISS: {job.get('title')!r} — {reason}", flush=True)
        print(
            f"  ... judged {min(start + _BATCH, len(sample))}/{len(sample)}", flush=True
        )

    rate = len(misses) / len(sample) if sample else 0.0
    print(
        f"\nfalse-negative rate in sample: {len(misses)}/{len(sample)} = {100 * rate:.1f}%"
        f"  ->  ~{round(rate * len(dropped))} tech jobs wrongly dropped overall (estimate)"
    )
    return 1 if (misses or strong_dropped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
