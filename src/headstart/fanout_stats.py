"""Did widening the fan-out buy throughput, or only queue? — the per-width record that answers it.

`spare_egress.stream_width`'s own docstring flags `_WALLED_STREAM_WIDTH = 12` as untuned: ADR-0081
removed the "one to three IPs per colo" measurement that justified it "without supplying a
re-measured width to replace `12`". Nothing in the shard log could settle it either — a shard
reported how long its Boards took but never *at what width*, so the two operating points it
already runs at were never comparable.

It does run at two, on purpose. `stream_width` clamps a walled group to 12 and leaves every other
group at its resolved ceiling, so one shard fans out at both widths within a single run. Recording
each batch against the width in force therefore yields a two-point throughput curve for free — no
experiment, no flag, no extra traffic.

**The unit is one fan-out batch, not the shard.** Many Boards fan out concurrently, so summing
batch walls overshoots the shard's own wall by design; `req/s` here is the throughput *of a fan-out*
at that width, which is the thing a width decides. Comparing it across the widths of one fan-out is
the whole point, and that comparison is unaffected.

**One ATS's detail passes merge into one row.** Eightfold calls `fan_out_async` from two places in
`eightfold.py` and both land in `eightfold details`. Accepted rather than overlooked: both resolve
their width from the same clamp, so the row stays honest about `(ATS, width)`, and separating them
would thread a label through a shared helper for a distinction no width decision turns on
(ADR-0110).

Read it as Little's Law. `streams` is the mean occupancy actually achieved (item-seconds ÷ batch
wall): near the ceiling every stream is busy; far below it the width is not the binding constraint.
Then, for one fan-out:

- throughput flat while latency scales with the width -> past the knee, narrow it.
- throughput scaling with the width -> below the knee, widen it.
- `streams` far below its width -> the width is not what limits this fan-out; look elsewhere.

Measured 2026-09-05 against one Workday CXS host through the tunnel — the shape this exists to
surface — width 12 served 100 pages in 44.7s (p50 4.68s) and width 25 served 100 in 45.8s (p50
10.30s): **2.1x the streams bought 0.98x the throughput at 2.2x the latency**. Driving the real
paginate through this module a second time agreed, at 0.88x. Both runs say the wider fan-out is
slower, which is the answer `_WALLED_STREAM_WIDTH` never had — but two probes of one host on one
day are a reason to instrument every shard, not to move the constant.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator

__all__ = ["batch", "report", "reset", "stats"]

#: Keyed (fanout, width). `busy` is item-seconds, `wall` is batch-seconds; their ratio is the mean
#: number of streams actually occupied, which is what separates "saturated" from "not the
#: constraint" and cannot be recovered from a count of requests alone.
_rows: dict[tuple[str, int], dict[str, float]] = {}
_lock = threading.Lock()

#: Below this many items a row's rates are noise — a handful of Boards with three postings each
#: would otherwise produce a confident-looking verdict. Only the comparison is suppressed; the
#: measured row is still printed, so a small sample is visible rather than silently dropped.
_MIN_ITEMS = 50


@contextlib.contextmanager
def batch(fanout: str, width: int) -> Iterator[Callable[[float], None]]:
    """Time one fan-out batch at ``width``, yielding the per-item timer its workers call.

    The yielded callback takes one item's own duration. It is called from the gather's event-loop
    thread, one at a time, so it needs no lock of its own; the module-level totals do, because
    every worker thread in the shard fans out into them.

    The batch is recorded in a ``finally``: a gather that raises (Workday re-raises past
    `_MAX_LOST_PAGE_SHARE`, ADR-0076) still spent the time and still says something about the
    width, and losing exactly the struggling batches would bias the record toward the healthy ones.
    """
    started = time.monotonic()
    totals = {"items": 0.0, "busy": 0.0}

    def done(seconds: float) -> None:
        totals["items"] += 1
        totals["busy"] += seconds

    try:
        yield done
    finally:
        wall = time.monotonic() - started
        with _lock:
            row = _rows.setdefault(
                (fanout, width),
                {"batches": 0.0, "items": 0.0, "busy": 0.0, "wall": 0.0},
            )
            row["batches"] += 1
            row["items"] += totals["items"]
            row["busy"] += totals["busy"]
            row["wall"] += wall


def stats() -> dict[tuple[str, int], dict[str, float]]:
    """A snapshot of the raw totals, for tests and for the shard report."""
    with _lock:
        return {key: dict(row) for key, row in _rows.items()}


def reset() -> None:
    """Zero the totals — a stage calls this once so its numbers describe its own work."""
    with _lock:
        _rows.clear()


def report() -> list[str]:
    """One line per (fan-out, width), plus the comparison where one ran at more than one width.

    Empty when nothing fanned out, so a shard of single-request Boards stays silent rather than
    printing a table of zeroes.
    """
    rows = stats()
    if not rows:
        return []
    lines: list[str] = []
    for fanout in sorted({fanout for fanout, _ in rows}):
        widths = sorted((w for name, w in rows if name == fanout), reverse=True)
        for width in widths:
            row = rows[(fanout, width)]
            items, wall, busy = row["items"], row["wall"], row["busy"]
            rate = items / wall if wall else 0.0
            streams = busy / wall if wall else 0.0
            mean = busy / items if items else 0.0
            batches = int(row["batches"])
            lines.append(
                f"concurrency {fanout} @{width}: {int(items):,} req over "
                f"{wall:,.0f} batch-s, {rate:.2f} req/s, streams {streams:.1f}/{width}, "
                f"mean {mean:.1f}s ({batches:,} batch{'' if batches == 1 else 'es'})"
            )
        lines.extend(_compare(fanout, widths, rows))
    return lines


def _compare(
    fanout: str, widths: list[int], rows: dict[tuple[str, int], dict[str, float]]
) -> list[str]:
    """The verdict line: what the extra streams actually bought, between a fan-out's widest
    and narrowest width.

    Deliberately only ever compares two rows and only when both carry :data:`_MIN_ITEMS`. The
    widths are not an experiment — a group is clamped because it *walled*, so the wide and narrow
    samples differ in more than their width and the ratio is a signal to read, not a controlled
    result. The line says what was measured and names the direction; it does not claim a cause.
    """
    if len(widths) < 2:
        return []
    wide, narrow = rows[(fanout, widths[0])], rows[(fanout, widths[-1])]
    if wide["items"] < _MIN_ITEMS or narrow["items"] < _MIN_ITEMS:
        return []
    if not (wide["wall"] and narrow["wall"] and wide["items"] and narrow["items"]):
        return []
    rate_wide = wide["items"] / wide["wall"]
    rate_narrow = narrow["items"] / narrow["wall"]
    if not rate_narrow:
        return []
    width_x = widths[0] / widths[-1]
    rate_x = rate_wide / rate_narrow
    lat_x = (wide["busy"] / wide["items"]) / (narrow["busy"] / narrow["items"])
    # The cut-points are **chosen, not derived** — 1.05x and 1.15x are round numbers either side
    # of "no change", picked to make the line legible. They could not be derived: the only
    # measurements in hand are two probes of one host on one day (0.98x and 0.88x), which ADR-0110
    # says are a reason to instrument every shard rather than to move a constant, and the same
    # evidence cannot be too thin to move `_WALLED_STREAM_WIDTH` and thick enough to calibrate the
    # line recommending it. So the bands label a direction; the ratio beside them is the evidence,
    # and it takes agreement across runs before anything moves. The middle band recommends nothing.
    verdict = (
        "throughput scaled — room to widen"
        if rate_x >= 1.15
        else "queueing, not throughput — narrowing is free"
        if rate_x <= 1.05
        else "mixed — widen only if the latency cost is acceptable"
    )
    return [
        (
            f"concurrency {fanout}: {width_x:.1f}x the streams bought {rate_x:.2f}x the "
            f"throughput at {lat_x:.1f}x the latency — {verdict}"
        )
    ]
