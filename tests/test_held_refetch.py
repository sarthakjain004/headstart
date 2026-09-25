"""Tests for the held-description re-fetch rotation (headstart.ingest.held_refetch, ADR-0211)."""

from __future__ import annotations

import gzip
from collections import Counter
from datetime import UTC, datetime, timedelta

from headstart.ingest import held_refetch as hr

AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
PERIOD = timedelta(days=hr.PERIOD_DAYS)


def test_a_held_job_is_due_once_its_last_fetch_is_a_period_old():
    held = {"eightfold": {"eightfold:acme:old", "eightfold:acme:recent"}}
    checked = {
        "eightfold:acme:old": AT - PERIOD,
        "eightfold:acme:recent": AT - PERIOD + timedelta(hours=1),
    }
    assert hr.plan(held, checked, AT) == {"eightfold:acme:old"}


def test_an_unknown_job_is_seeded_so_a_first_round_is_spread_over_the_period():
    """An empty ledger (the rollout, or a lost file) must not re-fetch every held Job at once.
    Each unknown Job gets a last-fetch hour spread over the past period, so about one run's
    share falls due each hour, and once due it stays due until a scrape reaches it."""
    ids = {f"eightfold:acme:{n}" for n in range(5000)}
    checked: dict = {}
    due_now = hr.plan({"eightfold": ids}, checked, AT)
    assert due_now == set()  # nothing is a full period old yet
    hours = Counter(
        (AT - checked[i]) // timedelta(hours=1) for i in ids
    )  # hours since the seeded fetch
    assert set(hours) <= set(range(hr.PERIOD_DAYS * 24))
    assert max(hours.values()) < 3 * len(ids) / (hr.PERIOD_DAYS * 24)

    later = AT + timedelta(hours=10)
    due_later = hr.plan({"eightfold": ids}, checked, later)
    assert 0 < len(due_later) < len(ids) / 10
    # Not reached by any scrape: still due a day later, plus whatever else fell due since.
    assert due_later <= hr.plan({"eightfold": ids}, checked, later + timedelta(days=1))


def test_the_ledger_is_narrowed_to_held_jobs():
    checked = {"eightfold:acme:gone": AT}
    hr.plan({"eightfold": {"eightfold:acme:1"}}, checked, AT)
    assert set(checked) == {"eightfold:acme:1"}


def test_record_stamps_a_fetch_and_an_asked_row_but_nothing_else():
    checked: dict = {}
    hr.record(
        checked,
        [
            hr.CorpusRow("eightfold:acme:fresh", True),  # fetched, and text came back
            hr.CorpusRow(
                "eightfold:acme:asked", False
            ),  # due and asked for; came back empty
            hr.CorpusRow("eightfold:acme:skipped", False),  # held and skipped: no fetch
            hr.CorpusRow("greenhouse:acme:fresh", True),  # not a rotated ATS
        ],
        {"eightfold:acme:asked"},
        AT,
    )
    assert checked == {"eightfold:acme:fresh": AT, "eightfold:acme:asked": AT}


def test_the_ledger_round_trips_and_a_damaged_one_never_fails_the_run(tmp_path):
    path = tmp_path / "checked.tsv.gz"
    hr.write_checked(path, {"eightfold:acme:1": AT})
    assert hr.read_checked(path) == {"eightfold:acme:1": AT}
    assert hr.read_checked(tmp_path / "absent.tsv.gz") == {}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("eightfold:acme:1\t2026-09-25T12+00:00\nbroken line\nx\tnot-a-date\n")
    assert hr.read_checked(path) == {"eightfold:acme:1": AT}
    path.write_bytes(b"not gzip")
    assert hr.read_checked(path) == {}


def test_malformed_ledger_lines_are_counted(tmp_path, caplog):
    path = tmp_path / "checked.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("lever:a:1\t2026-09-01T00+00:00\nno-tab\nlever:a:2\tnot-a-date\n")
    with caplog.at_level("INFO", logger=hr.__name__):
        assert list(hr.read_checked(path)) == ["lever:a:1"]
    assert caplog.messages == [f"{path}: skipped 2 malformed line(s)"]
