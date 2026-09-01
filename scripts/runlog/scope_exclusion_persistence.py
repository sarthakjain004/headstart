#!/usr/bin/env python3
"""How long each Board has been excluded from the eviction scope — across runs, not within one.

`fanout_merge.py` reports ADR-0053's scope exclusion for **one** run: how many Boards were
unauthoritative, why each was, and (since PR #280) how many eviction-candidate rows the exclusion
withheld. That per-run view cannot answer the question the mechanism actually poses, because
ADR-0053 has no drain: a Board excluded on *every* run never re-enters scope, so its closed
postings are served indefinitely. One run against one run is an anecdote — the two runs that
prompted this file moved in opposite directions (1,276 rows then 813) while the individual Boards
underneath barely moved at all.

**Persistence is the number that matters, not the run total.** Each run scrapes only ~20,000 of
the Scrapable Boards, so the total swings with whichever Boards happened to be in the slice. A
Board's *streak* — how many runs in this window excluded it — survives that, and a streak equal
to the window is the failure mode ADR-0053's Consequences section predicted and left unbounded.

**Read the denominator carefully: it is runs, not scrapes of that Board.** CLAUDE.md is explicit
that the unit for a Board-level claim is a *scrape of that Board*, and this harness cannot supply
that unit — `index sync` logs the scraped-Board **count** (the `corpus:` line) but never the
scraped-Board *list*, so a Board absent from a run's exclusions is unresolvable between two very
different states:

- it **was** scraped, came back authoritative, and re-entered the eviction scope (it drained); or
- it was **not in that run's slice** at all, so nothing happened to it either way.

Both print `-`. Two consequences, and neither is optional when quoting these numbers:

- `k/N` for `k < N` is **not** a drain rate. Do not read a gap as evidence a Board self-drains
  without confirming from the *scrape* stage's logs that the Board was in that run's slice.
- `N/N` ("ALL RUNS") is sound in the direction it is used — a Board can only be excluded in a run
  that scraped it — but the ALL-RUNS *set* is a **lower bound** on the persistently-excluded
  population: a Board excluded on every scrape it got, but scraped in only some of the window's
  runs, scores below `N` and never earns the flag.

Reads the `merge` job's `index sync` stage. Three log lines, three different mechanisms, and this
file reads exactly one of them (CONTEXT.md's glossary is authoritative on why they must not be
conflated):

- `scope-excluded Board: {board} — {why}` — **this** file's subject. ADR-0053, per-Board, emitted
  once per excluded Board with its `truncated`/error reason. The complete list, not a top-N.
- `{n} eviction-candidate row(s) kept out of scope on {board}` — the row cost of the same
  exclusion, but **only the top 10 Boards** (`index._TOP_OUT_OF_SCOPE_BOARDS`), so a Board can be
  excluded with no row line. Absent from runs built before PR #280 (`960d991`, 2026-08-24).
- `Unconfirmed` (ADR-0083) is the *other* withholding mechanism (a third, ADR-0046's collapse
  guard, existed until ADR-0101 removed it and still appears in runs before 2026-09-01). Neither
  is parsed here on purpose; inferring which one fired from an outcome is the exact error
  CLAUDE.md warns against.

`--latest` takes the newest runs whatever they concluded, so a window that must be
**successful** runs only (a cancelled run's merge job never reaches `index sync` and would
shorten the window silently) is pinned by passing the ids explicitly.

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

# Before `8825031` (#160, 2026-08-18) the same Boards were logged as one batched `_log_ids` line
# — a bare space-separated list, no reasons — rather than one `_log_reasons` line each. (That is
# a different commit from `960d991` (#280, 2026-08-24), which left this line alone and added the
# separate row-count line `SCOPE_ROWS`/`SCOPE_ROW_BOARD` read below; the two are easy to conflate
# because both landed inside the windows this harness is usually pointed at.) A window that
# reaches back past #160 reads every older run as *zero exclusions* unless both shapes are
# parsed, and a silent zero is indistinguishable from a genuinely clean run. That is the "confirm
# the runs you read actually carry the line rather than reading its absence as nothing happened"
# trap, so both shapes are handled here and `reasons_known` records which one a run carried.
SCOPE_EXCLUDED_BATCH = re.compile(r"scope-excluded Board \[[^\]]+\]: (.+)")
_NO_REASON = "(batched log format — no reason recorded before #160)"


def _key(board: str) -> str:
    """The Board's canonical identity (ADR-0023), which for a log string is its case-folded key.

    `sync` reaches the reason map through `unauthoritative[b.lower()]`, so the *emitted* spelling
    is whatever the ledger row carried and the same Board can log under more than one casing
    across a window — the "stale casing duplicates" mechanism CLAUDE.md documents. Streaking on
    the raw string would split one Board's run into two short ones and hide exactly the `N/N` this
    harness exists to find. `board_identity` is the real canonicaliser but needs a `CompanyRef`;
    from a log line, case-folding is the part of it that applies.
    """
    return board.lower()


def main() -> None:
    ap = common_args(__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the per-run capture here as well")
    args = ap.parse_args()

    runs = runs_from(args)
    # Oldest first, so a streak reads left-to-right in run order.
    runs.sort(key=lambda r: r.created)

    per_run: list[dict] = []
    # Streaks merge on the case-folded key, but the report prints the spelling the log used, so a
    # Board named here is still greppable in a raw run log. Last spelling seen wins; they differ
    # only by case, which is the point.
    spelling: dict[str, str] = {}

    for run in runs:
        text = ""
        for _shard, _job, log in run.stage(STAGE):
            text += log
        excluded: dict[str, str] = {}
        for board, why in SCOPE_EXCLUDED.findall(text):
            excluded[_key(board)] = why
            spelling[_key(board)] = board
        reasons_known = bool(excluded)
        if not excluded:
            for chunk in SCOPE_EXCLUDED_BATCH.findall(text):
                for board in chunk.split():
                    excluded[_key(board)] = _NO_REASON
                    spelling[_key(board)] = board
        rows_line = SCOPE_ROWS.search(text)
        rows = {_key(b): int(count) for count, b in SCOPE_ROW_BOARD.findall(text)}
        record = {
            "run": run.id,
            "created": run.created,
            "head": run.head,
            # A run whose merge job never reached `index sync` reports nothing; distinguish that
            # from a genuinely clean run, or the window silently shrinks.
            #
            # Keyed on the `corpus:` line, not on `"[index]"`. `[index]` is not a line at all —
            # it is the module tag `log._Formatter` stamps on *every* record the module emits
            # (`headstart/log.py:50-53`), so it is already true of the stage's first line and of
            # `index prune`'s output. A run whose sync died immediately, or that only got as far
            # as prune, would still be counted usable while contributing zero exclusions — the
            # silent zero this flag exists to catch. `corpus:` is emitted once in the whole
            # package (`index.py:409`), inside `sync`, after the per-Board `scope-excluded Board`
            # lines, so its presence proves sync reached and cleared that block. Its wording is
            # unchanged across both log formats.
            "reached_sync": "corpus:" in text,
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
                shielded = rec["rows"].get(board)
                trail[board].append(str(shielded) if shielded is not None else "·")
            else:
                trail[board].append("-")

    window = len(usable)
    print(
        f"\n{'runs':>5}  {'max rows':>8}  Board / row count per run\n"
        "         · = excluded, but outside the top-10 that get a row line\n"
        "         - = NOT excluded this run — either it drained, or the Board was not in this\n"
        "             run's slice; the merge log carries no scraped-Board list to tell them\n"
        "             apart, so a gap is not evidence of a drain (see the module docstring)",
        flush=True,
    )
    for board, count in sorted(streak.items(), key=lambda kv: (-kv[1], kv[0])):
        seen = [r["rows"].get(board) for r in usable if board in r["excluded"]]
        top = max([s for s in seen if s is not None], default=None)
        flag = "  ALL RUNS" if count == window else ""
        print(
            f"{count:>3}/{window}  {top or ''!s:>8}  {spelling[board]}{flag}\n"
            f"         {' '.join(trail[board])}",
            flush=True,
        )

    always = sorted(b for b, c in streak.items() if c == window)
    print(
        f"\nexcluded on all {window} runs: {len(always)} Board(s) — a LOWER BOUND on the "
        "persistently-excluded set, since a Board excluded on every scrape it got but absent "
        "from some run's slice scores below the window and is not listed here",
        flush=True,
    )
    for b in always:
        why, times = reasons[b].most_common(1)[0]
        print(f"  {spelling[b]} — {why[:150]} (x{times})", flush=True)

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
