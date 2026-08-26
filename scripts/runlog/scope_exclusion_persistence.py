#!/usr/bin/env python3
"""How long each Board has been held out of the eviction scope — across runs, not within one.

`fanout_merge.py` reports ADR-0053's scope exclusion for **one** run: how many Boards were
unauthoritative, why each was, and (since PR #280) how many eviction-candidate rows the exclusion
withheld. That per-run view cannot answer the question the mechanism actually poses, because
ADR-0053 has no drain: a Board excluded on *every* run never re-enters scope, so its closed
postings are served indefinitely. One run against one run is an anecdote — the two runs that
prompted this file moved in opposite directions (1,276 rows then 813) while the individual Boards
underneath barely moved at all.

**Persistence is the number that matters, not the run total.** Each run scrapes only ~20,000 of
~66,000 live Boards, so the total swings with whichever Boards happened to be in the slice. A
Board's *streak* — how many of its own appearances in this window were exclusions — is
slice-independent, and a streak equal to the window is the failure mode ADR-0053's Consequences
section predicted and left unbounded.

Reads the `merge` job's `index sync` stage. Three log lines, three different mechanisms, and this
file reads exactly one of them (CONTEXT.md's glossary is authoritative on why they must not be
conflated):

- `scope-excluded Board: {board} — {why}` — **this** file's subject. ADR-0053, per-Board, emitted
  once per excluded Board with its `truncated`/error reason. The complete list, not a top-N.
- `{n} eviction-candidate row(s) kept out of scope on {board}` — the row cost of the same
  exclusion, but **only the top 10 Boards** (`index._TOP_OUT_OF_SCOPE_BOARDS`), so a Board can be
  excluded with no row line. Absent from runs built before PR #280 (960d991).
- `collapse guard: withheld ...` (ADR-0046 `held`) and `Unconfirmed` (ADR-0083) are the *other*
  two withholding mechanisms. Neither is parsed here on purpose; inferring which one fired from an
  outcome is the exact error CLAUDE.md warns against.

Run: python scripts/runlog/scope_exclusion_persistence.py --latest pipeline.yml -n 14
     python scripts/runlog/scope_exclusion_persistence.py 32942748996 32936269675
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from fanout_merge import SCOPE_EXCLUDED, SCOPE_ROW_BOARD, SCOPE_ROWS
from run_logs import common_args, runs_from

# The `index sync` stage lives in the `merge` job's log.
STAGE = "merge"

# Before PR #280 the same Boards were logged as one batched `_log_ids` line — a bare
# space-separated list, no reasons — rather than one line each. A window that reaches back past
# that commit reads every older run as *zero exclusions* unless both shapes are parsed, and a
# silent zero is indistinguishable from a genuinely clean run. That is the "confirm the runs you
# read actually carry the line rather than reading its absence as nothing happened" trap, so both
# shapes are handled here and `reasons_known` records which one a run carried.
SCOPE_EXCLUDED_BATCH = re.compile(r"scope-excluded Board \[[^\]]+\]: (.+)")
_NO_REASON = "(batched log format — no reason recorded before PR #280)"


def main() -> None:
    ap = common_args(__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the per-run capture here as well")
    args = ap.parse_args()

    runs = runs_from(args)
    # Oldest first, so a streak reads left-to-right in run order.
    runs.sort(key=lambda r: r.created)

    per_run: list[dict] = []
    for run in runs:
        text = ""
        for _shard, _job, log in run.stage(STAGE):
            text += log
        excluded = dict(SCOPE_EXCLUDED.findall(text))
        reasons_known = bool(excluded)
        if not excluded:
            for chunk in SCOPE_EXCLUDED_BATCH.findall(text):
                for board in chunk.split():
                    excluded[board] = _NO_REASON
        rows_line = SCOPE_ROWS.search(text)
        rows = {b: int(n) for n, b in SCOPE_ROW_BOARD.findall(text)}
        record = {
            "run": run.id,
            "created": run.created,
            "head": run.head,
            # A run whose merge job never reached `index sync` reports nothing; distinguish that
            # from a genuinely clean run, or the window silently shrinks.
            "reached_sync": "[index]" in text,
            "has_row_line": bool(rows_line),
            "reasons_known": reasons_known,
            "total_rows": int(rows_line.group(1)) if rows_line else None,
            "total_boards": int(rows_line.group(2)) if rows_line else len(excluded),
            "excluded": excluded,
            "rows": rows,
        }
        per_run.append(record)
        print(
            f"run {run.id} {record['created']} head={run.head} "
            f"boards={record['total_boards']} rows={record['total_rows']} "
            f"{'' if record['reached_sync'] else '(NO index sync in log)'}",
            flush=True,
        )

    usable = [r for r in per_run if r["reached_sync"]]
    print(f"\n{len(usable)} of {len(per_run)} runs reached `index sync`", flush=True)

    # --- persistence ---------------------------------------------------------------------
    streak: Counter[str] = Counter()
    trail: dict[str, list[str]] = defaultdict(list)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in usable:
        for board, why in rec["excluded"].items():
            streak[board] += 1
            reasons[board][why] += 1
    for board in streak:
        for rec in usable:
            if board in rec["excluded"]:
                n = rec["rows"].get(board)
                trail[board].append(str(n) if n is not None else "·")
            else:
                trail[board].append("-")

    n = len(usable)
    print(
        f"\n{'runs':>5}  {'max rows':>8}  Board / row count per run (· = outside top-10)"
    )
    for board, count in sorted(streak.items(), key=lambda kv: (-kv[1], kv[0])):
        seen = [r["rows"].get(board) for r in usable if board in r["excluded"]]
        top = max([s for s in seen if s is not None], default=None)
        flag = "  ALL RUNS" if count == n else ""
        print(
            f"{count:>3}/{n}  {top or ''!s:>8}  {board}{flag}\n"
            f"         {' '.join(trail[board])}",
            flush=True,
        )

    always = sorted(b for b, c in streak.items() if c == n)
    print(f"\nexcluded on all {n} runs: {len(always)} Board(s)")
    for b in always:
        why, times = reasons[b].most_common(1)[0]
        print(f"  {b} — {why[:150]} (x{times})", flush=True)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(
                {"runs": per_run, "streak": dict(streak), "trail": dict(trail)},
                fh,
                indent=1,
            )
        print(f"\nwrote {args.json}", flush=True)


if __name__ == "__main__":
    main()
