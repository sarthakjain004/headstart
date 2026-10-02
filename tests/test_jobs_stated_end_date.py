"""Tests for headstart.jobs.stated_end_date: the day a description says its posting or its
applications end (ADR-0367). Every phrasing is a served posting's, read off the table of
2026-09-30."""

from __future__ import annotations

from datetime import date

import pytest

from headstart.jobs import stated_end_date


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("Job Posting End Date December 17, 2025", date(2025, 12, 17)),  # PwC, s13
        ("Closing date: July 31, 2026", date(2026, 7, 31)),
        ("Application Deadline: 08/17/2026", date(2026, 8, 17)),
        ("Job Posting End Date: 10-13-2025", date(2025, 10, 13)),
        ("deadline for applications is 8/15/2026", date(2026, 8, 15)),
        ("job posting expires on 24 September 2026", date(2026, 9, 24)),
        ("Closing Date: 29th September 2026", date(2026, 9, 29)),
        ("Applications close: 7th September 2026", date(2026, 9, 7)),
        ("Application closing date: Monday, 12 October 2026", date(2026, 10, 12)),
        ("Deadline 12th of October 2026", date(2026, 10, 12)),
        ("Deadline: 31.10.2026", date(2026, 10, 31)),
        ("Deadline: 25/09/2026", date(2026, 9, 25)),
        ("Posting End Date: 2026-12-11", date(2026, 12, 11)),
        ("Last Date to Apply: October 1, 2026", date(2026, 10, 1)),
        ("apply by 31/10/2026", date(2026, 10, 31)),
        ("Posting End Date: 30 Oct 2026", date(2026, 10, 30)),
    ],
)
def test_a_closing_label_with_a_date_right_after_it_is_read(text, day):
    read = stated_end_date.latest(f"About the role. {text}. Apply now.")
    assert read is not None and read.day == day
    assert read.said in text and read.said.endswith(text.split()[-1])


@pytest.mark.parametrize(
    "text",
    [
        "Meet deadlines in a fast-paced team.",
        "Must meet the deadline. Started on December 17, 2025.",
        # No year: either side of today.
        "Applications close on 11th October.",
        # Day or month first cannot be told apart.
        "Job Posting End Date: 09/6/2026",
        "The posting will close on the day before the posting end date.",
        "Deadline: 2099-01-01",
        "",
        None,
    ],
)
def test_no_day_is_read_without_a_label_a_year_or_a_certain_order(text):
    assert stated_end_date.latest(text) is None


def test_the_latest_of_several_stated_days_wins():
    text = "Closing date: 1 August 2026. Deadline extended: Deadline: 30 October 2026."
    assert stated_end_date.latest(text).day == date(2026, 10, 30)
