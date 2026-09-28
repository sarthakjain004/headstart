"""One JSON-RPC message in, one out: what every HeadStart MCP server answers, whatever carries it.

`handle` is the seam between the protocol and a transport. `stdio.serve` feeds it newline-delimited
lines for a local install (ADR-0137); `streamable_http.answer` feeds it one POST body for the
Space's hosted endpoint (ADR-0267). It is written out here rather than taken from the `mcp` SDK,
which brings pydantic, anyio, starlette, uvicorn and more to a base install of two packages.

**Two eras, chosen per request.** A request whose ``params._meta`` names
``io.modelcontextprotocol/protocolVersion`` is **modern** (2026-07-28): stateless, answered with
``resultType``, the server's identity in the result's ``_meta``, and caching hints on
``server/discover`` and ``tools/list``. Anything else is **legacy** (2025-11-25 and earlier): the
``initialize`` handshake, ``ping``, and results as that era shaped them. claude.ai's connector
setup probes with a legacy ``initialize`` and its chats speak modern, so both are served
(ADR-0267). A legacy ``server/discover`` — one with no ``_meta`` — is an unknown method, which is
the error a dual-era client falls back to ``initialize`` on.

**Two kinds of failure, kept apart.** A protocol fault (an unknown method or tool, malformed
params, a revision this module does not speak) is a JSON-RPC error. Everything a caller could act
on — a refused argument, a missing credential, an unknown id, a crash inside a tool — is a tool
result with ``isError`` and one sentence, because a protocol error tells the model the server is
broken while a result tells it what to do next.

**Nothing a caller sent reaches a log.** Every line here is written through the server's own
logger (so a record carries the module that owns the server), and names methods, tools, argument
*names* and types — never values, which may be résumé wording or a search.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: The handshake-era revisions ``initialize`` negotiates, newest first. For a tools-only server
#: the two differ in nothing these servers use: 2025-11-25's validation-error rule is already how
#: failures are answered, and its schema dialect change is moot for schemas that use only keywords
#: draft-07 and 2020-12 read alike.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
NEWEST_LEGACY = LEGACY_VERSIONS[0]

#: The stateless revisions a request may name in its ``_meta``.
MODERN_VERSIONS = ("2026-07-28",)

#: Every revision this module speaks, as an unsupported-version error and ``server/discover``
#: list them.
SUPPORTED_VERSIONS = (*MODERN_VERSIONS, *LEGACY_VERSIONS)

PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"

#: JSON-RPC error codes: the standard ones, and the two 2026-07-28 defines that are sent here.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

#: The caching hint on a modern ``server/discover`` and ``tools/list``. The tool list is the same
#: for everyone and changes only with a deploy, so any cache may keep it for an hour.
CACHE_HINT = {"ttlMs": 3_600_000, "cacheScope": "public"}


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
    """One MCP server as :func:`handle` sees it.

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


def error_reply(
    request_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """A JSON-RPC error response; ``request_id`` is None when the request's id could not be read."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def modern_version(message: dict[str, Any]) -> Any:
    """The revision a modern request names in its ``_meta``, or None for a legacy request."""
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return meta.get(PROTOCOL_VERSION_KEY) if isinstance(meta, dict) else None


def _text(text: str, failed: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if failed:
        payload["isError"] = True
    return payload


def _initialize_reply(request_id: Any, params: Any, server: Server) -> dict[str, Any]:
    """The ``initialize`` answer: the client's revision when this module speaks it, else the
    newest one it does — which is what the spec says to do; the client may then decide it cannot
    talk to us."""
    asked = params.get("protocolVersion") if isinstance(params, dict) else None
    result: dict[str, Any] = {
        "protocolVersion": asked if asked in LEGACY_VERSIONS else NEWEST_LEGACY,
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
        return error_reply(request_id, INVALID_PARAMS, "invalid params")
    name = params.get("name")
    if not any(tool["name"] == name for tool in server.tools):
        # A protocol error by the spec's own list (2025-11-25): no tool of that name exists
        # here, so there is nothing for the model to correct but the name. Checked before the
        # unconfigured reason, which is about the tools this server does have. The name is the
        # caller's text, so the trace this leaves does not repeat it.
        server.log.debug("tools/call refused: unknown tool")
        return error_reply(request_id, INVALID_PARAMS, f"unknown tool: {name}")
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
    except Exception as exc:  # a traceback down the transport is a dead server
        # The client gets one sentence; the stack goes to the log. Argument names only.
        # `.error`, not `.exception`, here and in `stdio.serve`: tests/test_log_levels.py finds
        # annotation-level lines by that name.
        server.log.error(  # noqa: G201
            "tool %s failed (args %s)", name, sorted(args), exc_info=True
        )
        return _result(request_id, _text(f"{type(exc).__name__}: {exc}", failed=True))
    finally:
        # No values: the outcome is the only trace a refusal leaves in the log.
        server.log.debug(
            "tool %s -> %s in %.0fms",
            name,
            outcome,
            (time.monotonic() - started) * 1000,
        )
    return _result(request_id, _text(text))


def _legacy_reply(
    request_id: Any, method: Any, params: Any, server: Server
) -> dict[str, Any]:
    if method == "initialize":
        return _initialize_reply(request_id, params, server)
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": server.tools})
    if method == "tools/call":
        return _tool_call_reply(request_id, params or {}, server)
    return error_reply(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _modern_reply(
    request_id: Any, method: Any, params: dict[str, Any], version: Any, server: Server
) -> dict[str, Any]:
    """A 2026-07-28 answer. ``initialize`` and ``ping`` are gone from that revision, so they
    are unknown methods here."""
    if version not in MODERN_VERSIONS:
        return error_reply(
            request_id,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_VERSIONS), "requested": version},
        )
    if method == "server/discover":
        result: dict[str, Any] = {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}},
            **CACHE_HINT,
        }
        if server.instructions:
            result["instructions"] = server.instructions
    elif method == "tools/list":
        result = {"tools": server.tools, **CACHE_HINT}
    elif method == "tools/call":
        reply = _tool_call_reply(request_id, params, server)
        if "error" in reply:
            return reply
        result = reply["result"]
    else:
        return error_reply(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
    identity = {"name": server.name, "version": server.version}
    return _result(
        request_id,
        {"resultType": "complete", **result, "_meta": {SERVER_INFO_KEY: identity}},
    )


def handle(message: dict[str, Any], server: Server) -> dict[str, Any] | None:
    """One request in, one response out — or None for a notification, which takes no reply."""
    request_id = message.get("id")
    if request_id is None:  # a notification — `initialized`, `cancelled`, anything else
        return None
    method = message.get("method")
    params = message.get("params")
    version = modern_version(message)
    if version is None:
        return _legacy_reply(request_id, method, params, server)
    return _modern_reply(request_id, method, params, version, server)
