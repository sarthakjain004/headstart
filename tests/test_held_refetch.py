"""Tests for the held-description re-fetch rotation (headstart.ingest.held_refetch, ADR-0211)."""

from __future__ import annotations

from datetime import date, timedelta

from headstart.ingest import held_refetch as hr

DAY = date(2026, 9, 25)


def test_a_checked_job_is_due_once_its_last_fetch_is_a_period_old():
    checked = {"eightfold:acme:1": DAY - timedelta(days=hr.PERIOD_DAYS)}
    assert hr.is_due("eightfold:acme:1", checked, DAY)
    checked = {"eightfold:acme:1": DAY - timedelta(days=hr.PERIOD_DAYS - 1)}
    assert not hr.is_due("eightfold:acme:1", checked, DAY)


def test_an_unknown_job_is_due_on_exactly_one_day_of_each_cycle():
    """An empty ledger (the rollout, or a lost file) must spread the first round over the period,
    not re-fetch every held Job at once."""
    for n in range(50):
        job_id = f"eightfold:acme:{n}"
        days = [
            d
            for d in range(hr.PERIOD_DAYS)
            if hr.is_due(job_id, {}, DAY + timedelta(days=d))
        ]
        assert len(days) == 1, job_id


def test_record_stamps_a_fetch_and_an_asked_row_but_nothing_else():
    checked: dict = {}
    hr.record(
        checked,
        [
            ("eightfold:acme:fresh", True),  # fetched, and text came back
            ("eightfold:acme:asked", False),  # due and asked for; came back empty
            ("eightfold:acme:skipped", False),  # held and skipped: no fetch happened
            ("greenhouse:acme:fresh", True),  # not a rotated ATS
        ],
        {"eightfold:acme:asked"},
        DAY,
    )
    assert checked == {"eightfold:acme:fresh": DAY, "eightfold:acme:asked": DAY}


def test_the_ledger_round_trips(tmp_path):
    path = tmp_path / "checked.tsv.gz"
    hr.write_checked(path, {"eightfold:acme:1": DAY})
    assert hr.read_checked(path) == {"eightfold:acme:1": DAY}
    assert hr.read_checked(tmp_path / "absent.tsv.gz") == {}
