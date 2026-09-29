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


#: What one tool call met: the edge page, an answer, or a refusal of its arguments.
EDGE, ANSWERED, REFUSED = "edge", "answered", "refused"


def _calls(probe, *steps):
    """Tool calls from (tool name, what the call met) pairs."""
    tool_call = probe.space_mcp_eval.ToolCall
    result = {
        EDGE: probe.EDGE_PAGE.decode(),
        ANSWERED: "an answer",
        REFUSED: "a refusal",
    }
    return [tool_call(name, {}, result[met], met != ANSWERED) for name, met in steps]


@pytest.mark.parametrize(
    "steps, said",
    [
        ([("find_company", EDGE), ("find_company", EDGE), ("find_company", ANSWERED)],
         "recovered by retrying"),
        # After-arm t16 on 2026-09-29: the retry was refused, and a later search answered.
        ([("search_jobs", EDGE), ("search_jobs", EDGE), ("search_jobs", REFUSED),
          ("search_jobs", ANSWERED)], "recovered by retrying"),
        ([("find_company", EDGE), ("find_company", EDGE), ("search_jobs", ANSWERED)],
         "recovered elsewhere"),
        ([("hiring_now", EDGE), ("hiring_now", EDGE)], "gave up"),
        ([("hiring_now", EDGE), ("hiring_now", REFUSED)], "gave up"),
        ([], "no failure met"),
    ],
)  # fmt: skip
def test_outcome(probe, steps, said):
    assert probe.outcome(_calls(probe, *steps)) == said


def test_the_after_arm_serves_the_shipped_instructions_and_before_drops_one_sentence(
    probe,
):
    """The arms differ only by the retry sentence, and the endpoint serves the arm's text."""
    before, after = probe.instructions("before"), probe.instructions("after")
    sentence = probe.space_server._INSTRUCTIONS_EDGE_RETRY

    assert after == probe.space_server.INSTRUCTIONS
    assert sentence in after and sentence not in before
    assert after.replace(" " + sentence, "") == before
