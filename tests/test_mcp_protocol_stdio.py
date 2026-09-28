"""The JSON-RPC loop every HeadStart MCP server shares — `headstart/mcp_protocol/stdio.py`.

A stand-in server with two tools exercises the loop itself: the version it answers, how it tells
a protocol fault from a tool failure, and what it logs. What one real server does through it is
tested beside that server (`tests/test_resume_mcp.py`), which still reaches the loop through its
own `handle` and `serve`.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from headstart.mcp_protocol import stdio

LOGGER = "headstart.test_mcp_protocol_stdio"

TOOLS = [
    {
        "name": "echo",
        "title": "Echo",
        "annotations": {"readOnlyHint": True},
        "description": "Answers with its word.",
        "inputSchema": {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "refuse",
        "description": "Always refuses.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call(name, arguments):
    if name == "refuse":
        raise stdio.ToolFailure("no, and here is why")
    if arguments.get("word") == "crash":
        raise RuntimeError("tool broke")
    return f"echo {arguments.get('word')}"


def _server(**overrides) -> stdio.Server:
    fields = {
        "name": "test-server",
        "version": "9.9.9",
        "tools": TOOLS,
        "call": _call,
        "log": logging.getLogger(LOGGER),
        "instructions": "Use echo to hear a word back.",
    }
    fields.update(overrides)
    return stdio.Server(**fields)


def _call_message(name, arguments, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _records(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


# ---- the handshake ----------------------------------------------------------------------


@pytest.mark.parametrize("asked", stdio.SUPPORTED_VERSIONS)
def test_a_version_the_loop_speaks_is_echoed(asked):
    hello = stdio.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": asked},
        },
        _server(),
    )
    assert hello["result"]["protocolVersion"] == asked


@pytest.mark.parametrize("params", [{}, {"protocolVersion": "2031-01-01"}, None, [1]])
def test_a_missing_or_unknown_version_is_answered_with_the_newest(params):
    message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    if params is not None:
        message["params"] = params
    hello = stdio.handle(message, _server())
    assert hello["result"]["protocolVersion"] == stdio.NEWEST == "2025-11-25"


def test_initialize_names_the_server_its_capability_and_its_instructions():
    result = stdio.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, _server()
    )["result"]
    assert result["serverInfo"] == {"name": "test-server", "version": "9.9.9"}
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["instructions"] == "Use echo to hear a word back."
    bare = stdio.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        _server(instructions=None),
    )["result"]
    assert "instructions" not in bare


def test_a_2026_client_probing_with_server_discover_is_told_to_fall_back():
    """2026-07-28 has a client probe with `server/discover` and fall back to `initialize` on an
    error it does not recognise as a modern one; an unknown method is exactly that."""
    probe = stdio.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover"}, _server()
    )
    assert probe["error"]["code"] == -32601


def test_tools_are_listed_as_given_in_order_with_their_titles_and_annotations():
    listed = stdio.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _server()
    )
    assert listed["result"]["tools"] == TOOLS


def test_ping_answers_and_a_notification_gets_no_reply():
    assert stdio.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"}, _server()) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {},
    }
    assert (
        stdio.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}, _server())
        is None
    )


# ---- protocol faults and tool failures --------------------------------------------------


def test_an_unknown_tool_is_a_protocol_error():
    """2025-11-25 lists unknown tools among protocol errors: there is no argument for the
    model to correct, only the name."""
    answer = stdio.handle(_call_message("nope", {}), _server())
    assert answer["error"]["code"] == -32602
    assert "nope" in answer["error"]["message"]


def test_a_refusal_is_a_result_the_model_can_read():
    answer = stdio.handle(_call_message("refuse", {}), _server())
    assert "error" not in answer
    assert answer["result"] == {
        "content": [{"type": "text", "text": "no, and here is why"}],
        "isError": True,
    }


def test_an_answer_is_one_text_block():
    answer = stdio.handle(_call_message("echo", {"word": "hi"}), _server())
    assert answer["result"] == {"content": [{"type": "text", "text": "echo hi"}]}


def test_an_unconfigured_server_lists_its_tools_and_explains_every_call():
    reason = RuntimeError("set TEST_TOKEN")
    server = _server(unconfigured=reason)
    listed = stdio.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, server)
    assert listed["result"]["tools"] == TOOLS
    called = stdio.handle(_call_message("echo", {"word": "hi"}), server)
    assert called["result"]["isError"] is True
    assert called["result"]["content"][0]["text"] == "set TEST_TOKEN"


def test_a_crash_inside_a_tool_is_a_result_and_its_stack_goes_to_the_servers_log(
    caplog,
):
    answer = stdio.handle(_call_message("echo", {"word": "crash"}), _server())
    assert answer["result"]["isError"] is True
    assert answer["result"]["content"][0]["text"] == "RuntimeError: tool broke"
    [record] = _records(caplog)
    assert record.levelname == "ERROR" and record.exc_info is not None
    assert "['word']" in record.getMessage() and "crash" not in record.getMessage()


def test_every_call_leaves_one_debug_line_naming_the_outcome_not_the_values(caplog):
    caplog.set_level("DEBUG", logger=LOGGER)
    stdio.handle(_call_message("refuse", {}), _server())
    stdio.handle(_call_message("echo", {"word": "secret-word"}), _server())
    lines = [r.getMessage() for r in _records(caplog)]
    assert len(lines) == 2
    assert "refuse -> refused" in lines[0] and "echo -> ok" in lines[1]
    assert "secret-word" not in " ".join(lines)


@pytest.mark.parametrize("params", [[1, 2], {"name": "echo", "arguments": [1]}, "text"])
def test_malformed_params_are_refused_and_logged_by_type_only(caplog, params):
    caplog.set_level("INFO", logger=LOGGER)
    stdout = io.StringIO()
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
    stdio.serve(io.StringIO(json.dumps(message) + "\n"), stdout, _server())
    assert json.loads(stdout.getvalue())["error"]["code"] == -32602
    [record] = _records(caplog)
    assert record.levelname == "INFO" and str(params) not in record.getMessage()


# ---- the stdio loop ---------------------------------------------------------------------


def test_a_bug_in_handle_answers_an_internal_error_and_serving_continues(
    monkeypatch, caplog
):
    """Moved from `tests/test_resume_mcp.py` with the loop it tests (ADR-0137 amendment)."""
    monkeypatch.setattr(stdio, "_result", lambda *a: 1 / 0)
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "nope"})
        + "\n"
    )
    stdout = io.StringIO()
    stdio.serve(stdin, stdout, _server())
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies] == [-32603, -32601]
    [record] = _records(caplog)
    assert record.levelname == "ERROR" and record.exc_info is not None


def test_each_line_is_answered_in_order_and_blank_lines_are_skipped():
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps(_call_message("echo", {"word": "hi"}, request_id=2))
        + "\n"
    )
    stdout = io.StringIO()
    stdio.serve(stdin, stdout, _server())
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]


def test_an_unparseable_line_and_a_non_object_are_answered_and_logged_without_content(
    caplog,
):
    caplog.set_level("INFO", logger=LOGGER)
    stdout = io.StringIO()
    stdio.serve(io.StringIO('{not json\n["private words"]\n'), stdout, _server())
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies] == [-32700, -32600]
    assert "private words" not in caplog.text and "list" in caplog.text
