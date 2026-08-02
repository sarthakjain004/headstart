"""Tests for the router client (headstart.llm_router, ADR-0032).

Two properties carry the module: every failure mode collapses to ``RouterUnavailable`` (the
caller has exactly one thing to catch), and the client makes exactly ONE attempt — the router
runs its own provider-fallback chain, so a retry here would re-send a request the router is
already failing over. Stdlib urllib is monkeypatched; no network, no tunnel, runs in CI.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from headstart import llm_router


class _Response(io.BytesIO):
    """`urlopen` returns a context manager readable by ``json.load`` — BytesIO plus enter/exit."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reply(text: str) -> _Response:
    return _Response(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def test_ask_returns_the_completion_text(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        return _reply("backend engineer, fintech")

    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("LLM_ROUTER_MODEL", "agent-default")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert llm_router.ask("hello") == "backend engineer, fintech"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "agent-default"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    # Regression for the truncation bug: the router's default model spends ~600 thinking tokens
    # before the one-line answer, so a small budget comes back `finish_reason: length` as a
    # fragment ("Java, GraphQL"). The wired budget must leave room for reasoning + answer.
    assert captured["body"]["max_tokens"] == llm_router._MAX_TOKENS >= 2000


def test_unreachable_router_raises_router_unavailable(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_router.RouterUnavailable):
        llm_router.ask("hello")


def test_malformed_reply_raises_router_unavailable(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: _Response(b"not json")
    )
    with pytest.raises(llm_router.RouterUnavailable):
        llm_router.ask("hello")


def test_exactly_one_attempt_on_failure(monkeypatch):
    """The no-retry decision, asserted: the router owns fallback, so a failure here must not
    be retried into it."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_router.RouterUnavailable):
        llm_router.ask("hello")
    assert len(calls) == 1
