"""`trends.found_late`: which postings were found late, their counts per Board and span, the one
rule a figure of postings opened is called mostly found late by, and the split `/trends` gives its
company lines (ADR-0351, ADR-0369)."""

from __future__ import annotations

import pytest

from headstart.trends import found_late
from headstart.trends.trend_history import TrendQuestion

_SEEN = "2026-09-26T20:07:20+00:00"


@pytest.mark.parametrize(
    "posted, late",
    [
        ("2026-05-05", True),
        ("2026-09-11T00:00:00.000-04:00", True),  # 15 days before first sight
        ("2026-09-12", False),  # 14 days is not past FOUND_LATE_DAYS
        ("2026-09-26", False),
        (None, False),  # undated counts as fresh
        ("21-Apr-2026", False),  # unreadable counts as fresh
        ("2026-13-40", False),
    ],
)
def test_a_posting_is_found_late_only_past_fourteen_days_before_first_sight(
    posted, late
):
    assert found_late.posting_found_late(_SEEN, posted) is late


@pytest.mark.parametrize(
    "opened, fresh, late, mostly",
    [
        (509, 112, 410, True),  # Deloitte US, 2026-09-30
        (
            279,
            74,
            303,
            True,
        ),  # Amgen: more found late than opened still reads as mostly
        (38, 19, 234, False),  # Accenture Federal Services: fresh is half of opened
        (575, 0, 0, False),  # New York Life: opened left nothing served
        (9, 0, 9, False),  # below MIN_OPENED
        (10, 4, 5, True),
        (94, 89, 8, False),  # Deloitte South Asia
        (None, 1, 2, False),
        (10, None, 9, False),
        (10, 1, None, False),
    ],
)
def test_mostly_found_late_needs_both_halves_and_enough_opened(
    opened, fresh, late, mostly
):
    assert found_late.mostly_found_late(opened, fresh, late) is mostly


def _postings():
    rows = [
        ("workday:acme/Site:One:REQ:1", "2026-09-26T10:00:00+00:00", "2026-01-01"),
        ("WORKDAY:ACME/SITE:ONE:REQ:2", "2026-09-26T10:00:00+00:00", "2026-09-25"),
        ("workday:acme/Site:One:REQ:3", "2026-09-28T10:00:00+00:00", "2026-08-01"),
        ("gh:beta:1", "2026-09-27T10:00:00+00:00", None),
        ("gh:beta:2", None, "2026-01-01"),  # never seen: left out
        (
            "lever:nobody:1",
            "2026-09-27T10:00:00+00:00",
            "2026-01-01",
        ),  # no Board of ours
    ]
    return found_late.FirstSeenPostings(rows, ["workday:acme/Site:One", "gh:beta"])


def test_a_span_counts_the_postings_first_seen_after_its_start_and_by_its_end():
    """A Board is matched whole and case-blind, so a colon in its key names it (ADR-0049); a
    posting first seen exactly at the span's start is before it, one at its end inside it."""
    postings = _postings()
    acme = ["workday:acme/site:one"]
    assert postings.split(acme, "2026-09-25", "2026-09-30") == (1, 2)
    assert postings.split(acme, "2026-09-26T10:00:00+00:00", "2026-09-30") == (0, 1)
    assert postings.split(acme, "2026-09-25", "2026-09-26T10:00:00+00:00") == (1, 1)
    both = ["workday:acme/Site:One", "gh:beta"]
    assert postings.split(both, "2026-09-25", "2026-09-30") == (2, 2)
    assert postings.split(["lever:nobody"], "2026-09-25", "2026-09-30") == (0, 0)
    assert postings.split(both, "2026-09-29", "2026-09-30") == (0, 0)


def test_the_clause_names_the_counts_only_where_opened_was_mostly_found_late():
    turnover = {"opened": 509, "closed": 14, "net": 495}
    assert found_late.clause(turnover) is None, "no split given: nothing said"
    turnover.update(opened_fresh=112, opened_found_late=410)
    assert found_late.clause(turnover) == (
        "most of the 509 postings opened were found, not newly posted: of the postings "
        "HeadStart first saw in these runs and still lists, 410 were posted over 14 days "
        "before HeadStart first saw them and 112 within 14 days or with no date"
    )
    assert found_late.sentence(turnover).startswith("Most of the 509 postings opened")
    assert found_late.sentence(turnover).endswith(
        "within 14 days or with no date. So most of this opened is not hiring."
    )
    turnover.update(opened_fresh=400)
    assert found_late.clause(turnover) is found_late.sentence(turnover) is None


def _payload(split_by="bands", **extra):
    def move():
        return {"turnover": {"opened": 20, "closed": 1, "net": 19}}

    payload = {
        "metric": "stock",
        "coverage": "all",
        "family": None,
        "split_by": split_by,
        "turnover_since": "2026-09-25T18:16:48+00:00",
        "counted_since": {
            "workday:acme/Site:One": "2026-09-20T00:00:00+00:00",
            "gh:beta": "2026-09-26T12:00:00+00:00",
        },
        "companies": [
            {"key": "workday:acme/Site:One", "board_keys": ["workday:acme/Site:One"]},
            {"key": "gh:beta", "board_keys": ["gh:beta"]},
        ],
        "reading": {
            "window": {
                "from": "2026-09-23T00:00:00+00:00",
                "to": "2026-09-30T00:00:00+00:00",
            },
            "total": {"move": move()},
            "lines": [
                {"name": "workday:acme/Site:One", "move": move()},
                {"name": "gh:beta", "move": move()},
                {"name": "software-engineering", "move": move()},
            ],
        },
    }
    payload.update(extra)
    return payload


def _split(move):
    turnover = move["turnover"]
    return turnover.get("opened_fresh"), turnover.get("opened_found_late")


def test_a_picked_companys_first_row_carries_its_split_from_its_own_first_count():
    """Each pick counts from the latest of the window's first tick, turnover's first tick and its
    own first count: gh:beta was first counted at noon on the 26th, after its posting's run
    that morning would have been its backlog."""
    payload = _payload()
    found_late.attach(payload, TrendQuestion(), _postings())
    total = payload["reading"]["total"]["move"]
    assert _split(total) == (2, 2)
    assert all(
        "opened_fresh" not in line["move"]["turnover"]
        for line in payload["reading"]["lines"]
    ), "a category line under a pick is the pick's, and is given nothing"
    late_count = _payload(counted_since={"gh:beta": "2026-09-27T12:00:00+00:00"})
    found_late.attach(late_count, TrendQuestion(), _postings())
    assert _split(late_count["reading"]["total"]["move"]) == (1, 2)


def test_each_company_line_carries_its_own_split_under_the_company_split():
    payload = _payload(split_by="company")
    found_late.attach(payload, TrendQuestion(), _postings())
    lines = {line["name"]: line["move"] for line in payload["reading"]["lines"]}
    assert _split(lines["workday:acme/Site:One"]) == (1, 2)
    assert _split(lines["gh:beta"]) == (1, 0)
    assert _split(lines["software-engineering"]) == (None, None)


@pytest.mark.parametrize(
    "payload, question",
    [
        (_payload(family="security"), TrendQuestion()),
        (_payload(), TrendQuestion(ats=("workday",))),
        (_payload(coverage="comparable"), TrendQuestion()),
        (_payload(metric="new"), TrendQuestion()),
        (_payload(companies=[]), TrendQuestion()),
    ],
)
def test_no_split_where_opened_counts_only_part_of_what_the_postings_do(
    payload, question
):
    """A category, an ATS or comparable coverage counts part of a pick's opened, and measure new
    and the whole index have none to set the postings against (ADR-0369)."""
    found_late.attach(payload, question, _postings())
    assert _split(payload["reading"]["total"]["move"]) == (None, None)


def test_unread_postings_give_nothing():
    payload = _payload()
    found_late.attach(payload, TrendQuestion(), None)
    assert _split(payload["reading"]["total"]["move"]) == (None, None)
