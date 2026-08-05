"""Digest selection: the Watermark cut, the ordering, and the cap (ADR-0035)."""

from headstart.alerts.shortlist import CAP, shortlist

WATERMARK = "2026-08-02T12:00:00+00:00"


def _row(first_seen, score, title="Engineer"):
    return {"first_seen": first_seen, "score": score, "title": title}


def test_keeps_only_rows_first_seen_after_the_watermark():
    rows = [
        _row("2026-08-02T13:00:00+00:00", 0.7, "new"),
        _row("2026-08-02T11:00:00+00:00", 0.9, "old"),
        _row(WATERMARK, 0.9, "exactly the watermark"),
    ]
    assert [r["title"] for r in shortlist(rows, WATERMARK)] == ["new"]


def test_drops_rows_with_no_first_seen():
    # A pre-ADR-0031 row cannot be shown to be new, so it is never mailed.
    rows = [_row(None, 0.9, "unstamped"), _row("2026-08-02T13:00:00+00:00", 0.1, "new")]
    assert [r["title"] for r in shortlist(rows, WATERMARK)] == ["new"]


def test_orders_by_score_descending():
    rows = [
        _row("2026-08-02T13:00:00+00:00", 0.4, "c"),
        _row("2026-08-02T13:00:00+00:00", 0.9, "a"),
        _row("2026-08-02T13:00:00+00:00", 0.6, "b"),
    ]
    assert [r["title"] for r in shortlist(rows, WATERMARK)] == ["a", "b", "c"]


def test_missing_score_sorts_last_rather_than_raising():
    rows = [
        _row("2026-08-02T13:00:00+00:00", None, "no score"),
        _row("2026-08-02T13:00:00+00:00", 0.2, "scored"),
    ]
    assert [r["title"] for r in shortlist(rows, WATERMARK)] == ["scored", "no score"]


def test_caps_the_digest():
    rows = [_row("2026-08-02T13:00:00+00:00", i / 100) for i in range(80)]
    assert len(shortlist(rows, WATERMARK)) == CAP
    assert len(shortlist(rows, WATERMARK, cap=5)) == 5


def test_a_lagging_space_that_ignores_first_seen_after_still_cuts_here():
    # The whole corpus comes back; only the genuinely-new rows survive.
    rows = [_row(f"2026-07-{day:02d}T00:00:00+00:00", 0.9) for day in range(1, 29)]
    rows.append(_row("2026-08-02T13:00:00+00:00", 0.1, "new"))
    assert [r["title"] for r in shortlist(rows, WATERMARK)] == ["new"]
