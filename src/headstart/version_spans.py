"""Which series version each run of the Trends ledgers was counted at (ADR-0221).

`role_trends` stamps every row, and every Board-delta tick, with a series version; a refit or a
new classifier head starts a new one, whose first tick re-writes every series and every Board's
stock from scratch. A version's *span* is a stretch of consecutive ticks counted at it, so a
refit is a step in one history rather than its end, and a version that returns after a newer one
(a head rolled back) is a new span, not a continuation of its old one — its first tick is a
fresh re-write too, because `role_trends` finds no snapshot at it.

Shared by the Space (which must not import `headstart.ingest`) and the Hot stage, so both read
the same spans off the same ledger.
"""

from __future__ import annotations

from collections.abc import Iterable


def spans(ticks: Iterable[tuple[str, int]]) -> list[tuple[int, str, str | None]]:
    """``(version, first ts, the next span's first ts or None)`` for each run of consecutive
    ticks counted at one version, oldest first. ``ticks`` are ``(ts, version)`` pairs in any
    order, repeats allowed.

    A new span starts at a tick holding a version not seen since the running span began: the
    refit's own tick, which carries the new version's re-write (and may carry the old one's
    last rows beside it). A version the running span has already seen is a stray write and
    starts nothing."""
    by_ts: dict[str, set[int]] = {}
    for ts, version in ticks:
        by_ts.setdefault(ts, set()).add(version)
    out: list[list] = []
    seen: set[int] = set()
    for ts in sorted(by_ts):
        versions = by_ts[ts]
        fresh = versions - seen
        if not out or fresh:
            out.append([max(fresh or versions), ts, None])
            seen = set(versions)
        else:
            seen |= versions
    for k in range(len(out) - 1):
        out[k][2] = out[k + 1][1]
    return [(version, start, end) for version, start, end in out]


def version_at(span_list: list[tuple[int, str, str | None]], ts: str) -> int | None:
    """The version the span holding ``ts`` was counted at, or None before the first span."""
    found = None
    for version, start, _ in span_list:
        if start <= ts:
            found = version
        else:
            break
    return found
