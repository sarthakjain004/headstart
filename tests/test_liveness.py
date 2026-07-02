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


def test_needs_probe_new_and_unknown():
    assert needs_probe(None, _TODAY) is True  # never seen
    v = Verdict("x", "t", "u", UNKNOWN, None, "2026-07-02")
    assert needs_probe(v, _TODAY) is True  # unknown always re-probed


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
