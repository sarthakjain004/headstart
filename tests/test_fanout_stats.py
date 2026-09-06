"""The per-width fan-out record — does it separate "saturated" from "not the constraint"?"""

from __future__ import annotations

from headstart import fanout_stats


def setup_function() -> None:
    fanout_stats.reset()


def test_nothing_fanned_out_says_nothing() -> None:
    """A shard of single-request Boards should stay silent, not print a table of zeroes."""
    assert fanout_stats.report() == []


def test_a_batch_records_its_width_items_and_occupancy() -> None:
    with fanout_stats.batch("workday pages", 12) as done:
        for _ in range(4):
            done(0.5)
    row = fanout_stats.stats()[("workday pages", 12)]
    assert row["batches"] == 1
    assert row["items"] == 4
    assert row["busy"] == 2.0


def test_the_same_site_at_two_widths_stays_two_rows() -> None:
    """The whole point: the ADR-0078 clamp runs one shard at both widths, and they must not be
    averaged into a single number that belongs to neither."""
    with fanout_stats.batch("workday pages", 25) as done:
        done(1.0)
    with fanout_stats.batch("workday pages", 12) as done:
        done(1.0)
    assert set(fanout_stats.stats()) == {("workday pages", 25), ("workday pages", 12)}


def test_a_batch_that_raises_is_still_recorded() -> None:
    """Workday re-raises past `_MAX_LOST_PAGE_SHARE` (ADR-0076). Dropping those batches would
    keep only the healthy ones and bias every width's numbers upward."""
    try:
        with fanout_stats.batch("workday pages", 12) as done:
            done(0.25)
            raise RuntimeError("too little of the board read to keep")
    except RuntimeError:
        pass
    assert fanout_stats.stats()[("workday pages", 12)]["items"] == 1


def _row(fanout: str, width: int, *, items: float, busy: float, wall: float) -> None:
    """Seed one (fan-out, width) row directly.

    The verdict tests do NOT drive `batch()`. Its `wall` is real elapsed time, so a test that
    sleeps to create one is asserting on the OS scheduler: the first version of these did exactly
    that and flipped verdict roughly once in twenty runs, because `rate_x` reduces to the ratio of
    two ~50ms sleeps. `_compare` is arithmetic over four numbers, so give it the four numbers.
    """
    fanout_stats._rows[(fanout, width)] = {
        "batches": 1.0,
        "items": items,
        "busy": busy,
        "wall": wall,
    }


def test_the_verdict_calls_flat_throughput_queueing() -> None:
    """The measured shape this exists to surface: ~2.1x the streams for ~1.0x the throughput."""
    _row("workday pages", 25, items=100, busy=1030.0, wall=45.8)
    _row("workday pages", 12, items=100, busy=470.0, wall=44.7)
    verdict = [line for line in fanout_stats.report() if "bought" in line]
    assert len(verdict) == 1
    assert "queueing, not throughput" in verdict[0], verdict[0]
    assert "2.1x the width" in verdict[0]
    assert "0.98x the throughput" in verdict[0]


def test_the_verdict_says_widen_when_throughput_actually_scaled() -> None:
    """The other direction has to be reachable, or the line only ever says one thing."""
    _row("eightfold details", 25, items=200, busy=400.0, wall=20.0)
    _row("eightfold details", 12, items=100, busy=400.0, wall=20.0)
    verdict = [line for line in fanout_stats.report() if "bought" in line]
    assert len(verdict) == 1
    assert "room to widen" in verdict[0], verdict[0]


def test_the_middle_band_recommends_nothing() -> None:
    """Between the two cut-points the honest answer is that the measurement does not say. Tested
    because an untested band is one nobody has ever seen the wording of."""
    _row("workday pages", 25, items=110, busy=200.0, wall=10.0)
    _row("workday pages", 12, items=100, busy=200.0, wall=10.0)
    verdict = [line for line in fanout_stats.report() if "bought" in line]
    assert len(verdict) == 1
    assert "mixed" in verdict[0], verdict[0]


def test_reset_clears_the_totals() -> None:
    with fanout_stats.batch("workday pages", 12) as done:
        done(1.0)
    fanout_stats.reset()
    assert fanout_stats.stats() == {}
