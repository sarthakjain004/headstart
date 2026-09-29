"""Tests for how many requests a route answers at once (headstart.serving.concurrency_limit,
ADR-0276).

Contracts: a caller takes places up to its own share and no further; every caller together takes
no more than the total; a refusal says which cap it met; a place given back is free again, to a
caller already waiting for it too; and a caller holding nothing is forgotten.
"""

from __future__ import annotations

import threading

from headstart.serving.concurrency_limit import ConcurrencyLimit, Refused


def test_a_caller_takes_up_to_its_share_and_is_refused_past_it():
    limit = ConcurrencyLimit(total=4, each=2)
    assert limit.take("a", 0) is None
    assert limit.take("a", 0) is None
    assert limit.take("a", 0) is Refused.CALLER
    assert limit.take("b", 0) is None  # another caller's share is its own


def test_a_caller_named_in_shares_takes_up_to_its_own_share():
    """ADR-0334: Anthropic's range stands for every claude.ai user, so it holds more places."""
    limit = ConcurrencyLimit(total=4, each=2, shares={"anthropic": 3})
    assert (limit.share("anthropic"), limit.share("a")) == (3, 2)
    for _ in range(3):
        assert limit.take("anthropic", 0) is None
    assert limit.take("anthropic", 0) is Refused.CALLER
    assert limit.take("a", 0) is None  # the last place
    assert limit.take("b", 0) is Refused.TOTAL


def test_every_caller_together_takes_no_more_than_the_total():
    limit = ConcurrencyLimit(total=3, each=2)
    for caller in ("a", "a", "b"):
        assert limit.take(caller, 0) is None
    assert limit.take("c", 0) is Refused.TOTAL
    # Both caps met at once: the caller's own is named, since it is the one it can act on.
    assert limit.take("a", 0) is Refused.CALLER


def test_a_place_given_back_is_free_again():
    limit = ConcurrencyLimit(total=1, each=1)
    assert limit.take("a", 0) is None
    limit.give_back("a")
    assert limit.take("b", 0) is None


def test_a_waiting_caller_gets_the_place_given_back():
    limit = ConcurrencyLimit(total=1, each=1)
    assert limit.take("a", 0) is None
    got = []
    waiter = threading.Thread(target=lambda: got.append(limit.take("b", 5)))
    waiter.start()
    limit.give_back("a")
    waiter.join(5)
    assert got == [None]


def test_a_caller_holding_nothing_is_forgotten():
    limit = ConcurrencyLimit(total=4, each=2)
    limit.take("a", 0)
    limit.give_back("a")
    assert not limit._held
