"""How the Space MCP server reaches the Space — `headstart/space_mcp/space_client.py`.

Every case is a scripted sequence of replies through the `Fetch` port and a fake clock, so the
deadline, the waits and the "who answered" rule are measured rather than slept through.
"""

from __future__ import annotations

import json
import logging

import pytest

from headstart.space_mcp import space_client as sc

APP = {"x-headstart": f"app; agent-api={sc.AGENT_API}"}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Script:
    """A `Fetch` that answers each request with the next scripted step: a `Reply`, an exception
    to raise, or a number of seconds the request takes before timing out."""

    def __init__(self, clock: Clock, *steps):
        self.clock = clock
        self.steps = list(steps)
        self.urls: list[str] = []
        self.headers: list[dict] = []
        self.timeouts: list[float] = []

    def __call__(self, url, headers, timeout_s):
        self.urls.append(url)
        self.headers.append(dict(headers))
        self.timeouts.append(timeout_s)
        step = self.steps.pop(0)
        if isinstance(step, (int, float)):
            self.clock.now += min(step, timeout_s)
            raise TimeoutError("timed out")
        if isinstance(step, BaseException):
            raise step
        return step


def _reply(status=200, body=None, headers=APP):
    return sc.Reply(
        status, dict(headers), json.dumps(body).encode() if body is not None else b""
    )


def _client(clock, fetch, **kwargs):
    return sc.SpaceClient(fetch=fetch, clock=clock, sleep=clock.sleep, **kwargs)


def test_an_app_answer_is_its_json_and_the_request_carries_no_credential():
    clock = Clock()
    fetch = Script(clock, _reply(body={"companies": []}))
    got = _client(clock, fetch).read(
        sc.SpaceRoute.COMPANIES_SUGGEST, [("q", "stripe"), ("limit", "8")]
    )
    assert got == {"companies": []}
    assert fetch.urls == [f"{sc.SPACE_URL}/companies/suggest?q=stripe&limit=8"]
    assert "Authorization" not in fetch.headers[0]


def test_repeated_keys_stay_repeated():
    clock = Clock()
    fetch = Script(clock, _reply(body=[]))
    _client(clock, fetch).read(
        sc.SpaceRoute.SEARCH, [("board", "workday:a/1"), ("board", "workday:a/2")]
    )
    assert fetch.urls[0].endswith("?board=workday%3Aa%2F1&board=workday%3Aa%2F2")


def test_only_the_listed_routes_can_be_read():
    clock = Clock()
    client = _client(clock, Script(clock))
    with pytest.raises(ValueError):
        client.read("/sets")


@pytest.mark.parametrize(
    ("status", "body", "error", "words"),
    [
        (
            400,
            {"error": "invalid filter", "detail": "ats 'x' is not served"},
            sc.InvalidRequest,
            "ats 'x'",
        ),
        (
            400,
            {"error": "unknown company: greenhouse:nobody"},
            sc.InvalidRequest,
            "unknown company",
        ),
        (401, {"error": "sign in first"}, sc.SpaceTooOld, "still asks for sign-in"),
        (503, {"error": "no trend data yet"}, sc.NotOnDeployment, "no trend data yet"),
        (500, None, sc.SpaceFailed, "HTTP 500"),
    ],
)
def test_the_apps_own_refusals_are_answered_at_once(status, body, error, words):
    clock = Clock()
    fetch = Script(clock, _reply(status, body))
    with pytest.raises(error, match=words):
        _client(clock, fetch).read(sc.SpaceRoute.TRENDS)
    assert len(fetch.urls) == 1 and clock.slept == []


@pytest.mark.parametrize("status", [403, 404, 502, 503])
def test_an_edge_reply_is_retried_until_the_app_answers(status):
    """HF's edge answers a booting or sleeping Space with its own statuses — 403 and 404 among
    them (`tests/test_alerts_space_query.py`) — and never carries the app's marker."""
    clock = Clock()
    fetch = Script(
        clock,
        _reply(status, None, headers={"content-type": "text/html"}),
        _reply(body=[]),
    )
    assert _client(clock, fetch).read(sc.SpaceRoute.SEARCH) == []
    assert clock.slept == [5.0]


def test_no_answer_at_all_is_retried_as_a_waking_space():
    clock = Clock()
    fetch = Script(clock, ConnectionRefusedError("refused"), 20, _reply(body=[]))
    assert _client(clock, fetch).read(sc.SpaceRoute.SEARCH) == []
    assert clock.slept == [5.0, 10.0]


def test_the_deadline_ends_the_wait_with_the_measured_boot():
    clock = Clock()
    edge = _reply(503, None, headers={})
    fetch = Script(clock, *[edge] * 50)
    with pytest.raises(sc.SpaceWaking, match="4 min 13 s"):
        _client(clock, fetch).read(sc.SpaceRoute.HOT)
    assert clock.now <= 90.0
    assert all(timeout <= 20.0 for timeout in fetch.timeouts)


def test_a_timeout_after_the_app_answered_in_this_call_is_a_failure_not_a_boot():
    clock = Clock()
    fetch = Script(clock, _reply(body={"companies": []}), 20)
    client = _client(clock, fetch)
    client.read(sc.SpaceRoute.COMPANIES_SUGGEST)
    with pytest.raises(sc.SpaceFailed, match="stopped answering"):
        client.read(sc.SpaceRoute.TRENDS)
    assert clock.slept == []


def test_an_app_older_than_the_agent_contract_stops_the_call():
    """An older Space would ignore `strict=1` and answer the widest possible search."""
    clock = Clock()
    fetch = Script(clock, _reply(body=[], headers={"x-headstart": "app; agent-api=0"}))
    with pytest.raises(sc.SpaceTooOld, match="deploy main"):
        _client(clock, fetch).read(sc.SpaceRoute.SEARCH)


def test_an_app_404_on_a_route_this_server_needs_is_an_older_space():
    clock = Clock()
    fetch = Script(clock, _reply(404, None))
    with pytest.raises(sc.SpaceTooOld):
        _client(clock, fetch).read(sc.SpaceRoute.COMPANIES_LOOKUP)


def test_an_app_from_before_the_marker_is_recognised_by_its_json_401():
    clock = Clock()
    fetch = Script(clock, _reply(401, {"error": "sign in first"}, headers={}))
    with pytest.raises(sc.SpaceTooOld, match="predates the public read routes"):
        _client(clock, fetch).read(sc.SpaceRoute.SEARCH)
    assert clock.slept == []


def test_the_request_budget_refuses_past_its_limit_and_frees_after_the_window():
    clock = Clock()
    budget = sc.RequestBudget(limit=2, window_s=60, clock=clock)
    budget.take()
    budget.take()
    with pytest.raises(sc.RateLimited, match="2 HeadStart requests"):
        budget.take()
    clock.now += 60
    budget.take()


def test_logs_name_the_route_never_a_parameter(caplog):
    caplog.set_level(logging.DEBUG, logger=sc.__name__)
    clock = Clock()
    fetch = Script(clock, _reply(503, None, headers={}), _reply(body=[]))
    _client(clock, fetch).read(sc.SpaceRoute.SEARCH, [("q", "secret search words")])
    assert "/search" in caplog.text and "secret" not in caplog.text


def test_the_client_asks_for_gzip_and_unzips_what_the_space_zipped():
    """The Space gzips an answer for a client that asks (ADR-0251); a Trends answer is ~400 kB
    unzipped. What arrives zipped is unzipped before it is read."""
    import gzip

    body = json.dumps({"reading": None}).encode()
    assert sc._decoded({"content-encoding": "gzip"}, gzip.compress(body)) == body
    assert sc._decoded({}, body) == body
    clock = Clock()
    fetch = Script(clock, _reply(body={}))
    _client(clock, fetch).read(sc.SpaceRoute.TRENDS)
    assert fetch.headers[0]["Accept-Encoding"] == "gzip"
