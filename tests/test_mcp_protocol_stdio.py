"""The stdio transport every HeadStart MCP server runs locally — `headstart/mcp_protocol/stdio.py`.

The stand-in server is `tests/test_mcp_protocol_messages.py`'s, so the loop is tested around the
`handle` those tests pin.
"""

from __future__ import annotations

import io
import json

from test_mcp_protocol_messages import LOGGER, _call_message, _records, _server

from headstart.mcp_protocol import messages, stdio

# ---- the stdio loop ---------------------------------------------------------------------


def test_a_bug_in_handle_answers_an_internal_error_and_serving_continues(
    monkeypatch, caplog
):
    """Moved from `tests/test_resume_mcp.py` with the loop it tests (ADR-0137 amendment)."""
    monkeypatch.setattr(messages, "_result", lambda *a: 1 / 0)
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
