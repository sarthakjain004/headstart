"""Series-version spans (ADR-0221): consecutive runs of ticks, so a rollback is a new span."""

from __future__ import annotations

from headstart.version_spans import spans, version_at


def test_a_refit_ends_one_span_and_starts_the_next() -> None:
    ticks = [("t1", 2), ("t2", 2), ("t3", 2001), ("t4", 2001)]
    assert spans(ticks) == [(2, "t1", "t3"), (2001, "t3", None)]


def test_a_version_that_returns_is_a_new_span() -> None:
    """A head rolled back: its first tick back is a fresh re-write, not a continuation."""
    ticks = [("t1", 3001), ("t2", 3002), ("t3", 3001), ("t4", 3001)]
    assert spans(ticks) == [(3001, "t1", "t2"), (3002, "t2", "t3"), (3001, "t3", None)]


def test_the_refit_tick_starts_the_new_span_and_a_stray_starts_nothing() -> None:
    """The refit's tick may carry the old version's last rows beside the new one's re-write."""
    ticks = [("t1", 2), ("t2", 2001), ("t2", 2), ("t3", 2001), ("t4", 2), ("t4", 2001)]
    assert spans(ticks) == [(2, "t1", "t2"), (2001, "t2", None)]


def test_version_at_reads_the_span_holding_a_tick() -> None:
    s = [(2, "t1", "t3"), (2001, "t3", None)]
    assert [version_at(s, t) for t in ("t0", "t1", "t2", "t3", "t9")] == [
        None,
        2,
        2,
        2001,
        2001,
    ]
