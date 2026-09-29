"""The one sentence every trends answer leads with when turnover covers less than its window.

HeadStart counts postings opened and closed (turnover, ADR-0227) only from 2026-09-25 18:16, so
over a longer window it cannot say whether hiring rose (ADR-0321). `read_trends` and `hiring_now`
both say so, in these words, before any figure.
"""

from __future__ import annotations

from datetime import datetime


def _days(start: str, end: str) -> float:
    return (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 86400


def span_sentence(began: str | None, start: str, end: str) -> str | None:
    """What a window from ``start`` to ``end`` can say of hiring when turnover was counted only
    from ``began``; None when turnover covers the whole window, or was never counted."""
    if not began or began <= start:
        return None
    measured = (
        "HeadStart can measure hiring, as postings opened and closed, only from "
        f"{began[:16].replace('T', ' ')}"
    )
    if began >= end:
        return (
            f"{measured}, after this window ends: over this window it cannot say whether "
            "hiring rose or fell."
        )
    counted = f"{_days(began, end):.1f}"
    return (
        f"{measured}: {counted} of this window's {_days(start, end):.1f} days. Over the whole "
        "window it cannot say whether hiring rose or fell; the opened and closed below are "
        f"those {counted} days'."
    )
