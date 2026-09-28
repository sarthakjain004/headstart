"""The JSON-RPC 2.0 loop every HeadStart MCP server speaks over stdio (ADR-0137).

MCP's stdio transport is newline-delimited JSON-RPC over a subprocess's stdin and stdout. It is
written out here rather than taken from the `mcp` SDK, which brings pydantic, anyio, starlette,
uvicorn and more to a base install of two packages. It moved here from `resume_mcp.server` when a
second server needed the same loop, so a protocol revision is one change for both.

**The legacy handshake.** Claude Code speaks the 2025-era `initialize` handshake to stdio
servers unless told otherwise, so that is what this answers. A `server/discover` probe from a
2026-07-28 client is an unknown method, and the spec has such a client fall back to
`initialize` on that error — so it needs no code here.

**Two kinds of failure, kept apart.** A protocol fault (a line that is not JSON, a request that
is not an object, an unknown method or tool, malformed params) is a JSON-RPC error. Everything a
caller could act on — a refused argument, a missing credential, an unknown id, a crash inside a
tool — is a tool result with ``isError`` and one sentence, because a protocol error tells the
model the server is broken while a result tells it what to do next.

**Nothing a caller sent reaches a log.** Every line here is written through the server's own
logger (so a record carries the module that owns the server), to stderr, and names methods,
tools, argument *names* and types — never values, which may be résumé wording or a search.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

#: The protocol revisions this loop speaks, newest first. For a tools-only server the two differ
#: in nothing these servers use: 2025-11-25's validation-error rule is already how failures are
#: answered, and its schema dialect change is moot for schemas that use only keywords draft-07 and
#: 2020-12 read alike.
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18")
NEWEST = SUPPORTED_VERSIONS[0]


#: The annotations of a tool that only reads: it changes nothing, so calling it twice is calling it
#: once, and it reaches no system but its own server's.
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class ToolFailure(Exception):
    """Something the caller should read and act on — reported as a failed tool result with a
    sentence in it, never as a JSON-RPC error. A protocol error says the server is broken; a
    missing token, an unknown id or a refused argument are all answers to the question asked."""


@dataclass(frozen=True)
class Server:
    """One MCP server as this loop sees it.

    ``tools`` is served to ``tools/list`` as given, in order. ``call(name, arguments)`` answers
    one ``tools/call`` for a tool in ``tools`` with its text, raising :class:`ToolFailure` for a
    refusal. ``log`` is the owning module's logger. ``unconfigured`` is the reason the server
    cannot answer yet: it still starts and lists its tools, and every call says why, because a
    server that exits on a missing variable shows up in the client as one that will not connect.
    """

    name: str
    version: str
    tools: list[dict[str, Any]]
    call: Callable[[str, dict[str, Any]], str]
    log: logging.Logger
    instructions: str | None = None
    unconfigured: Exception | None = None


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _text(text: str, failed: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if failed:
        payload["isError"] = True
    return payload


def _initialize_reply(request_id: Any, params: Any, server: Server) -> dict[str, Any]:
    """The ``initialize`` answer: the client's revision when this loop speaks it, else the
    newest one it does — which is what the spec says to do; the client may then decide it cannot
    talk to us."""
    asked = params.get("protocolVersion") if isinstance(params, dict) else None
    result: dict[str, Any] = {
        "protocolVersion": asked if asked in SUPPORTED_VERSIONS else NEWEST,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": server.name, "version": server.version},
    }
    if server.instructions:
        result["instructions"] = server.instructions
    return _result(request_id, result)


def _tool_call_reply(request_id: Any, params: Any, server: Server) -> dict[str, Any]:
    args = (params.get("arguments") or {}) if isinstance(params, dict) else None
    if not isinstance(args, dict):
        # Type names only: the malformed value may carry the caller's text.
        server.log.info(
            "tools/call refused: params %s, arguments %s",
            type(params).__name__,
            type(args).__name__,
        )
        return _error(request_id, -32602, "invalid params")
    name = params.get("name")
    if not any(tool["name"] == name for tool in server.tools):
        # A protocol error by the spec's own list (2025-11-25): no tool of that name exists
        # here, so there is nothing for the model to correct but the name. Checked before the
        # unconfigured reason, which is about the tools this server does have. The name is the
        # caller's text, so the trace this leaves does not repeat it.
        server.log.debug("tools/call refused: unknown tool")
        return _error(request_id, -32602, f"unknown tool: {name}")
    if server.unconfigured is not None:
        return _result(request_id, _text(str(server.unconfigured), failed=True))
    started = time.monotonic()
    outcome = "error"
    try:
        text = server.call(name, args)
        outcome = "ok"
    except ToolFailure as exc:
        outcome = "refused"
        return _result(request_id, _text(str(exc), failed=True))
    except Exception as exc:  # a traceback down stdio is a dead server
        # The client gets one sentence; the stack goes to stderr. Argument names only.
        # `.error`, not `.exception`, here and in `serve`: tests/test_log_levels.py finds
        # annotation-level lines by that name.
        server.log.error(  # noqa: G201
            "tool %s failed (args %s)", name, sorted(args), exc_info=True
        )
        return _result(request_id, _text(f"{type(exc).__name__}: {exc}", failed=True))
    finally:
        # No values: the outcome is the only trace a refusal leaves on stderr.
        server.log.debug(
            "tool %s -> %s in %.0fms",
            name,
            outcome,
            (time.monotonic() - started) * 1000,
        )
    return _result(request_id, _text(text))


def handle(message: dict[str, Any], server: Server) -> dict[str, Any] | None:
    """One request in, one response out — or None for a notification, which takes no reply."""
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:  # a notification — `initialized`, `cancelled`, anything else
        return None
    params = message.get("params")
    if method == "initialize":
        return _initialize_reply(request_id, params, server)
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": server.tools})
    if method == "tools/call":
        return _tool_call_reply(request_id, params or {}, server)
    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin: TextIO, stdout: TextIO, server: Server) -> None:
    """The stdio loop. Every line is one JSON-RPC message; every reply is flushed as it is
    written, because a client blocked on a buffered answer looks exactly like a hung server."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            # Length only, as below: the line may carry the caller's text.
            server.log.info("unparseable JSON-RPC line dropped: length %d", len(line))
            reply: dict[str, Any] | None = _error(None, -32700, f"parse error: {exc}")
        else:
            if isinstance(message, dict):
                try:
                    reply = handle(message, server)
                except Exception:  # one bad request must not end the session
                    # The method name only; the message may carry the caller's text.
                    server.log.error(  # noqa: G201
                        "request %s failed", message.get("method"), exc_info=True
                    )
                    reply = _error(message.get("id"), -32603, "internal error")
            else:
                # Type and size only. INFO, not WARNING: this is per message, and ADR-0039
                # bounds annotations per loop.
                server.log.info(
                    "non-object JSON-RPC message dropped: %s of length %d",
                    type(message).__name__,
                    len(line),
                )
                reply = _error(None, -32600, "invalid request: not a JSON object")
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
