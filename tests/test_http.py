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


def test_a_network_error_is_never_classified_by_digits_in_its_message(monkeypatch):
    """Retry classes come from the status, never from the message text.

    They used to come from a substring test, and libcurl puts numbers in its errors: `port 40000`
    — which is literally `spare_egress._PORT` — read as a 400, `HTTP/2 stream 1400` read as a 400,
    `port 40500` read as a 405 bot-wall. Every WARP connect failure therefore landed in a status
    bucket. These buckets are the signal for "is this ATS degrading", so a network failure
    landing in one would misread as a wall or a rate limit."""
    http.reset_retry_stats()
    for message in (
        "Failed to connect to 127.0.0.1 port 40000: Connection refused",
        "curl: (92) HTTP/2 stream 1400 was not closed cleanly",
        "Operation timed out after 4001 milliseconds",
        "Recv failure on port 40500",
    ):
        calls = _stub(monkeypatch, [http.RequestsError(message), 200])
        http.fetch("GET", "u")
        assert len(calls) == 2
    stats = http.retry_stats()
    assert stats["network"] == 4, stats
    assert "405-wall" not in stats  # "port 40500" must not read as a 405 bot-wall
    assert list(stats) == ["network"], stats  # no digit landed in any status bucket


def test_does_not_retry_400_by_default(monkeypatch):
    """A 400 stays settled for every caller that does not ask otherwise. It usually *is* a
    malformed request, and retrying one wastes the ladder against a host that will keep saying
    no — which is why `TRANSIENT` does not carry it."""
    calls = _stub(monkeypatch, [400, 200])
    assert http.fetch("GET", "u").status_code == 400
    assert len(calls) == 1


def test_retries_a_status_when_the_caller_opts_in(monkeypatch):
    """`retry_on` is a general per-caller seam: extend `TRANSIENT` and that status earns a retry
    for this call only. (No live caller extends it today — workday's 400 was reverted in ADR-0103
    — but the seam stays because it is not host-specific.)"""
    calls = _stub(monkeypatch, [400, 200])
    got = http.fetch("GET", "u", retry_on=http.TRANSIENT | {400})
    assert got.status_code == 200
    assert len(calls) == 2


def test_async_retries_a_status_when_the_caller_opts_in(monkeypatch):
    """`retry_on` overrides identically on the async path."""
    _warp(monkeypatch)
    session, calls = _astub(monkeypatch, [400, 200])
    got = asyncio.run(
        http.fetch_async(session, "GET", "u", retry_on=http.TRANSIENT | {400})
    )
    assert got.status_code == 200
    assert len(calls) == 2


def test_async_retries_are_classified_by_status_too(monkeypatch):
    """The async path has to thread the status into the counter as well.

    Mutation-tested: dropping the status argument from `fetch_async`'s `_note_retry` call left
    every other test green, on the path where Workday's 400s actually land — so the counter
    ADR-0098 reads would have silently reported `network` for all of them."""
    _warp(monkeypatch)
    http.reset_retry_stats()
    session, _ = _astub(monkeypatch, [500, 429, 200])
    asyncio.run(http.fetch_async(session, "GET", "u"))
    stats = http.retry_stats()
    assert stats["5xx"] == 1, stats
    assert stats["429-ratelimit"] == 1, stats
    assert "network" not in stats


def test_async_does_not_retry_400_by_default(monkeypatch):
    _warp(monkeypatch)
    session, calls = _astub(monkeypatch, [400, 200])
    assert asyncio.run(http.fetch_async(session, "GET", "u")).status_code == 400
    assert len(calls) == 1


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
    # The curve, not the header: its first step jittered into [0.75, 2.25], never the 0 asked for.
    assert len(slept) == 1 and 0.75 <= slept[0] <= 2.25, slept


def test_http_date_retry_after_falls_back_to_the_backoff_curve(monkeypatch):
    slept: list[float] = []
    _stub(monkeypatch, [(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), 200])
    monkeypatch.setattr(http.time, "sleep", slept.append)
    http.fetch("GET", "u")
    assert len(slept) == 1 and 0.75 <= slept[0] <= 2.25, slept


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

    `rotate` is stubbed to False — "no fresh IP" — for a harder reason than speed: unstubbed it is
    the *live* function, which shells out to `sudo -n` and restarts the machine's actual WARP
    daemon. Measured: WARP's pid moved 96855 -> 97119 during one test. Where sudo needs a password
    that call fails in milliseconds and every test passes by accident; where it does not, they
    bounce the developer's tunnel and then fail, because a rotation that *succeeds* hands the
    caller back an attempt and the canned outcome list runs out.

    Autouse, so it holds for the whole file rather than only the tests that go through `_warp` —
    several stub `proxy_url` themselves, and an opt-in guard would reopen this the first time one
    of those walls. Tests that are *about* rotation override it locally.
    """
    monkeypatch.setattr(http.spare_egress, "_ROTATION_COOLDOWN", 0.0)
    monkeypatch.setattr(http.spare_egress, "rotate", lambda board=None, **_: False)
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
    # no fresh IP (the file-wide default), so the retry budget stays at its base 3 — this test is
    # about what the counters record, not about the earned-attempt policy
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
        lambda board=None, **_: (
            rotations.append(board),
            True,
        )[1],
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
        lambda board=None, **_: (
            rotations.append(board),
            True,
        )[1],
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
        http.spare_egress,
        "rotate",
        lambda board=None, **_: rotations.append(board) or True,
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
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None, **_: True,
    )
    calls = _stub(monkeypatch, [429, 429, 429, 429, 200])

    resp = http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert resp.status_code == 200
    assert len(calls) == 5  # 3 base attempts, plus the 2 the rotations earned


def test_the_earned_attempts_are_capped(monkeypatch):
    """A Board every IP refuses would otherwise retry forever, one rotation at a time."""
    _warp(monkeypatch)
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None, **_: True,
    )
    calls = _stub(monkeypatch, [429] * 8)

    resp = http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert resp.status_code == 429
    assert len(calls) == http._ATTEMPTS + http._MAX_EARNED_ATTEMPTS == 5
    # The marking guarantee has a *rotating* variant, and this is the only test that reaches it:
    # under rotation the last attempt is the 5th, not the 3rd, and a wall there is exactly as
    # informative about the origin as an early one. Everything else asserting this runs with the
    # file-wide `rotate -> False`, so without this line the extended-budget path is unpinned.
    assert http.spare_egress.walled_groups() == frozenset({"workday"})


def test_no_fresh_ip_earns_nothing(monkeypatch):
    """`rotate` returns False when no fresh IP came back — the caller waited the cap out and is
    still on the spent route. Crediting it would turn a hard wall into an unbounded retry loop."""
    _warp(monkeypatch)
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None, **_: False,
    )
    calls = _stub(monkeypatch, [429] * 5)

    http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert len(calls) == http._ATTEMPTS  # the base budget, unextended


def test_the_board_that_spent_the_ip_is_named_to_rotate(monkeypatch):
    """Attribution the shard report could not make before: which Boards drank the IP supply."""
    _warp(monkeypatch)
    named = []
    monkeypatch.setattr(
        http.spare_egress,
        "rotate",
        lambda board=None, **_: named.append(board) or True,
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


# --- a connection we severed ourselves is not the request's fault ---------------------------------


def test_a_connection_our_rotation_severed_earns_its_attempt_back(monkeypatch):
    """The restart tore down the tunnel this request was riding. The origin never got a say, so
    the attempt that died with it should not count against the request's budget."""
    _warp(monkeypatch)
    state = {"n": 0}
    monkeypatch.setattr(http.spare_egress, "generation", lambda: state["n"])
    monkeypatch.setattr(
        http.spare_egress, "proxy_for", lambda g: "socks5://127.0.0.1:40000"
    )
    calls = []

    class _Session:
        def request(self, method, url, **kwargs):
            calls.append(url)
            state["n"] += 1  # a peer rotated while this request was in flight
            if len(calls) <= http._ATTEMPTS:
                raise _err(None)
            return _Resp(200)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(http.time, "sleep", lambda *a: None)

    resp = http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert resp.status_code == 200
    assert len(calls) == http._ATTEMPTS + 1  # the refund bought exactly one more


def test_a_direct_request_earns_nothing_from_someone_elses_rotation(monkeypatch):
    """WARP runs in proxy mode, so restarting it cannot sever a connection that never went through
    it — but the rotation counter is process-global and moves for every ATS at once. Without the
    `proxied` gate the common direct request claims a free attempt off an unrelated ATS."""
    state = {"n": 0}
    monkeypatch.setattr(http.spare_egress, "generation", lambda: state["n"])
    calls = []

    class _Session:
        def request(self, method, url, **kwargs):
            calls.append(url)
            state["n"] += 1  # some other ATS rotated; nothing to do with this request
            raise _err(None)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(http.time, "sleep", lambda *a: None)

    with pytest.raises(http.RequestsError):
        http.fetch("GET", "u")  # direct: no egress_group, so no proxy

    assert len(calls) == http._ATTEMPTS


def test_a_connection_error_with_no_rotation_earns_nothing(monkeypatch):
    """Without a rotation the error is the network's, not ours — the budget stands unchanged."""
    _warp(monkeypatch)
    monkeypatch.setattr(http.spare_egress, "generation", lambda: 7)
    monkeypatch.setattr(
        http.spare_egress, "proxy_for", lambda g: "socks5://127.0.0.1:40000"
    )
    calls = _stub(monkeypatch, [_err(None)] * 8)

    with pytest.raises(http.RequestsError):
        http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert len(calls) == http._ATTEMPTS


def test_rotation_severed_refunds_are_capped(monkeypatch):
    """Rotations that keep landing mid-request must still run the budget out."""
    _warp(monkeypatch)
    state = {"n": 0}
    monkeypatch.setattr(http.spare_egress, "generation", lambda: state["n"])
    monkeypatch.setattr(
        http.spare_egress, "proxy_for", lambda g: "socks5://127.0.0.1:40000"
    )
    calls = []

    class _Session:
        def request(self, method, url, **kwargs):
            calls.append(url)
            state["n"] += 1  # every single request is crossed by a rotation
            raise _err(None)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(http.time, "sleep", lambda *a: None)

    with pytest.raises(http.RequestsError):
        http.fetch("GET", "u", egress_group="workday", egress_on=frozenset({429}))

    assert len(calls) == http._ATTEMPTS + http._MAX_EARNED_ATTEMPTS


def test_the_async_path_refunds_a_severed_connection_too(monkeypatch):
    """`fetch_async` hand-duplicates the sync loop, which is exactly where the two drift apart."""
    _warp(monkeypatch)
    state = {"n": 0}
    monkeypatch.setattr(http.spare_egress, "generation", lambda: state["n"])

    async def _route(_g):
        return "socks5://127.0.0.1:40000"

    monkeypatch.setattr(http.spare_egress, "proxy_for_async", _route)
    calls = []

    class _AsyncSession:
        async def request(self, method, url, **kwargs):
            calls.append(url)
            state["n"] += 1
            if len(calls) <= http._ATTEMPTS:
                raise _err(None)
            return _Resp(200)

    async def _sleep(*_a):
        return None

    monkeypatch.setattr(http.asyncio, "sleep", _sleep)

    resp = asyncio.run(
        http.fetch_async(
            _AsyncSession(),
            "GET",
            "u",
            egress_group="workday",
            egress_on=frozenset({429}),
        )
    )

    assert resp.status_code == 200
    assert len(calls) == http._ATTEMPTS + 1


def test_a_proxied_request_is_counted_as_riding_the_tunnel(monkeypatch):
    """The drain in `spare_egress.rotate` is inert unless the request marks itself in flight.

    Without this the counter is always zero, every rotation reads an empty tunnel and restarts
    straight through the requests it is about to sever — which is the bug the drain exists for
    (`experiment/workday-rotation-severed-pages/`).
    """
    from headstart import spare_egress

    seen: list[int] = []

    class _Session:
        def request(self, method, url, **kwargs):
            seen.append(spare_egress.in_flight_count())
            return _Resp(200)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(
        spare_egress, "proxy_for", lambda g: "socks5h://127.0.0.1:40000"
    )

    http.fetch("GET", "u", egress_group="workday")
    assert seen == [1], "a proxied request must be counted while it is on the wire"
    assert spare_egress.in_flight_count() == 0, "and released once it lands"


def test_a_direct_request_is_not_counted_as_riding_the_tunnel(monkeypatch):
    """A restart cannot sever a connection that never went through the proxy, so counting one
    would make every rotation wait on traffic it is not about to break."""
    from headstart import spare_egress

    seen: list[int] = []

    class _Session:
        def request(self, method, url, **kwargs):
            seen.append(spare_egress.in_flight_count())
            return _Resp(200)

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(spare_egress, "proxy_for", lambda g: None)

    http.fetch("GET", "u", egress_group="workday")
    assert seen == [0]


def test_the_async_path_also_rides_the_tunnel(monkeypatch):
    """Workday's detail pass and its fanned-out listing pages both run on `fetch_async`; that is
    the larger share of the traffic a restart severs, so it cannot be left out."""
    from headstart import spare_egress

    seen: list[int] = []

    class _Session:
        async def request(self, method, url, **kwargs):
            seen.append(spare_egress.in_flight_count())
            return _Resp(200)

    async def _route(_g):
        return "socks5h://127.0.0.1:40000"

    monkeypatch.setattr(spare_egress, "proxy_for_async", _route)
    asyncio.run(http.fetch_async(_Session(), "GET", "u", egress_group="workday"))
    assert seen == [1]
    assert spare_egress.in_flight_count() == 0


def test_the_backoff_curve_is_jittered_so_a_severed_cohort_does_not_retry_in_lockstep(
    monkeypatch,
):
    """Every stream on the tunnel dies at the same instant when it restarts. With a bare
    `1.5 * (attempt + 1)` curve they then retried at the same instant too, so the next restart
    caught the whole cohort again — measured as 12 severed per rotation, the exact walled stream
    width. Jitter spreads them.

    An honoured `Retry-After` is never jittered: the host named a window and guessing around it is
    what the header exists to stop.
    """
    delays = {http._note_retry("GET", "u", 0, 3, "why", None, 429) for _ in range(40)}
    assert len(delays) > 1, "a fixed curve retries a severed cohort in lockstep"
    assert all(0.75 <= d <= 2.25 for d in delays), sorted(delays)[:3]

    honoured = {http._note_retry("GET", "u", 0, 3, "why", 7.0, 429) for _ in range(10)}
    assert honoured == {7.0}, "a host-supplied window must be taken literally"


def test_resolving_a_route_never_stalls_the_event_loop_during_a_rotation(monkeypatch):
    """A closed rotation gate must not freeze the loop, or the drain can never finish.

    `fetch_async` used to call the blocking `proxy_for`, whose `_gate.wait` holds for the length of
    a rotation. On the event loop that froze every request already riding the tunnel: their
    `riding_the_tunnel` blocks could not exit, `_inflight` could not fall, and `_drain` therefore
    waited out its whole `_DRAIN_CAP` and restarted through the requests it existed to protect.
    Measured on the harness, that cost 18-23 of 48 pages and 122-149 severed connections per crawl;
    with the loop free, both are 0.

    Driven through `fetch_async` rather than the resolver, because the defect was *which* resolver
    the call site reached for — a test on `proxy_for_async` alone stays green when someone switches
    that line back. The assertion is the symptom: other coroutines keep being scheduled while a
    request waits on a closed gate.
    """
    from headstart import spare_egress

    _warp(monkeypatch)
    monkeypatch.setattr(spare_egress, "proxy_url", lambda: "socks5h://127.0.0.1:40000")

    class _Session:
        async def request(self, method, url, **kwargs):
            return _Resp(200)

    # Walled, or the resolver short-circuits to the direct route and never reaches the gate —
    # which is what made the first draft of this test pass with the fix reverted.
    monkeypatch.setattr(spare_egress, "_walled", {"workday"})
    spare_egress._gate.clear()
    try:

        async def _drive():
            ticks = 0

            async def heartbeat():
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.01)

            beat = asyncio.create_task(heartbeat())
            call = asyncio.create_task(
                http.fetch_async(_Session(), "GET", "u", egress_group="workday")
            )
            await asyncio.sleep(0.3)
            opened = ticks
            spare_egress._gate.set()
            await call
            beat.cancel()
            return opened

        # ~30 ticks if the loop runs; exactly 1 if the gate wait blocked it.
        assert asyncio.run(_drive()) > 5
    finally:
        spare_egress._gate.set()
