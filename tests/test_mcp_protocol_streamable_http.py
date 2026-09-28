"""One POST to the hosted MCP endpoint — `headstart/mcp_protocol/streamable_http.py` (ADR-0267).

Framework-free, so these pass headers and bytes straight in; the stand-in server is
`tests/test_mcp_protocol_messages.py`'s. The Space's own route is tested against the real app in
`tests/test_space_mcp_against_space_app.py`.
"""

from __future__ import annotations

import base64
import json

import pytest
from test_mcp_protocol_messages import _MODERN_META, _server

from headstart.mcp_protocol import messages, streamable_http

_ORIGINS = frozenset({"https://claude.ai", "https://claude.com"})


def _post(message, headers=None, *, server=None):
    body = message if isinstance(message, bytes) else json.dumps(message).encode()
    status, reply_headers, reply = streamable_http.answer(
        headers or {}, body, server or _server(), _ORIGINS
    )
    return status, reply_headers, json.loads(reply) if reply else None


def _modern(method, request_id=1, **params):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**params, "_meta": dict(_MODERN_META)},
    }


def _modern_headers(method, name=None):
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-11-25"},
}


# ---- the Origin gate ----------------------------------------------------------------------


@pytest.mark.parametrize("origin", [None, "https://claude.ai", "https://claude.com"])
def test_no_origin_and_claudes_origins_are_answered(origin):
    headers = {} if origin is None else {"Origin": origin}
    status, _, reply = _post(_INITIALIZE, headers)
    assert status == 200 and reply["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize(
    "origin", ["https://evil.example.com", "null", "http://claude.ai"]
)
def test_any_other_origin_is_forbidden_before_the_body_is_read(origin):
    status, _, reply = _post(b"not even json", {"origin": origin})
    assert status == 403
    assert reply["id"] is None and reply["error"]["code"] == -32600


# ---- one request per POST -----------------------------------------------------------------


def test_a_notification_is_accepted_with_no_body():
    status, headers, reply = _post(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert (status, headers, reply) == (202, {}, None)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"{not json", -32700),
        (b"[1, 2]", -32600),
        (b'{"jsonrpc": "2.0", "id": 1, "result": {}}', -32600),
    ],
)
def test_a_body_that_is_not_one_request_is_a_400(body, code):
    status, _, reply = _post(body)
    assert status == 400 and reply["error"]["code"] == code


def test_a_body_past_the_cap_is_refused():
    status, _, _ = _post(b" " * (streamable_http.MAX_BODY_BYTES + 1))
    assert status == 413


def test_a_session_id_is_ignored_and_none_is_minted():
    status, headers, _ = _post(_INITIALIZE, {"Mcp-Session-Id": "abc"})
    assert status == 200 and headers == {"Content-Type": "application/json"}


# ---- the legacy era -----------------------------------------------------------------------


def test_a_legacy_error_rides_a_200():
    status, _, reply = _post({"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
    assert status == 200 and reply["error"]["code"] == -32601


def test_a_legacy_request_after_the_handshake_carries_its_version_header():
    status, _, reply = _post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"MCP-Protocol-Version": "2025-11-25"},
    )
    assert status == 200 and reply["result"]["tools"]


def test_a_modern_version_header_on_a_body_without_meta_is_a_mismatch():
    status, _, reply = _post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"MCP-Protocol-Version": "2026-07-28"},
    )
    assert status == 400 and reply["error"]["code"] == -32020


# ---- the 2026-07-28 era -------------------------------------------------------------------


def test_a_modern_discover_with_its_headers_is_answered():
    status, _, reply = _post(
        _modern("server/discover"), _modern_headers("server/discover")
    )
    assert status == 200 and reply["result"]["resultType"] == "complete"


def test_a_modern_tool_call_names_its_tool_in_a_header_plain_or_base64():
    call = _modern("tools/call", name="echo", arguments={"word": "hi"})
    status, _, reply = _post(call, _modern_headers("tools/call", "echo"))
    assert status == 200 and reply["result"]["content"][0]["text"] == "echo hi"
    encoded = "=?base64?" + base64.b64encode(b"echo").decode() + "?="
    status, _, _ = _post(call, _modern_headers("tools/call", encoded))
    assert status == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"MCP-Protocol-Version": "2026-07-28"},
        _modern_headers("tools/list", "echo"),
        _modern_headers("tools/call"),
        _modern_headers("tools/call", "refuse"),
        _modern_headers("tools/call", "=?base64?!!!?="),
        {**_modern_headers("tools/call", "echo"), "MCP-Protocol-Version": "2025-11-25"},
    ],
)
def test_a_missing_or_disagreeing_header_is_a_header_mismatch(headers):
    call = _modern("tools/call", name="echo", arguments={"word": "hi"})
    status, _, reply = _post(call, headers)
    assert status == 400 and reply["error"]["code"] == -32020


def test_an_unsupported_modern_version_is_a_400_naming_the_supported_ones():
    message = _modern("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2031-01-01"
    headers = {"MCP-Protocol-Version": "2031-01-01", "Mcp-Method": "tools/list"}
    status, _, reply = _post(message, headers)
    assert status == 400 and reply["error"]["code"] == -32022
    assert "2026-07-28" in reply["error"]["data"]["supported"]


def test_an_unknown_modern_method_is_a_404():
    status, _, reply = _post(_modern("initialize"), _modern_headers("initialize"))
    assert status == 404 and reply["error"]["code"] == -32601


def test_an_unknown_tool_in_the_modern_era_is_a_400():
    status, _, reply = _post(
        _modern("tools/call", name="nope"), _modern_headers("tools/call", "nope")
    )
    assert status == 400 and reply["error"]["code"] == -32602


def test_a_bug_in_handle_is_a_500_and_the_endpoint_keeps_answering(monkeypatch):
    monkeypatch.setattr(messages, "_result", lambda *a: 1 / 0)
    status, _, reply = _post(_INITIALIZE)
    assert status == 500 and reply["error"]["code"] == -32603
    monkeypatch.undo()
    assert _post(_INITIALIZE)[0] == 200
