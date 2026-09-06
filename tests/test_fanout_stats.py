"""The per-width fan-out record — does it separate "saturated" from "not the constraint"?"""

from __future__ import annotations

import time

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


def test_the_verdict_calls_flat_throughput_queueing() -> None:
    """The measured shape this exists to surface: 2.1x the streams for ~1.0x the throughput."""
    with fanout_stats.batch("workday pages", 25) as done:
        for _ in range(100):
            done(10.3)
        time.sleep(0.05)
    with fanout_stats.batch("workday pages", 12) as done:
        for _ in range(100):
            done(4.7)
        time.sleep(0.05)
    verdict = [line for line in fanout_stats.report() if "bought" in line]
    assert len(verdict) == 1
    assert "queueing, not throughput" in verdict[0]
    assert "2.1x the streams" in verdict[0]


def test_a_thin_sample_reports_its_row_but_no_verdict() -> None:
    """Three postings on two Boards must not produce a confident-looking recommendation — but the
    row itself still prints, so a small sample is visible rather than silently dropped."""
    with fanout_stats.batch("workday pages", 25) as done:
        done(1.0)
    with fanout_stats.batch("workday pages", 12) as done:
        done(1.0)
    lines = fanout_stats.report()
    assert len(lines) == 2
    assert not [line for line in lines if "bought" in line]


def test_reset_clears_the_totals() -> None:
    with fanout_stats.batch("workday pages", 12) as done:
        done(1.0)
    fanout_stats.reset()
    assert fanout_stats.stats() == {}
