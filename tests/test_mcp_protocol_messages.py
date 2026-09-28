"""One JSON-RPC message in, one out, in either protocol era — `headstart/mcp_protocol/messages.py`.

A stand-in server with two tools exercises `handle` itself: the versions it answers, how it tells
a protocol fault from a tool failure, and what it logs. What one real server does through it is
tested beside that server (`tests/test_resume_mcp.py`, `tests/test_space_mcp_server.py`). The
two transports are tested in `tests/test_mcp_protocol_stdio.py` and
`tests/test_mcp_protocol_streamable_http.py`, which reuse this file's stand-in.
"""

from __future__ import annotations

import logging

import pytest

from headstart.mcp_protocol import messages

LOGGER = "headstart.test_mcp_protocol_messages"

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
        raise messages.ToolFailure("no, and here is why")
    if arguments.get("word") == "crash":
        raise RuntimeError("tool broke")
    return f"echo {arguments.get('word')}"


def _server(**overrides) -> messages.Server:
    fields = {
        "name": "test-server",
        "version": "9.9.9",
        "tools": TOOLS,
        "call": _call,
        "log": logging.getLogger(LOGGER),
        "instructions": "Use echo to hear a word back.",
    }
    fields.update(overrides)
    return messages.Server(**fields)


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


@pytest.mark.parametrize("asked", messages.LEGACY_VERSIONS)
def test_a_version_the_loop_speaks_is_echoed(asked):
    hello = messages.handle(
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
    hello = messages.handle(message, _server())
    assert hello["result"]["protocolVersion"] == messages.NEWEST_LEGACY == "2025-11-25"


def test_initialize_names_the_server_its_capability_and_its_instructions():
    result = messages.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, _server()
    )["result"]
    assert result["serverInfo"] == {"name": "test-server", "version": "9.9.9"}
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["instructions"] == "Use echo to hear a word back."
    bare = messages.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        _server(instructions=None),
    )["result"]
    assert "instructions" not in bare


def test_a_2026_client_probing_with_server_discover_is_told_to_fall_back():
    """2026-07-28 has a client probe with `server/discover` and fall back to `initialize` on an
    error it does not recognise as a modern one; an unknown method is exactly that."""
    probe = messages.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover"}, _server()
    )
    assert probe["error"]["code"] == -32601


def test_tools_are_listed_as_given_in_order_with_their_titles_and_annotations():
    listed = messages.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _server()
    )
    assert listed["result"]["tools"] == TOOLS


def test_ping_answers_and_a_notification_gets_no_reply():
    assert messages.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "ping"}, _server()
    ) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {},
    }
    assert (
        messages.handle(
            {"jsonrpc": "2.0", "method": "notifications/cancelled"}, _server()
        )
        is None
    )


# ---- protocol faults and tool failures --------------------------------------------------


def test_an_unknown_tool_is_a_protocol_error():
    """2025-11-25 lists unknown tools among protocol errors: there is no argument for the
    model to correct, only the name."""
    answer = messages.handle(_call_message("nope", {}), _server())
    assert answer["error"]["code"] == -32602
    assert "nope" in answer["error"]["message"]


def test_a_refusal_is_a_result_the_model_can_read():
    answer = messages.handle(_call_message("refuse", {}), _server())
    assert "error" not in answer
    assert answer["result"] == {
        "content": [{"type": "text", "text": "no, and here is why"}],
        "isError": True,
    }


def test_an_answer_is_one_text_block():
    answer = messages.handle(_call_message("echo", {"word": "hi"}), _server())
    assert answer["result"] == {"content": [{"type": "text", "text": "echo hi"}]}


def test_an_unconfigured_server_lists_its_tools_and_explains_every_call():
    reason = RuntimeError("set TEST_TOKEN")
    server = _server(unconfigured=reason)
    listed = messages.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, server
    )
    assert listed["result"]["tools"] == TOOLS
    called = messages.handle(_call_message("echo", {"word": "hi"}), server)
    assert called["result"]["isError"] is True
    assert called["result"]["content"][0]["text"] == "set TEST_TOKEN"


def test_a_crash_inside_a_tool_is_a_result_and_its_stack_goes_to_the_servers_log(
    caplog,
):
    answer = messages.handle(_call_message("echo", {"word": "crash"}), _server())
    assert answer["result"]["isError"] is True
    assert answer["result"]["content"][0]["text"] == "RuntimeError: tool broke"
    [record] = _records(caplog)
    assert record.levelname == "ERROR" and record.exc_info is not None
    assert "['word']" in record.getMessage() and "crash" not in record.getMessage()


def test_every_call_leaves_one_debug_line_naming_the_outcome_not_the_values(caplog):
    caplog.set_level("DEBUG", logger=LOGGER)
    messages.handle(_call_message("refuse", {}), _server())
    messages.handle(_call_message("echo", {"word": "secret-word"}), _server())
    lines = [r.getMessage() for r in _records(caplog)]
    assert len(lines) == 2
    assert "refuse -> refused" in lines[0] and "echo -> ok" in lines[1]
    assert "secret-word" not in " ".join(lines)


@pytest.mark.parametrize("params", [[1, 2], {"name": "echo", "arguments": [1]}, "text"])
def test_malformed_params_are_refused_and_logged_by_type_only(caplog, params):
    caplog.set_level("INFO", logger=LOGGER)
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
    assert messages.handle(message, _server())["error"]["code"] == -32602
    [record] = _records(caplog)
    assert record.levelname == "INFO" and str(params) not in record.getMessage()


# ---- the 2026-07-28 era -----------------------------------------------------------------

_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _modern(method, request_id=1, **params):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**params, "_meta": dict(_MODERN_META)},
    }


def test_server_discover_names_the_versions_the_tools_capability_and_the_server():
    result = messages.handle(_modern("server/discover"), _server())["result"]
    assert result == {
        "resultType": "complete",
        "supportedVersions": ["2026-07-28", "2025-11-25", "2025-06-18"],
        "capabilities": {"tools": {}},
        "instructions": "Use echo to hear a word back.",
        "ttlMs": 3_600_000,
        "cacheScope": "public",
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {
                "name": "test-server",
                "version": "9.9.9",
            }
        },
    }


def test_a_modern_tool_list_is_complete_cacheable_by_anyone_and_signed():
    result = messages.handle(_modern("tools/list"), _server())["result"]
    assert result["tools"] == TOOLS
    assert result["resultType"] == "complete"
    assert (result["ttlMs"], result["cacheScope"]) == (3_600_000, "public")
    assert (
        result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "test-server"
    )


def test_a_modern_tool_call_is_the_legacy_answer_marked_complete():
    answer = messages.handle(
        _modern("tools/call", name="echo", arguments={"word": "hi"}), _server()
    )["result"]
    assert answer["content"] == [{"type": "text", "text": "echo hi"}]
    assert answer["resultType"] == "complete" and "ttlMs" not in answer
    refused = messages.handle(_modern("tools/call", name="refuse"), _server())
    assert refused["result"]["isError"] is True
    unknown = messages.handle(_modern("tools/call", name="nope"), _server())
    assert unknown["error"]["code"] == -32602


def test_an_unsupported_modern_version_names_every_version_spoken():
    message = _modern("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2031-01-01"
    error = messages.handle(message, _server())["error"]
    assert error["code"] == -32022
    assert error["data"] == {
        "supported": ["2026-07-28", "2025-11-25", "2025-06-18"],
        "requested": "2031-01-01",
    }


@pytest.mark.parametrize("method", ["initialize", "ping", "resources/list"])
def test_a_method_the_modern_era_does_not_have_is_unknown(method):
    assert messages.handle(_modern(method), _server())["error"]["code"] == -32601


def test_a_modern_notification_gets_no_reply():
    message = _modern("notifications/cancelled")
    del message["id"]
    assert messages.handle(message, _server()) is None
