"""Tests for scripts/eval/space_mcp_edge_retry.py: the endpoint that fails a run's first tool calls
with Hugging Face's edge page, the outcome it reads from a transcript, and the two arms'
instructions. Nothing here starts Claude Code or reaches the Space."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_mcp_protocol_messages import _call_message, _server

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "eval" / "space_mcp_edge_retry.py"
)


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("space_mcp_edge_retry", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _post(url, message):
    request = urllib.request.Request(
        url,
        json.dumps(message).encode(),
        {"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_the_first_tool_calls_of_a_run_meet_the_edge_page_and_the_rest_are_answered(
    probe,
):
    endpoint = probe.EdgeFailingEndpoint(_server(), failures=2)
    try:
        listed = _post(
            endpoint.url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        calls = [
            _post(endpoint.url, _call_message("echo", {"word": "hi"})) for _ in range(3)
        ]
    finally:
        endpoint.close()

    assert listed[0] == 200  # only tool calls fail
    for status, headers, body in calls[:2]:
        assert status == 502 and body == probe.EDGE_PAGE
        assert headers["Content-Type"] == "text/html"
        assert "X-HeadStart" not in headers  # the edge's reply, not the app's
    assert calls[2][0] == 200 and "result" in json.loads(calls[2][2])
    assert endpoint.posts == [
        {"tool": None, "failed": False},
        {"tool": "echo", "failed": True},
        {"tool": "echo", "failed": True},
        {"tool": "echo", "failed": False},
    ]


def test_the_edge_page_is_the_one_a_real_502_carried(probe):
    page = probe.EDGE_PAGE.decode()

    assert page.startswith("<!DOCTYPE html>")
    assert "<h1>500</h1>" in page and "Sorry, there is an error on our side." in page


def _calls(probe, *steps):
    """Tool calls: (name, answered) or (name, "edge") for one that met the edge page."""
    ev = probe.space_mcp_eval
    page = probe.EDGE_PAGE.decode()
    return [
        ev.ToolCall(name, {}, page, True)
        if how == "edge"
        else ev.ToolCall(name, {}, "an answer", not how)
        for name, how in steps
    ]


@pytest.mark.parametrize(
    "steps, said",
    [
        ([("find_company", "edge"), ("find_company", "edge"), ("find_company", True)],
         "recovered by retrying"),
        ([("find_company", "edge"), ("find_company", "edge"), ("search_jobs", True)],
         "recovered elsewhere"),
        ([("hiring_now", "edge"), ("hiring_now", "edge")], "gave up"),
        ([("hiring_now", "edge"), ("hiring_now", False)], "gave up"),  # refused, not answered
        ([], "no failure met"),
    ],
)  # fmt: skip
def test_outcome(probe, steps, said):
    assert probe.outcome(_calls(probe, *steps)) == said


def test_the_arms_differ_only_by_the_retry_sentence(probe):
    before, after = probe.instructions("before"), probe.instructions("after")
    sentence = probe.space_server._INSTRUCTIONS_EDGE_RETRY

    assert sentence in after and sentence not in before
    assert after.replace(" " + sentence, "") == before
