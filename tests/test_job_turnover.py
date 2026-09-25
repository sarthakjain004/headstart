"""Tests for job turnover (headstart.ingest.job_turnover, ADR-0227).

The contract is one identity and the rules that keep it honest: for every Board-delta key, the
change in stock between two ticks is opened − closed + recounted_in − recounted_out exactly, and
nothing that is not hiring (a found Board, a prune removal, a classifier move, a key change) is
booked as Opened or Closed. Only an id `index sync` evicted is Closed.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

# role_family_classifier, whose title normaliser reposts() shares, imports numpy
pytest.importorskip("numpy")

from headstart.ingest.job_turnover import (
    CLOSED,
    OPENED,
    RECOUNTED_IN,
    RECOUNTED_OUT,
    drop_evictions_through,
    queue_evictions,
    queued_evictions,
    reposts,
    turnover,
)
from headstart.ingest.role_assignments import Placement

_BEFORE = "2026-09-25T05:00:00+00:00"
_AFTER = "2026-09-25T06:00:00+00:00"  # a sync stamp later than the previous tick
_ACME = Placement("greenhouse:acme", "software-engineering", "mid", "greenhouse")


def _turnover(previous, current, **kw):
    args = {
        "previous_as_of": _BEFORE,
        "first_seen": {job_id: _AFTER for job_id in current},
        "counted_boards": {"greenhouse:acme"},
        "evicted": set(previous) - set(current),
    }
    return turnover(previous, current, **{**args, **kw})


def _by_metric(booked):
    out = Counter()
    for (_board, metric, *_rest), n in booked.items():
        out[metric] += n
    return out


def test_a_new_posting_on_a_counted_board_is_opened_and_an_evicted_one_closed():
    booked = _turnover({"old": _ACME}, {"new": _ACME})
    key = ("greenhouse:acme", "software-engineering", "mid", "greenhouse")
    assert booked == {
        (key[0], OPENED, *key[1:]): 1,
        (key[0], CLOSED, *key[1:]): 1,
    }


def test_a_found_boards_backlog_is_recounted_not_opened():
    """A Board the previous tick never counted lands its whole stock at once: already open, not
    hiring. Hot had to exclude 108 such Boards in one week for the same reason (ADR-0171)."""
    found = Placement("lever:newco", "software-engineering", "mid", "lever")
    assert _by_metric(_turnover({}, {"a": found, "b": found})) == {RECOUNTED_IN: 2}


def test_an_arrival_first_seen_before_the_previous_tick_is_recounted():
    """It was served then, as non-tech: the classifier moved it into tech, nobody posted it.
    A row with no `first_seen` (pre-ADR-0031) cannot be dated either."""
    booked = _turnover(
        {},
        {"moved": _ACME, "undated": _ACME},
        first_seen={"moved": "2026-09-20T00:00:00+00:00", "undated": None},
    )
    assert _by_metric(booked) == {RECOUNTED_IN: 2}


def test_only_a_departure_sync_evicted_is_closed():
    """A row the classifier moved out of tech is still served. A prune removal, whether in the
    pipeline or in `cleanup-index`, is a duplicate still served from another Board, or a Board
    that left. Neither is a posting that closed."""
    booked = _turnover(
        {"to-non-tech": _ACME, "pruned-in-cleanup": _ACME, "evicted": _ACME},
        {},
        evicted={"evicted"},
    )
    assert _by_metric(booked) == {RECOUNTED_OUT: 2, CLOSED: 1}


def test_a_key_change_moves_the_row_between_keys_without_opening_or_closing():
    moved = _ACME._replace(family="devops-sre", band="senior")
    booked = _turnover({"a": _ACME}, {"a": moved})
    assert booked == {
        (
            "greenhouse:acme",
            RECOUNTED_OUT,
            "software-engineering",
            "mid",
            "greenhouse",
        ): 1,
        ("greenhouse:acme", RECOUNTED_IN, "devops-sre", "senior", "greenhouse"): 1,
    }
    assert _turnover({"a": _ACME}, {"a": _ACME}) == {}


def test_turnover_adds_up_to_the_stock_change_of_every_key():
    """The identity the sentence rests on, over random ticks: for each key,
    Δstock = opened − closed + recounted_in − recounted_out."""
    rng = random.Random(222)
    boards = ["greenhouse:acme", "lever:newco", "ashby:other"]
    families = ["software-engineering", "devops-sre"]

    def place():
        board = rng.choice(boards)
        return Placement(
            board,
            rng.choice(families),
            rng.choice(["mid", "senior"]),
            board.split(":")[0],
        )

    for _ in range(50):
        previous = {f"p{i}": place() for i in rng.sample(range(40), 25)}
        current = {
            **{
                i: place() if rng.random() < 0.2 else p
                for i, p in previous.items()
                if rng.random() < 0.7
            },
            **{f"n{i}": place() for i in range(rng.randint(0, 10))},
        }
        booked_now = _turnover(
            previous,
            current,
            first_seen={i: rng.choice([_AFTER, _BEFORE, None]) for i in current},
            counted_boards=set(rng.sample(boards, 2)),
            evicted=set(rng.sample(sorted(previous), 6)),
        )
        stock = Counter(current.values())
        stock.subtract(previous.values())
        sign = {OPENED: 1, CLOSED: -1, RECOUNTED_IN: 1, RECOUNTED_OUT: -1}
        booked = Counter()
        for (board, metric, family, band, ats), n in booked_now.items():
            booked[Placement(board, family, band, ats)] += sign[metric] * n
        assert {k: v for k, v in stock.items() if v} == {
            k: v for k, v in booked.items() if v
        }


def test_reposts_match_a_new_id_to_a_missing_one_by_board_and_title():
    arrived = {
        "new-1": ("greenhouse:Acme", "Senior  Backend Engineer"),
        "new-2": ("greenhouse:acme", "Frontend Engineer"),
        "new-3": ("lever:other", "Senior Backend Engineer"),
    }
    absent = {"old-1": ("greenhouse:acme", "senior backend engineer")}
    assert reposts(arrived, absent) == 1
    assert reposts(arrived, {}) == 0


def test_dropping_booked_evictions_reports_what_it_dropped_and_kept(tmp_path):
    queue = tmp_path / "eviction_queue.tsv"
    queue_evictions(queue, "2026-09-24T00:00:00+00:00", ["a", "b"])
    queue_evictions(queue, "2026-09-25T00:00:00+00:00", ["c"])
    assert drop_evictions_through(queue, "2026-09-24T00:00:00+00:00") == (2, 1)
    assert queued_evictions(queue) == {"c": "2026-09-25T00:00:00+00:00"}
