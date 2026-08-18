#!/usr/bin/env python3
"""Measure index flapping (#142) from the merge logs of consecutive pipeline runs.

The merge stage logs every id behind a table change with a distinct label — ``add`` / ``evict``
(sync), ``prune off-Board`` / ``prune duplicate`` (prune) — so consecutive runs' logs are enough
to (a) measure the re-add rate the issue reports and (b) say *which eviction path* took each
flapped id out, which is the discriminator between candidate causes.

Reads the last ``--runs`` completed ``nightly-pipeline`` runs via ``gh``, caches each merge job's
log under ``experiment/index-flapping/artifacts/`` (gitignored), and prints:

  - per consecutive run pair: |evict(N) ∩ add(N+1)| / |evict(N)| — the next-run re-add rate
  - the window rate: evictions from any run re-added by any *later* run in the window
  - flapped ids classified by eviction label (sync evict vs prune off-Board vs prune duplicate)
  - the worst-flapping Boards, with per-run add/evict counts
  - "already-known adds": ids added by run N that some earlier run in the window added or evicted

Verdict: the goal metric is the **overall already-known-adds rate** — adds the window had already
seen, over all adds after the first run (which has no history to be known to). RED above 10%,
GREEN below (#142's acceptance bar).

Run:
  .venv/bin/python -u scripts/eval/flap_audit.py --runs 8
Exit: 0 GREEN, 1 RED, 2 when fewer than two runs could be harvested.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _ROOT / "experiment" / "index-flapping" / "artifacts"

# `_log_ids` lines: ``HH:MM:SS [index] LABEL [i-j of N]: id id id ...``. Anchored on the logger
# prefix so the workflow's own `|| echo` text can never match (the command-echo trap). Known
# noise: the ids are space-joined, and a rare workday native id itself contains spaces (ADR-0049),
# so those few split into junk fragments — visible as nonsense "ATSes", too small to move rates.
_IDS_LINE = re.compile(
    r"\[index\] (add|evict|prune off-Board|prune duplicate) \[\d+-\d+ of \d+\]: (.*)$"
)
_LABELS = ("add", "evict", "prune off-Board", "prune duplicate")
_RED_THRESHOLD = 0.10  # overall already-known-adds rate above this is RED


def _gh(args: list[str]) -> str:
    """Run ``gh`` with one retry — api.github.com is flaky from this machine."""
    for attempt in (1, 2):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=_ROOT)  # noqa: PLW1510
        if proc.returncode == 0:
            return proc.stdout
        if attempt == 1:
            time.sleep(3)
    raise RuntimeError(f"gh {' '.join(args[:3])}… failed: {proc.stderr.strip()}")


def _recent_runs(n: int) -> list[tuple[str, str]]:
    """Oldest-first ``(run_id, created_at)`` for the last n successful pipeline runs."""
    out = _gh(
        [
            "run",
            "list",
            "--workflow=nightly-pipeline",
            f"--limit={n}",
            "--status=success",
            "--json",
            "databaseId,createdAt",
            "--jq",
            '.[] | "\\(.databaseId) \\(.createdAt)"',
        ]
    )
    rows = [line.split() for line in out.splitlines() if line.strip()]
    return sorted(((rid, ts) for rid, ts in rows), key=lambda r: r[1])


def _merge_log(run_id: str) -> Path:
    path = _CACHE / f"merge-{run_id}.log"
    if path.exists():
        return path
    jobs = _gh(
        [
            "api",
            f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs?per_page=100",
            "--jq",
            '.jobs[] | select(.name == "merge") | .id',
        ]
    ).strip()
    if not jobs:
        raise RuntimeError(f"run {run_id}: no merge job found")
    log = _gh(["api", f"repos/{{owner}}/{{repo}}/actions/jobs/{jobs}/logs"])
    _CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(log, encoding="utf-8")
    return path


def _parse(path: Path) -> dict[str, set[str]]:
    sets: dict[str, set[str]] = {label: set() for label in _LABELS}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _IDS_LINE.search(line)
        if m and m.group(1) in sets:
            sets[m.group(1)].update(m.group(2).split())
    return sets


def _board(job_id: str) -> str:
    """Board guess for grouping stats — the ``{ats}:{slug}`` prefix (see ``corpus.board_of``;
    self-comparing use only, so the guess pairs with itself)."""
    return job_id.rsplit(":", 1)[0]


def _out(sets: dict[str, set[str]]) -> set[str]:
    """Every id a run took out of the table, across all three eviction paths."""
    return sets["evict"] | sets["prune off-Board"] | sets["prune duplicate"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=8, help="window size (default 8)")
    ap.add_argument("--boards", type=int, default=12, help="worst Boards to show")
    args = ap.parse_args()

    runs: list[tuple[str, str, dict[str, set[str]]]] = []
    for run_id, ts in _recent_runs(args.runs):
        try:
            sets = _parse(_merge_log(run_id))
        except RuntimeError as exc:
            print(f"skip {run_id}: {exc}", flush=True)
            continue
        evicted = _out(sets)
        print(
            f"run {run_id} {ts}: add {len(sets['add'])}, evict {len(sets['evict'])} "
            f"(+{len(sets['prune off-Board'])} off-Board, "
            f"+{len(sets['prune duplicate'])} dup) = {len(evicted)} out",
            flush=True,
        )
        runs.append((run_id, ts, sets))
    if len(runs) < 2:
        print("fewer than two runs harvested — cannot measure", flush=True)
        return 2

    # Next-run and window re-add rates, over every eviction path combined.
    print("\n== re-add rates ==", flush=True)
    window_evicted: set[str] = set()
    window_readded: set[str] = set()
    label_of: dict[str, str] = {}  # flapped id -> the label that evicted it (last wins)
    for i in range(len(runs) - 1):
        _, ts, sets = runs[i]
        out = _out(sets)
        next_add = runs[i + 1][2]["add"]
        later_add = set().union(*(r[2]["add"] for r in runs[i + 1 :]))
        readded = out & later_add
        for label in ("evict", "prune off-Board", "prune duplicate"):
            for jid in sets[label] & readded:
                label_of[jid] = label
        rate = len(out & next_add) / len(out) if out else 0.0
        wrate = len(readded) / len(out) if out else 0.0
        print(
            f"  {ts}: evicted {len(out)}, re-added next run {len(out & next_add)} "
            f"({rate:.0%}), within window {len(readded)} ({wrate:.0%})",
            flush=True,
        )
        window_evicted |= out
        window_readded |= readded

    overall = len(window_readded) / len(window_evicted) if window_evicted else 0.0
    print(
        f"\noverall: {len(window_evicted)} evicted, {len(window_readded)} re-added "
        f"({overall:.0%})",
        flush=True,
    )
    by_label = Counter(label_of.values())
    print(f"flapped ids by eviction path: {dict(by_label)}", flush=True)

    # Adds that were already known to the window — the issue's micron symptom and the goal
    # metric. The first run seeds `seen` but is excluded from the totals: nothing can be
    # "already known" before there is a window to know it.
    print("\n== already-known adds ==", flush=True)
    seen: set[str] = set()
    known_total = adds_total = 0
    for i, (run_id, ts, sets) in enumerate(runs):
        known = sets["add"] & seen
        if i and sets["add"]:
            known_total += len(known)
            adds_total += len(sets["add"])
            print(
                f"  {ts}: {len(known)}/{len(sets['add'])} adds already known "
                f"({len(known) / len(sets['add']):.0%})",
                flush=True,
            )
        seen |= sets["add"] | _out(sets)
    known_rate = known_total / adds_total if adds_total else 0.0
    print(
        f"overall: {known_total}/{adds_total} adds already known ({known_rate:.0%})",
        flush=True,
    )

    # Worst boards by flap volume.
    print(f"\n== worst {args.boards} Boards by flapped rows ==", flush=True)
    flap_by_board = Counter(_board(jid) for jid in window_readded)
    for board, n in flap_by_board.most_common(args.boards):
        labels = Counter(
            label_of[jid] for jid in window_readded if _board(jid) == board
        )
        per_run = " ".join(
            f"+{sum(_board(i) == board for i in r[2]['add'])}"
            f"/-{sum(_board(i) == board for i in _out(r[2]))}"
            for r in runs
        )
        print(f"  {board}: {n} flapped {dict(labels)} | per-run {per_run}", flush=True)

    verdict = "RED" if known_rate > _RED_THRESHOLD else "GREEN"
    print(
        f"\nVERDICT: {verdict} (already-known adds {known_rate:.0%}, "
        f"threshold {_RED_THRESHOLD:.0%}; window re-add rate {overall:.0%})",
        flush=True,
    )
    return 1 if verdict == "RED" else 0


if __name__ == "__main__":
    sys.exit(main())
