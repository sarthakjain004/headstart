"""Tests for the consecutive-gone quarantine ledger (headstart.ingest.board_failures).

The property under test throughout: a Board leaves the scrape slice only on *agreement across
runs* — QUARANTINE_AT consecutive gone-verdicts — and any sign of life clears it. One 404 must
never quarantine, and a fetch-level failure (429, timeout) must never count at all: over the 19
runs that motivated this, Workday alone raised 2,840 fatal 429s on boards that are perfectly
alive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from headstart.ingest import board_failures as bf


def test_is_gone_only_matches_the_gone_class():
    assert bf.is_gone("HTTPError: HTTP Error 404: ")
    assert bf.is_gone("HTTPError: HTTP Error 410: Gone")
    # fetch failures, not gone-verdicts — the difference between a quarantine and an outage
    assert not bf.is_gone("HTTPError: HTTP Error 429: ")
    assert not bf.is_gone("HTTPError: HTTP Error 500: ")
    assert not bf.is_gone("Timeout: request timed out")
    assert not bf.is_gone("CertificateVerifyError: hostname mismatch")
    assert not bf.is_gone("")
    # a 404 named in a message but not as the status must not count
    assert not bf.is_gone("ParseError: line 404 of the feed")


def test_strikes_accumulate_only_on_consecutive_gone_runs():
    rows: dict[str, bf.Failure] = {}
    for n in range(1, bf.QUARANTINE_AT + 1):
        rows = bf.update(
            rows, {"greenhouse:hibu": "HTTPError: HTTP Error 404: "}, set(), f"t{n}"
        )
        assert rows["greenhouse:hibu"].strikes == n
        assert rows["greenhouse:hibu"].quarantined == (n >= bf.QUARANTINE_AT)
    assert bf.quarantined(rows) == {"greenhouse:hibu"}


def test_any_successful_scrape_clears_the_streak():
    rows = {
        "greenhouse:hibu": bf.Failure(
            bf.QUARANTINE_AT - 1, "HTTPError: HTTP Error 404: ", "t"
        )
    }
    rows = bf.update(rows, {}, {"greenhouse:hibu"}, "t2")
    assert "greenhouse:hibu" not in rows  # cleared entirely, not reset to 0


def test_a_run_that_did_not_touch_the_board_leaves_its_row_alone():
    """Partial-harvest rule: only ~30% of boards are in any given slice, so an untouched Board
    must neither age toward quarantine nor heal from it."""
    before = {"greenhouse:hibu": bf.Failure(3, "HTTPError: HTTP Error 404: ", "t")}
    after = bf.update(before, {}, set(), "t2")
    assert after == before


def test_partial_output_beats_a_per_page_404():
    """A Board can 404 on one detail page while its listing produced jobs — that is alive."""
    rows = bf.update(
        {}, {"greenhouse:hibu": "HTTPError: HTTP Error 404: "}, {"greenhouse:hibu"}, "t"
    )
    assert rows == {}


def test_ledger_round_trips_through_csv(tmp_path):
    p = tmp_path / "board_failures.csv"
    rows = {
        "greenhouse:hibu": bf.Failure(
            19, "HTTPError: HTTP Error 404: ", "2026-08-18T00:00:00+00:00"
        ),
        "ashby:phare-r1-r37": bf.Failure(
            2, "HTTPError: HTTP Error 404: ", "2026-08-18T00:00:00+00:00"
        ),
    }
    bf.save(p, rows)
    assert bf.load(p) == rows


def test_load_fails_open(tmp_path):
    """This file rides the HF state round-trip; a missing or torn copy must cost one run of
    memory, never quarantine a Board or stop the plan."""
    assert bf.load(tmp_path / "nope.csv") == {}
    p = tmp_path / "torn.csv"
    p.write_text(
        "board,strikes,last_reason,last_seen_gone\ngreenhouse:ok,2,x,t\nbad,notanint,x,t\n"
    )
    loaded = bf.load(p)
    assert "greenhouse:ok" in loaded and "bad" not in loaded


def test_a_gone_verdict_expires_into_parole():
    """The verdict is evidence with an age, not a fact. Past PAROLE_DAYS a quarantined Board is
    re-admitted for one run so the verdict can be re-earned — measured 2026-09-16, 23 of the 757
    Boards then quarantined answered 200 again, 12 of them serving tech postings."""
    fresh = "2026-09-16T00:00:00+00:00"
    stale = "2026-09-01T00:00:00+00:00"
    rows = {
        "greenhouse:fresh": bf.Failure(bf.QUARANTINE_AT, "404", fresh),
        "greenhouse:stale": bf.Failure(bf.QUARANTINE_AT, "404", stale),
        "greenhouse:striking": bf.Failure(bf.QUARANTINE_AT - 1, "404", stale),
    }
    now = "2026-09-16T12:00:00+00:00"
    # only a *quarantined* Board is on parole: one still accruing strikes is in the slice anyway
    assert bf.paroled(rows, now) == {"greenhouse:stale"}


def test_parole_starts_at_exactly_parole_days():
    old = datetime(2026, 9, 1, tzinfo=UTC)
    row = {"greenhouse:b": bf.Failure(bf.QUARANTINE_AT, "404", old.isoformat())}
    day_before = (old + timedelta(days=bf.PAROLE_DAYS, seconds=-1)).isoformat()
    on_time = (old + timedelta(days=bf.PAROLE_DAYS)).isoformat()
    assert bf.paroled(row, day_before) == set()
    assert bf.paroled(row, on_time) == {"greenhouse:b"}


def test_an_unreadable_stamp_paroles():
    """Same direction as every other guard here: a bad date must never be grounds for keeping a
    Board out of the slice forever. The naive stamp is the realistic one: every row this module
    writes is tz-aware, so a naive one is a hand-edited or torn file, and subtracting it raises
    TypeError rather than ValueError."""
    for stamp in ("", "not-a-date", "2026-09-16T00:00:00"):
        rows = {"greenhouse:b": bf.Failure(bf.QUARANTINE_AT, "404", stamp)}
        assert bf.paroled(rows, "2026-09-16T00:00:00+00:00") == {"greenhouse:b"}, stamp


def test_a_re_probe_that_404s_again_restarts_the_parole_clock():
    """The drain must not become a revolving door: a Board that re-earns its verdict goes back
    to serving the full PAROLE_DAYS, because `update` restamps `last_seen_gone`."""
    stale = "2026-09-01T00:00:00+00:00"
    now = "2026-09-16T00:00:00+00:00"
    rows = {"greenhouse:b": bf.Failure(bf.QUARANTINE_AT, "404", stale)}
    assert bf.paroled(rows, now) == {"greenhouse:b"}
    rows = bf.update(rows, {"greenhouse:b": "HTTPError: HTTP Error 404: "}, set(), now)
    assert rows["greenhouse:b"].strikes == bf.QUARANTINE_AT + 1
    assert bf.paroled(rows, now) == set()


def test_a_re_probe_that_answers_clears_the_quarantine():
    """The loop finding 2 of the 2026-09-16 review says is unreachable: `rows.pop` can only fire
    once a quarantined Board is back in the slice."""
    rows = {
        "greenhouse:b": bf.Failure(bf.QUARANTINE_AT, "404", "2026-09-01T00:00:00+00:00")
    }
    rows = bf.update(rows, {}, {"greenhouse:b"}, "2026-09-16T00:00:00+00:00")
    assert rows == {}


def test_key_for_lowercases_a_board_and_a_stored_key_alike():
    """ADR-0192: rows keep `board_key_of`'s casing; the quarantine test compares folded."""
    from headstart.scrapable_boards import ScrapableBoard

    board = ScrapableBoard("workday", "https://Acme.wd1.myworkdayjobs.com/External")
    assert bf.key_for(board) == bf.key_for("workday:Acme/External")
    assert bf.key_for(board) == "workday:acme/external"
