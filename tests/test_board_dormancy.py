"""Tests for judging Dormant Boards (headstart.ingest.board_dormancy, ADR-0248)."""

from __future__ import annotations

from datetime import date

import pytest

from headstart.ingest import board_dormancy
from headstart.ingest.board_dormancy import PostingDates, posting_date

TODAY = date(2026, 9, 28)  # the cutoff is 2024-09-28


@pytest.mark.parametrize(
    ("posted_at", "day"),
    [
        ("2017-09-14", "2017-09-14"),
        ("2026-06-30T15:46:35+05:00", "2026-06-30"),
        (None, None),
        ("", None),
        (1694649600, None),
        ("21-Apr-2026", None),  # darwinbox's legacy shape
        ("0001-01-01T00:00:00", None),  # keka's placeholders
        ("1900-01-01", None),
    ],
)
def test_posting_date_reads_only_a_real_iso_date(posted_at, day):
    assert posting_date(posted_at) == day


def _dates(*postings: tuple[str, object]) -> PostingDates:
    dates = PostingDates()
    for board, posted_at in postings:
        dates.see(board, posted_at)
    return dates


def test_a_board_whose_every_posting_is_over_two_years_old_is_dormant():
    dates = _dates(
        ("smartrecruiters:SonsoftInc", "2016-05-01"),
        ("smartrecruiters:SonsoftInc", "2017-09-14"),
    )
    assert dates.dormant(TODAY, set()) == {"smartrecruiters:sonsoftinc": "2017-09-14"}


def test_one_recent_posting_keeps_every_old_one_on_its_board():
    """Judged per Board: Databricks' 2021 req is a real opening on a Board that still posts."""
    dates = _dates(
        ("greenhouse:databricks", "2021-01-26"),
        ("greenhouse:databricks", "2026-09-01"),
    )
    assert dates.dormant(TODAY, set()) == {}


def test_the_cutoff_is_two_years_to_the_day():
    dates = _dates(("lever:a", "2024-09-28"), ("lever:b", "2024-09-27"))
    assert dates.dormant(TODAY, set()) == {"lever:b": "2024-09-27"}


@pytest.mark.parametrize("undated_first", [True, False])
def test_an_undated_posting_keeps_its_board_whenever_it_comes(undated_first):
    postings = [("zoho:quiet", "2019-03-01"), ("zoho:quiet", None)]
    dates = _dates(*(postings[::-1] if undated_first else postings))
    assert dates.dormant(TODAY, set()) == {}


def test_a_placeholder_date_keeps_its_board_as_an_undated_one_would():
    dates = _dates(("keka:acme", "2019-03-01"), ("keka:acme", "0001-01-01T00:00:00"))
    assert dates.dormant(TODAY, set()) == {}


def test_a_board_whose_scrape_was_not_authoritative_is_not_judged():
    """A truncated list may have lost the newest page."""
    dates = _dates(("smartrecruiters:SonsoftInc", "2017-09-14"))
    assert dates.dormant(TODAY, {"smartrecruiters:sonsoftinc"}) == {}


def test_a_jibe_board_is_never_judged():
    """Jibe drops a posting an iCIMS Board already serves before it becomes a line (ADR-0240), so
    its lines are not its whole listing."""
    dates = _dates(("jibe:acme", "2019-03-01"))
    assert dates.dormant(TODAY, set()) == {}


def test_board_keys_are_case_folded():
    dates = _dates(
        ("smartrecruiters:Foo", "2017-01-01"), ("smartrecruiters:foo", "2018-01-01")
    )
    assert dates.dormant(TODAY, set()) == {"smartrecruiters:foo": "2018-01-01"}
    assert dates.jobs("smartrecruiters:FOO") == 2


def test_the_verdict_round_trips(tmp_path):
    path = tmp_path / "jobs" / "dormant_boards.json"
    board_dormancy.write({"smartrecruiters:sonsoftinc": "2017-09-14"}, path)
    assert board_dormancy.read(path) == frozenset({"smartrecruiters:sonsoftinc"})


@pytest.mark.parametrize(
    "content", [None, "{not json", '["smartrecruiters:sonsoftinc"]']
)
def test_no_usable_verdict_reads_as_none(tmp_path, content):
    """None leaves every Board in: a lost verdict costs one run of stale rows, never live ones."""
    path = tmp_path / "dormant_boards.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert board_dormancy.read(path) is None
