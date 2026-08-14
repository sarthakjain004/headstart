"""Tests for the liveness ledger (ADR-0012): the pure CSV I/O + the TTL re-probe policy."""

from __future__ import annotations

from datetime import date

from headstart import liveness
from headstart.liveness import DEAD, LIVE, UNKNOWN, Verdict, needs_probe

_TODAY = date(2026, 7, 2)


def test_ledger_round_trip(tmp_path):
    path = tmp_path / "greenhouse.csv"
    rows = [
        Verdict(
            "greenhouse",
            "stripe",
            "https://boards.greenhouse.io/stripe",
            LIVE,
            12,
            "2026-07-01",
        ),
        Verdict(
            "greenhouse",
            "deadco",
            "https://boards.greenhouse.io/deadco",
            DEAD,
            None,
            "2026-06-01",
        ),
        Verdict(
            "greenhouse",
            "hazy",
            "https://boards.greenhouse.io/hazy",
            UNKNOWN,
            None,
            "2026-07-02",
        ),
    ]
    liveness.write(path, rows)
    back = liveness.load(path)
    assert back["stripe"] == rows[0]  # jobs stays an int
    assert back["deadco"].jobs is None  # blank jobs -> None
    assert back["hazy"].status == UNKNOWN


def test_load_missing_file_is_empty(tmp_path):
    assert liveness.load(tmp_path / "nope.csv") == {}


def test_needs_probe_new_board():
    assert needs_probe(None, _TODAY) is True  # never seen


def test_needs_probe_unknown_ttl():
    """``unknown`` has its own short TTL rather than being re-probed every single run.

    Some boards fail *identically* on all four escalating passes — a Workday board answering 403
    is outside the conclusive set, so it can never settle — and re-probing them each run spent the
    whole four-pass cost to learn nothing. Short TTL: still rechecked twice a week, never settled
    into a false verdict.
    """
    fresh = Verdict("x", "t", "u", UNKNOWN, None, "2026-07-01")  # 1 day old
    stale = Verdict("x", "t", "u", UNKNOWN, None, "2026-06-25")  # 7 days old
    assert needs_probe(fresh, _TODAY, unknown_ttl=3) is False
    assert needs_probe(stale, _TODAY, unknown_ttl=3) is True


def test_unknown_ttl_is_far_shorter_than_dead():
    """It means "ask again soon", not "settled" — guard against it drifting toward dead's 90d."""
    assert liveness.UNKNOWN_TTL_DAYS < liveness.LIVE_TTL_DAYS < liveness.DEAD_TTL_DAYS


def test_needs_probe_live_ttl():
    fresh = Verdict("x", "t", "u", LIVE, 5, "2026-06-28")  # 4 days old
    stale = Verdict("x", "t", "u", LIVE, 5, "2026-06-20")  # 12 days old
    assert needs_probe(fresh, _TODAY, live_ttl=7) is False
    assert needs_probe(stale, _TODAY, live_ttl=7) is True


def test_needs_probe_dead_ttl():
    fresh = Verdict("x", "t", "u", DEAD, None, "2026-06-01")  # 31 days old
    stale = Verdict("x", "t", "u", DEAD, None, "2026-01-01")  # ~182 days old
    assert needs_probe(fresh, _TODAY, dead_ttl=90) is False
    assert needs_probe(stale, _TODAY, dead_ttl=90) is True


def test_needs_probe_unparseable_date_is_stale():
    v = Verdict("x", "t", "u", LIVE, 5, "")
    assert needs_probe(v, _TODAY) is True
