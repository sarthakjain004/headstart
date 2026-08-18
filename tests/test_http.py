"""Tests for the reliable-fetch seam (headstart.http.fetch).

The retry/backoff/transient-classification policy was untested when it lived copy-pasted inside
each scraper's loop; now it lives once, so it's tested once. The session is stubbed (no network)
and sleep is neutralized, so each test asserts exactly what fetch retries, what it gives up on,
and what it hands back for the caller to classify.
"""

import logging

import pytest

import headstart.http as http


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
# stub `warp.proxy_url` rather than dialling anything, so what is asserted is the routing decision
# — which request carries a proxy and which does not — not WARP itself (see test_warp.py).


@pytest.fixture(autouse=True)
def _clean_egress():
    """Walled groups are process-global; leaking one would make later tests route unexpectedly."""
    http.reset_walled()
    yield
    http.reset_walled()


def _warp(monkeypatch, url="socks5://127.0.0.1:40000"):
    monkeypatch.setattr(http.warp, "proxy_url", lambda: url)


def _proxied(calls):
    """Which of the recorded calls carried a proxy, as a list of bools."""
    return ["proxies" in kwargs for _, _, kwargs in calls]


def test_without_egress_group_a_wall_changes_nothing(monkeypatch):
    # every ATS that has not opted in: 403 is still just a retryable blip, and no route moves
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    assert http.fetch("GET", "u").status_code == 200
    assert _proxied(calls) == [False, False]
    assert http.walled_groups() == frozenset()


def test_wall_marks_the_group_and_routes_the_retry(monkeypatch):
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    assert http.fetch("GET", "u", egress_group="eightfold").status_code == 200
    assert http.walled_groups() == frozenset({"eightfold"})
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
    http.fetch("GET", "board-1", egress_group="eightfold")
    http.fetch("GET", "board-2", egress_group="eightfold")
    assert _proxied(calls) == [False, True, True]


def test_wall_on_the_final_attempt_still_marks(monkeypatch):
    # this request is lost either way, but it is exactly as informative about the origin as an
    # early wall — not recording it would make every later Board repeat the same three attempts
    _warp(monkeypatch)
    _stub(monkeypatch, [405, 405, 405])
    assert http.fetch("GET", "u", egress_group="eightfold").status_code == 405
    assert http.walled_groups() == frozenset({"eightfold"})


def test_transient_but_non_wall_status_does_not_move_egress(monkeypatch):
    # a 500 is the origin failing, not the origin refusing this IP; spending a second budget on it
    # would buy nothing
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [500, 200])
    http.fetch("GET", "u", egress_group="eightfold")
    assert http.walled_groups() == frozenset()
    assert _proxied(calls) == [False, False]


def test_custom_egress_on_is_respected(monkeypatch):
    # the opt-in carries its own statuses, so a future ATS walled by 429 does not have to accept
    # Eightfold's 403/405 definition
    _warp(monkeypatch)
    calls = _stub(monkeypatch, [403, 200])
    http.fetch("GET", "u", egress_group="other", egress_on=frozenset({429}))
    assert http.walled_groups() == frozenset()
    assert _proxied(calls) == [False, False]


def test_no_warp_available_stays_on_the_direct_route(monkeypatch):
    # the degradation that makes this safe to ship: a runner without WARP behaves as it does today
    _warp(monkeypatch, url=None)
    calls = _stub(monkeypatch, [403, 403, 403])
    assert http.fetch("GET", "u", egress_group="eightfold").status_code == 403
    assert http.walled_groups() == frozenset(
        {"eightfold"}
    )  # still recorded, for the log line
    assert _proxied(calls) == [False, False, False]
