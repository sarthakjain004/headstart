"""The sentence read_trends and hiring_now lead with when turnover covers less than a window
(ADR-0321)."""

from __future__ import annotations

from headstart.space_mcp.turnover_span import span_sentence

BEGAN = "2026-09-25T18:16:48+00:00"


def test_a_window_turnover_covers_whole_says_nothing():
    assert (
        span_sentence(BEGAN, "2026-09-26T00:00:00+00:00", "2026-09-29T00:00:00+00:00")
        is None
    )
    assert (
        span_sentence(None, "2026-09-01T00:00:00+00:00", "2026-09-29T00:00:00+00:00")
        is None
    )


def test_a_window_turnover_covers_part_of_gives_both_spans():
    assert span_sentence(
        BEGAN, "2026-08-30T00:00:00+00:00", "2026-09-29T04:04:35+00:00"
    ) == (
        "HeadStart can measure hiring, as postings opened and closed, only from 2026-09-25 "
        "18:16: 3.4 of this window's 30.2 days. Over the whole window it cannot say whether "
        "hiring rose or fell; the opened and closed below are those 3.4 days'."
    )


def test_a_window_that_ends_before_turnover_began_says_it_cannot_tell():
    assert span_sentence(
        BEGAN, "2026-09-01T00:00:00+00:00", "2026-09-07T23:59:59+00:00"
    ) == (
        "HeadStart can measure hiring, as postings opened and closed, only from 2026-09-25 "
        "18:16, after this window ends: over this window it cannot say whether hiring rose or "
        "fell."
    )
