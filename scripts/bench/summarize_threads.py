#!/usr/bin/env python3
"""Summarise an `embed-threads` sweep: median tok/s per thread count (ADR-0029).

Reads the raw log the workflow tees, pairs each `=== pass=P threads=T ===` marker with the
`[bench] torch-fp32 ... N tok/s` line that follows it, and reports the **median** per thread
count plus the observed spread.

Median, not mean: a shared CI runner occasionally gives one pass a noisy-neighbour stall, and a
mean lets that single outlier decide the winner. The spread column is there so a result whose
passes disagree more than the variants do is visibly untrustworthy rather than quietly reported.

Run: python scripts/bench/summarize_threads.py <log file>
"""

from __future__ import annotations

import re
import statistics
import sys
from pathlib import Path

_MARKER = re.compile(r"=== pass=(\d+) threads=(\d+) ===")
_RESULT = re.compile(r"\[bench\]\s+torch-fp32\s+[\d.]+s\s+(\d+)\s+tok/s")


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: summarize_threads.py <log file>")
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")

    by_threads: dict[int, list[int]] = {}
    current: int | None = None
    for line in text.splitlines():
        m = _MARKER.search(line)
        if m:
            current = int(m.group(2))
            continue
        r = _RESULT.search(line)
        if r and current is not None:
            by_threads.setdefault(current, []).append(int(r.group(1)))
            current = None

    if not by_threads:
        print("no measurements parsed — did every pass fail?", flush=True)
        return 1

    baseline = by_threads.get(0) or by_threads.get(
        2
    )  # torch default is 2 on this runner
    base = statistics.median(baseline) if baseline else None

    print(
        f"\n{'threads':>8}{'passes':>8}{'median tok/s':>14}{'spread':>10}{'vs base':>10}"
    )
    for t in sorted(by_threads):
        vals = by_threads[t]
        med = statistics.median(vals)
        spread = (max(vals) - min(vals)) / med if med else 0.0
        rel = f"{med / base:.2f}x" if base else "-"
        print(f"{t:>8}{len(vals):>8}{med:>14.0f}{spread:>9.1%}{rel:>10}")

    worst = max((max(v) - min(v)) / statistics.median(v) for v in by_threads.values())
    spans = [statistics.median(v) for v in by_threads.values()]
    between = (max(spans) - min(spans)) / min(spans) if min(spans) else 0.0
    print(
        f"\nwithin-variant spread up to {worst:.1%}; between-variant difference {between:.1%}."
    )
    if worst >= between:
        print(
            "Run-to-run noise is at least as large as the effect — this sweep does NOT "
            "distinguish the thread counts. Raise --repeats or --docs before concluding.",
            flush=True,
        )
    else:
        print("The effect exceeds run-to-run noise.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
