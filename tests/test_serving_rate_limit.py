"""Tests for the Space's per-client sliding window (headstart.serving.rate_limit, ADR-0262).

Contracts: a client is admitted up to its limit inside any window and refused past it, told how
many whole seconds until its oldest request leaves; a refusal is not counted, so asking while
refused never delays the client further; clients never share a count; and a client idle for a
whole window is forgotten, so the map holds one window's clients rather than every one since boot.
"""

from __future__ import annotations

import pytest

from headstart.serving.rate_limit import RateLimit


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return _Clock()


def test_a_client_is_admitted_up_to_its_limit_and_refused_past_it(clock):
    limit = RateLimit(3, 60, clock=clock)
    assert [limit.admit("a") for _ in range(3)] == [0, 0, 0]
    clock.now += 0.5
    # 59.5 s until the first leaves the window, in whole seconds
    assert limit.admit("a") == 60


def test_the_window_frees_as_the_oldest_request_leaves_it(clock):
    limit = RateLimit(2, 60, clock=clock)
    limit.admit("a")
    clock.now += 30
    limit.admit("a")
    assert limit.admit("a") == 30
    clock.now += 30  # the first request is exactly one window old: out
    assert limit.admit("a") == 0
    # the second is still in, and so is the one just admitted
    assert limit.admit("a") == 30


def test_asking_while_refused_is_not_counted(clock):
    limit = RateLimit(1, 60, clock=clock)
    limit.admit("a")
    for _ in range(50):
        clock.now += 1
        assert limit.admit("a") > 0
    clock.now += 10  # 60 s after the one admitted request
    assert limit.admit("a") == 0


def test_clients_never_share_a_count(clock):
    limit = RateLimit(1, 60, clock=clock)
    assert limit.admit("a") == 0
    assert limit.admit("b") == 0
    assert limit.admit("a") > 0


def test_a_client_idle_for_a_whole_window_is_forgotten(clock):
    limit = RateLimit(5, 60, clock=clock)
    for client in ("a", "b", "c"):
        limit.admit(client)
    clock.now += 30
    limit.admit("b")
    clock.now += 30  # "a" and "c" are a window old; "b" asked 30 s ago
    limit.admit("d")
    assert list(limit._admitted) == ["b", "d"]
