"""Tests for the reliable-fetch seam (headstart.http.fetch).

The retry/backoff/transient-classification policy was untested when it lived copy-pasted inside
each scraper's loop; now it lives once, so it's tested once. The session is stubbed (no network)
and sleep is neutralized, so each test asserts exactly what fetch retries, what it gives up on,
and what it hands back for the caller to classify.
"""

import asyncio
import logging

import pytest

from headstart import http


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _err(code):
    e = http.RequestsError("network error")
    e.code = code
    return e


def _stub(monkeypatch, outcomes):
    """Stub the pooled session: each call yields the next outcome (an int status to return, or an
    exception to raise). Returns the call log so tests can assert the attempt count."""
    calls = []

    class _Session:
        def request(self, method, url, **kwargs):
            outcome = outcomes[len(calls)]
            calls.append((method, url, kwargs))
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, tuple):  # (status, headers)
                return _Resp(*outcome)
            return _Resp(outcome)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(http.time, "sleep", lambda *a: None)
    return calls


def test_returns_on_first_success(monkeypatch):
    calls = _stub(monkeypatch, [200])
    assert http.fetch("GET", "u").status_code == 200
    assert len(calls) == 1


def test_retries_transient_status_then_succeeds(monkeypatch):
    calls = _stub(monkeypatch, [503, 429, 200])
    assert http.fetch("GET", "u").status_code == 200
    assert len(calls) == 3


def test_retries_403_as_transient(monkeypatch):
    calls = _stub(monkeypatch, [403, 200])  # bot-wall blip
    assert http.fetch("GET", "u").status_code == 200
    assert len(calls) == 2


def test_retries_405_as_transient(monkeypatch):
    # the shape Eightfold's edge returns once its per-origin budget is spent (ADR-0047); before
    # this it settled on the first attempt and the description was dropped silently
    calls = _stub(monkeypatch, [405, 200])
    assert http.fetch("GET", "u").status_code == 200
    assert len(calls) == 2


def test_405_is_counted_apart_from_403(monkeypatch):
    http.reset_retry_stats()
    _stub(monkeypatch, [405, 403, 200])
    http.fetch("GET", "u")
    stats = http.retry_stats()
    assert stats["405-wall"] == 1
    assert stats["403-wall"] == 1


def test_honours_retry_after_over_the_local_backoff(monkeypatch):
    slept: list[float] = []
    _stub(monkeypatch, [(429, {"Retry-After": "7"}), 200])
    monkeypatch.setattr(http.time, "sleep", slept.append)
    assert http.fetch("GET", "u").status_code == 200
    assert slept == [7.0]  # the host's window, not the 1.5s the curve would have picked


def test_retry_after_is_capped(monkeypatch):
    # a host asking for ten minutes is asking for longer than the shard's whole budget
    slept: list[float] = []
    _stub(monkeypatch, [(429, {"Retry-After": "600"}), 200])
    monkeypatch.setattr(http.time, "sleep", slept.append)
    http.fetch("GET", "u")
    assert slept == [http._MAX_RETRY_AFTER]


def test_zero_retry_after_falls_back_to_the_backoff_curve(monkeypatch):
    # taken literally, "0" means retry immediately — on a rate-limit wall that is three attempts
    # back-to-back, the opposite of what honouring the header is for
    slept: list[float] = []
    _stub(monkeypatch, [(429, {"Retry-After": "0"}), 200])
    monkeypatch.setattr(http.time, "sleep", slept.append)
    http.fetch("GET", "u")
    assert slept == [1.5]


def test_http_date_retry_after_falls_back_to_the_backoff_curve(monkeypatch):
    slept: list[float] = []
    _stub(monkeypatch, [(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), 200])
    monkeypatch.setattr(http.time, "sleep", slept.append)
    http.fetch("GET", "u")
    assert slept == [1.5]


def test_async_path_retries_405_and_honours_retry_after(monkeypatch):
    """The async path is the one this change exists for — Eightfold's detail pass runs on
    ``fetch_async`` via ``fan_out_async``, not on ``fetch``."""
    import asyncio

    outcomes = [_Resp(405, {"Retry-After": "5"}), _Resp(200)]
    calls: list[str] = []
    slept: list[float] = []

    class _AsyncSession:
        async def request(self, method, url, **kwargs):
            calls.append(url)
            return outcomes[len(calls) - 1]

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(http.asyncio, "sleep", _sleep)
    response = asyncio.run(http.fetch_async(_AsyncSession(), "GET", "u"))
    assert response.status_code == 200
    assert len(calls) == 2  # the 405 was retried rather than settled
    assert slept == [5.0]  # and it waited the window the host asked for


def test_settled_transient_is_returned_not_raised(monkeypatch):
    # a persistent 503 settles to a 503 response for the caller to classify — not an exception
    calls = _stub(monkeypatch, [503, 503, 503])
    assert http.fetch("GET", "u").status_code == 503
    assert len(calls) == 3


def test_definitive_4xx_not_retried(monkeypatch):
    calls = _stub(monkeypatch, [404])  # not in the transient set
    assert http.fetch("GET", "u").status_code == 404
    assert len(calls) == 1


def test_dns_failure_not_retried(monkeypatch):
    calls = _stub(monkeypatch, [_err(http._DNS)])
    with pytest.raises(http.RequestsError):
        http.fetch("GET", "u")
    assert len(calls) == 1  # DNS is definitive — no point retrying


def test_transient_network_retried_then_succeeds(monkeypatch):
    calls = _stub(monkeypatch, [_err(28), 200])  # 28 = timeout
    assert http.fetch("GET", "u").status_code == 200
    assert len(calls) == 2


def test_transient_network_exhausted_raises(monkeypatch):
    calls = _stub(monkeypatch, [_err(28), _err(28), _err(28)])
    with pytest.raises(http.RequestsError):
        http.fetch("GET", "u")
    assert len(calls) == 3


def test_retries_log_debug_records(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="headstart.http")
    _stub(monkeypatch, [_err(28), 503, 200])  # one network retry, one status retry
    http.fetch("GET", "u")

    records = [r for r in caplog.records if r.name == "headstart.http"]
    assert [r.levelno for r in records] == [logging.DEBUG, logging.DEBUG]
    assert "attempt 1/3 failed" in records[0].getMessage()
    assert "-> 503" in records[1].getMessage()
    assert "retrying" in records[1].getMessage()


# --- spare egress (ADR-0063) ---------------------------------------------------------------------
# The escalation past retry: a wall status moves an opted-in ATS onto a second egress IP. These
# stub `spare_egress.proxy_url` rather than dialling anything, so what is asserted is the routing decision
# — which request carries a proxy and which does not — not the tunnel itself (see test_spare_egress.py).


@pytest.fixture(autouse=True)
def _clean_egress(monkeypatch):
    """Walled groups are process-global; leaking one would make later tests route unexpectedly.

    The rotation cooldown is neutralized here because `rotate` now *waits* it out rather than
    returning: left at its real value, every test that walls twice would sit out a real 5 seconds
    to assert something about routing. The cooldown itself is policy, and it is tested where it
    lives, in test_spare_egress.py.
    """
    monkeypatch.setattr(http.spare_egress, "_ROTATION_COOLDOWN", 0.0)
    http.spare_egress.reset()
    yield
    http.spare_egress.reset()


#: What Eightfold declares. Passed explicitly everywhere below, because `fetch` deliberately has no
#: default for it — the scraper that knows the ATS is the one that knows what a wall looks like.
_WALL = frozenset({403, 405})


def _warp(monkeypatch, url="socks5://127.0.0.1:40000"):
    monkeypatch.setattr(http.spare_egress, "proxy_url", lambda: url)


def _proxied(calls):
    """Which of the recorded calls carried a proxy, as a list of bools."""
    return ["proxies" in kwargs for _, _, kwargs in calls]


def test_without_egress_group_a_wall_changes_nothing(monkeypatch):
    # every ATS that has not opted in: 403 is still just a retryable blip, and no route moves
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    assert http.fetch("GET", "u").status_code == 200
    assert _proxied(calls) == [False, False]
    assert http.spare_egress.walled_groups() == frozenset()


def test_wall_marks_the_group_and_routes_the_retry(monkeypatch):
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    assert (
        http.fetch("GET", "u", egress_group="eightfold", egress_on=_WALL).status_code
        == 200
    )
    assert http.spare_egress.walled_groups() == frozenset({"eightfold"})
    # the first attempt goes direct (nothing known yet); the retry is the one that moves
    assert _proxied(calls) == [False, True]
    assert calls[1][2]["proxies"] == {
        "http": "socks5://127.0.0.1:40000",
        "https": "socks5://127.0.0.1:40000",
    }


def test_a_later_request_starts_on_the_spare_egress(monkeypatch):
    # the point of keying on the ATS rather than the Board: the second Board must not have to
    # rediscover the wall by spending its own three attempts
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [405, 200, 200])
    http.fetch("GET", "board-1", egress_group="eightfold", egress_on=_WALL)
    http.fetch("GET", "board-2", egress_group="eightfold", egress_on=_WALL)
    assert _proxied(calls) == [False, True, True]


def test_wall_on_the_final_attempt_still_marks(monkeypatch):
    # this request is lost either way, but it is exactly as informative about the origin as an
    # early wall — not recording it would make every later Board repeat the same three attempts
    _warp(monkeypatch)
    _stub(monkeypatch, [405, 405, 405])
    assert (
        http.fetch("GET", "u", egress_group="eightfold", egress_on=_WALL).status_code
        == 405
    )
    assert http.spare_egress.walled_groups() == frozenset({"eightfold"})


def test_transient_but_non_wall_status_does_not_move_egress(monkeypatch):
    # a 500 is the origin failing, not the origin refusing this IP; spending a second budget on it
    # would buy nothing
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [500, 200])
    http.fetch("GET", "u", egress_group="eightfold", egress_on=_WALL)
    assert http.spare_egress.walled_groups() == frozenset()
    assert _proxied(calls) == [False, False]


def test_custom_egress_on_is_respected(monkeypatch):
    # the opt-in carries its own statuses, so a future ATS walled by 429 does not have to accept
    # Eightfold's 403/405 definition
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    http.fetch("GET", "u", egress_group="other", egress_on=frozenset({429}))
    assert http.spare_egress.walled_groups() == frozenset()
    assert _proxied(calls) == [False, False]


def test_no_warp_available_stays_on_the_direct_route(monkeypatch):
    # the degradation that makes this safe to ship: a runner without WARP behaves as it does today
    _warp(monkeypatch, url=None)
    calls = _stub(monkeypatch, [403, 403, 403])
    assert (
        http.fetch("GET", "u", egress_group="eightfold", egress_on=_WALL).status_code
        == 403
    )
    assert http.spare_egress.walled_groups() == frozenset(
        {"eightfold"}
    )  # still recorded, for the log line
    assert _proxied(calls) == [False, False, False]


def test_a_group_with_no_wall_statuses_never_marks(monkeypatch):
    """`fetch` has no default `egress_on`, so a caller that names a group but no statuses opts
    into nothing. Asserted rather than assumed: this is the shape a mis-wired scraper would take."""
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    http.fetch("GET", "u", egress_group="eightfold")
    assert http.spare_egress.walled_groups() == frozenset()
    assert _proxied(calls) == [False, False]


def test_proxied_requests_are_counted_at_both_levels(monkeypatch):
    """Attempts say what the fallback cost; settled requests say what it bought.

    Counting only attempts made a request that walled twice before succeeding score 1/3 rather
    than 1/1, so every retry pushed the "recovery rate" down and a working spare egress looked
    like a failing one.
    """
    _warp(monkeypatch)
    monkeypatch.setattr(http.spare_egress, "rotate", lambda board=None: True)
    _stub(monkeypatch, [403, 200, 405, 405, 405])
    http.fetch(
        "GET", "u", egress_group="eightfold", egress_on=_WALL
    )  # direct 403 -> mark; retry over the proxy -> 200
    http.fetch(
        "GET", "v", egress_group="eightfold", egress_on=_WALL
    )  # already walled: 3 tries, all refused
    counts = http.spare_egress.traffic()["eightfold"]
    # attempt 1 of the first fetch went direct (nothing known yet); the other four were carried
    assert counts["routed"] == 4
    # but only two *requests* settled: one rescued, one still walled after every attempt
    assert (counts["requests"], counts["rescued"], counts["walled"]) == (2, 1, 1)


def test_a_non_wall_status_is_not_counted_against_the_spare_egress(monkeypatch):
    """A Board serving stale URLs that all 404 must not read as a refused IP range.

    `eightfold:nttdata.eightfold.ai` migrated off the ATS while its sitemap kept serving; its
    16,304 detail fetches all 404 through the proxy. Scored as failures to recover they dragged
    that shard's rate to 1% and looked exactly like the signal for abandoning the spare egress.
    """
    _warp(monkeypatch)
    _stub(monkeypatch, [403, 404])
    http.fetch(
        "GET", "u", egress_group="eightfold", egress_on=_WALL
    )  # wall, then a real 404
    counts = http.spare_egress.traffic()["eightfold"]
    assert counts["other"] == 1
    assert (
        counts["walled"] == 0 and counts["rescued"] == 0
    )  # excluded, not counted against
    # nothing was ever walled *through* the proxy, so there is no rate to report — which is the
    # honest answer. The old counter called this 0% recovery and read as a refused IP range.
    line = http.spare_egress.report()[0]
    assert (
        "rescued 0/0 walled request(s) (n/a)" in line and "1 settled non-wall" in line
    )


def test_the_ladder_is_direct_then_spare_egress_then_rotate(monkeypatch):
    """The whole escalation, in order: direct -> spare egress -> rotated spare egress.

    Each rung is a *different* recovery, so a log says which one worked rather than just "it took
    three tries". A wall seen *through* the spare egress means that IP is spent too, so the next
    rung moves again instead of spending another attempt on a route we were just refused by.
    """
    rotations = []
    monkeypatch.setattr(
        http.spare_egress, "proxy_url", lambda: "socks5://127.0.0.1:40000"
    )
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None: (rotations.append(board), True)[1],
    )
    calls = _stub(monkeypatch, [429, 429, 200])

    http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert _proxied(calls) == [False, True, True]  # direct, spare, spare (rotated)
    assert len(rotations) == 1  # only the wall seen *through* the proxy rotates


def test_a_wall_on_the_direct_route_never_rotates(monkeypatch):
    """Rotation answers "this IP is spent". On the first, direct attempt we do not yet have a
    spare egress to rotate — moving then would burn a rotation to learn nothing."""
    rotations = []
    monkeypatch.setattr(
        http.spare_egress, "proxy_url", lambda: None
    )  # no WARP available
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None: (rotations.append(board), True)[1],
    )
    _stub(monkeypatch, [429, 429, 429])
    http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))
    assert rotations == []


# --- spare egress on the async path (ADR-0063, second gap) ----------------------------------------
# `fetch_async` is where the detail passes live — Workday's 100-stream description fetch and
# Eightfold's detail/JSON-LD fan-outs — which is exactly the traffic that spends an Origin budget.
# Run 32146017194 measured 37,688 Workday requests carried by the spare egress on the *sync* path
# while every async detail request kept hammering the walled IP: the async path never grew the
# egress seam. These mirror the sync tests above, plus the one production shape the sync tests
# cannot express — a wall marked by the listing (sync) routing the detail pass (async).


def _astub(monkeypatch, outcomes):
    """Async twin of `_stub`: a fake AsyncSession yielding the next outcome per call."""
    calls = []

    class _AsyncSession:
        async def request(self, method, url, **kwargs):
            outcome = outcomes[len(calls)]
            calls.append((method, url, kwargs))
            if isinstance(outcome, BaseException):
                raise outcome
            return _Resp(outcome) if isinstance(outcome, int) else _Resp(*outcome)

    async def _sleep(seconds):
        pass

    monkeypatch.setattr(http.asyncio, "sleep", _sleep)
    return _AsyncSession(), calls


def test_async_without_egress_group_a_wall_changes_nothing(monkeypatch):
    _warp(monkeypatch)
    session, calls = _astub(monkeypatch, [403, 200])
    assert asyncio.run(http.fetch_async(session, "GET", "u")).status_code == 200
    assert _proxied(calls) == [False, False]
    assert http.spare_egress.walled_groups() == frozenset()


def test_async_wall_marks_the_group_and_routes_the_retry(monkeypatch):
    _warp(monkeypatch)
    session, calls = _astub(monkeypatch, [429, 200])
    response = asyncio.run(
        http.fetch_async(
            session, "GET", "u", egress_group="workday", egress_on=frozenset({429})
        )
    )
    assert response.status_code == 200
    assert http.spare_egress.walled_groups() == frozenset({"workday"})
    assert _proxied(calls) == [False, True]
    assert calls[1][2]["proxies"] == {
        "http": "socks5://127.0.0.1:40000",
        "https": "socks5://127.0.0.1:40000",
    }


def test_a_wall_marked_by_the_sync_path_routes_the_async_path(monkeypatch):
    """The production shape, and the reason the seam must be one registry: the *listing* (sync)
    is what sees the 429 first, and the *detail pass* (async) is the hundred-stream traffic that
    most needs to stop hitting the spent IP."""
    _warp(monkeypatch)
    _stub(monkeypatch, [429, 429, 429])
    http.fetch("GET", "listing", egress_group="workday", egress_on=frozenset({429}))

    session, calls = _astub(monkeypatch, [200])
    asyncio.run(
        http.fetch_async(
            session, "GET", "detail", egress_group="workday", egress_on=frozenset({429})
        )
    )
    assert _proxied(calls) == [True], "the async request must start on the spare egress"


def test_async_recovery_is_counted(monkeypatch):
    """`spare egress carried N request(s), M recovered` is the only evidence ADR-0063 works; a
    path that routes but does not count reads as a proxy that carried nothing."""
    _warp(monkeypatch)
    session, _calls = _astub(monkeypatch, [429, 200])
    asyncio.run(
        http.fetch_async(
            session, "GET", "u", egress_group="workday", egress_on=frozenset({429})
        )
    )
    assert http.spare_egress.traffic()["workday"]["rescued"] == 1


def test_async_wall_through_the_proxy_rotates(monkeypatch):
    _warp(monkeypatch)
    rotations = []
    monkeypatch.setattr(
        http.spare_egress, "rotate", lambda board=None: rotations.append(board) or True
    )
    session, _calls = _astub(monkeypatch, [429, 429, 200])
    asyncio.run(
        http.fetch_async(
            session, "GET", "u", egress_group="workday", egress_on=frozenset({429})
        )
    )
    # attempt 1 walls the group (direct), attempt 2 rides the proxy and is walled again -> rotate
    assert rotations == [None]  # no board named by this caller


def test_a_fresh_ip_buys_the_attempt_the_wait_would_have_cost(monkeypatch):
    """`rotate` now blocks until a fresh IP exists, so charging the request for that wait would let
    it exhaust its budget queueing and never try a working route — the opposite of the point."""
    _warp(monkeypatch)
    generation = [0]
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None: generation.__setitem__(0, generation[0] + 1) or True,
    )
    monkeypatch.setattr(http.spare_egress, "rotation_generation", lambda: generation[0])
    calls = _stub(monkeypatch, [429, 429, 429, 429, 200])

    resp = http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert resp.status_code == 200
    assert len(calls) == 5  # 3 base attempts, plus the 2 the rotations earned


def test_the_earned_attempts_are_capped(monkeypatch):
    """A Board every IP refuses would otherwise retry forever, one rotation at a time."""
    _warp(monkeypatch)
    generation = [0]
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None: generation.__setitem__(0, generation[0] + 1) or True,
    )
    monkeypatch.setattr(http.spare_egress, "rotation_generation", lambda: generation[0])
    calls = _stub(monkeypatch, [429] * 8)

    resp = http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert resp.status_code == 429
    assert len(calls) == http._ATTEMPTS + http._MAX_ROTATION_WAITS == 5


def test_a_wait_that_produced_no_fresh_ip_earns_nothing(monkeypatch):
    """Only a generation change is evidence of a new route. A rotation that timed out onto the
    same spent IP must not extend the budget, or a hard wall becomes an unbounded retry loop."""
    _warp(monkeypatch)
    monkeypatch.setattr(http.spare_egress, "rotate", lambda board=None: True)
    monkeypatch.setattr(
        http.spare_egress, "rotation_generation", lambda: 7
    )  # never moves
    calls = _stub(monkeypatch, [429] * 5)

    http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert len(calls) == http._ATTEMPTS  # the base budget, unextended


def test_the_board_that_spent_the_ip_is_named_to_rotate(monkeypatch):
    """Attribution the shard report could not make before: which Boards drank the IP supply."""
    _warp(monkeypatch)
    named = []
    monkeypatch.setattr(
        http.spare_egress, "rotate", lambda board=None: named.append(board) or True
    )
    _stub(monkeypatch, [429, 429, 200])

    http.fetch(
        "GET",
        "u",
        egress_group="workday",
        egress_on=frozenset({429}),
        egress_board="workday:dollartree/dollartreeus",
    )

    assert named == ["workday:dollartree/dollartreeus"]


def test_a_wall_marked_by_the_async_path_routes_the_sync_path(monkeypatch):
    """The registry works in both directions — the docstring says "and vice versa", so a test
    says it too. An eightfold sitemap fallback (sync) must ride a wall its detail pass saw."""
    _warp(monkeypatch)
    session, _acalls = _astub(monkeypatch, [429, 429, 429])
    asyncio.run(
        http.fetch_async(
            session, "GET", "detail", egress_group="workday", egress_on=frozenset({429})
        )
    )
    calls = _stub(monkeypatch, [200])
    http.fetch("GET", "listing", egress_group="workday", egress_on=frozenset({429}))
    assert _proxied(calls) == [True]


def test_async_wall_on_the_final_attempt_still_marks(monkeypatch):
    """Async twin of the sync guarantee: the last attempt's wall is exactly as informative as
    the first's, and not recording it makes every later Board repeat the same three attempts."""
    _warp(monkeypatch)
    session, _calls = _astub(monkeypatch, [429, 429, 429])
    response = asyncio.run(
        http.fetch_async(
            session, "GET", "u", egress_group="workday", egress_on=frozenset({429})
        )
    )
    assert response.status_code == 429
    assert http.spare_egress.walled_groups() == frozenset({"workday"})
