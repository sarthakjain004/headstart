"""Tests for job turnover (headstart.ingest.job_turnover, ADR-0222).

The contract is one identity and the rules that keep it honest: for every Board-delta key, the
change in stock between two ticks is opened − closed + recounted_in − recounted_out exactly, and
nothing that is not hiring (a found Board, a prune removal, a classifier move, a key change) is
booked as Opened or Closed.
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
    reposts,
    turnover,
)
from headstart.ingest.role_assignments import Placement

_BEFORE = "2026-09-25T05:00:00+00:00"
_AFTER = "2026-09-25T06:00:00+00:00"  # a sync stamp later than the previous tick
_ACME = Placement("greenhouse:acme", "software-engineering", "mid", "greenhouse")


def _flows(previous, current, **kw):
    args = {
        "previous_as_of": _BEFORE,
        "first_seen": {job_id: _AFTER for job_id in current},
        "counted_boards": {"greenhouse:acme"},
        "served": set(current),
        "pruned": set(),
    }
    return turnover(previous, current, **{**args, **kw})


def _by_metric(flows):
    out = Counter()
    for (_board, metric, *_rest), n in flows.items():
        out[metric] += n
    return out


def test_a_new_posting_on_a_counted_board_is_opened_and_an_evicted_one_closed():
    flows = _flows({"old": _ACME}, {"new": _ACME})
    key = ("greenhouse:acme", "software-engineering", "mid", "greenhouse")
    assert flows == {
        (key[0], OPENED, *key[1:]): 1,
        (key[0], CLOSED, *key[1:]): 1,
    }


def test_a_found_boards_backlog_is_recounted_not_opened():
    """A Board the previous tick never counted lands its whole stock at once: already open, not
    hiring. Hot had to exclude 108 such Boards in one week for the same reason (hot_boards)."""
    found = Placement("lever:newco", "software-engineering", "mid", "lever")
    assert _by_metric(_flows({}, {"a": found, "b": found})) == {RECOUNTED_IN: 2}


def test_an_arrival_first_seen_before_the_previous_tick_is_recounted():
    """It was served then, as non-tech: the classifier moved it into tech, nobody posted it.
    A row with no `first_seen` (pre-ADR-0031) cannot be dated either."""
    flows = _flows(
        {},
        {"moved": _ACME, "undated": _ACME},
        first_seen={"moved": "2026-09-20T00:00:00+00:00", "undated": None},
    )
    assert _by_metric(flows) == {RECOUNTED_IN: 2}


def test_a_departure_still_served_or_pruned_is_recounted_not_closed():
    """Still in the table means the classifier moved it out of tech. Pruned means a duplicate or
    an off-Board row: the posting is still served, from another Board, or its Board left."""
    flows = _flows(
        {"to-non-tech": _ACME, "duplicate": _ACME, "evicted": _ACME},
        {},
        served={"to-non-tech"},
        pruned={"duplicate"},
    )
    assert _by_metric(flows) == {RECOUNTED_OUT: 2, CLOSED: 1}


def test_a_key_change_moves_the_row_between_keys_without_opening_or_closing():
    moved = _ACME._replace(family="devops-sre", band="senior")
    flows = _flows({"a": _ACME}, {"a": moved})
    assert flows == {
        (
            "greenhouse:acme",
            RECOUNTED_OUT,
            "software-engineering",
            "mid",
            "greenhouse",
        ): 1,
        ("greenhouse:acme", RECOUNTED_IN, "devops-sre", "senior", "greenhouse"): 1,
    }
    assert _flows({"a": _ACME}, {"a": _ACME}) == {}


def test_flows_add_up_to_the_stock_change_of_every_key():
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
        flows = _flows(
            previous,
            current,
            first_seen={i: rng.choice([_AFTER, _BEFORE, None]) for i in current},
            counted_boards=set(rng.sample(boards, 2)),
            served=set(current) | set(rng.sample(sorted(previous), 3)),
            pruned=set(rng.sample(sorted(previous), 3)),
        )
        stock = Counter(current.values())
        stock.subtract(previous.values())
        sign = {OPENED: 1, CLOSED: -1, RECOUNTED_IN: 1, RECOUNTED_OUT: -1}
        booked = Counter()
        for (board, metric, family, band, ats), n in flows.items():
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
