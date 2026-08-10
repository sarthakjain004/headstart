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
    def __init__(self, status_code):
        self.status_code = status_code


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
            calls.append((method, url))
            if isinstance(outcome, BaseException):
                raise outcome
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
