"""Tests for the consecutive-gone quarantine ledger (headstart.ingest.board_failures).

The property under test throughout: a Board leaves the scrape slice only on *agreement across
runs* — QUARANTINE_AT consecutive gone-verdicts — and any sign of life clears it. One 404 must
never quarantine, and a fetch-level failure (429, timeout) must never count at all: over the 19
runs that motivated this, Workday alone raised 2,840 fatal 429s on boards that are perfectly
alive.
"""

from __future__ import annotations

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


def test_board_key_of_normalises_the_report_key_space():
    """Shard reports key errors `{ats}:{slug}` where a Workday slug is a whole URL; the corpus
    side keys `board_key()`. The two must land in one key space or gone-verdicts and produced-sets
    silently never pair — the same conversion scrape_join applies for eviction scope."""
    assert (
        bf.board_key_of("workday:https://x.wd1.myworkdayjobs.com/Careers")
        == "workday:x/Careers"
    )
    assert bf.board_key_of("greenhouse:hibu") == "greenhouse:hibu"
    assert bf.board_key_of("notanats:whatever") is None
    assert bf.board_key_of(":slug") is None
    assert bf.board_key_of("greenhouse:") is None
