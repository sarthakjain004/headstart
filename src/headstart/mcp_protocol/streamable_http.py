"""MCP's Streamable HTTP transport, as one POST in and one JSON answer out (ADR-0266).

:func:`answer` is framework-free: the Space's `/mcp` route hands it the request's headers and
body and sends back the status, headers and body it returns. Every request is answered with one
``application/json`` reply or a 202, never an SSE stream, and no session is minted, which both
protocol eras allow; a ``Mcp-Session-Id`` a client sends is ignored. The route itself answers
only POST, so a GET or DELETE meets the framework's 405, as both eras require.

**What it checks, in order.** An ``Origin`` outside the allowlist is a 403: Hugging Face's edge
answers CORS preflight itself and reflects any origin, so this is the only thing that stops a web
page from calling the endpoint through its visitors' browsers. Then the body's size, that it is
one JSON-RPC request or notification, and for a modern request the headers 2026-07-28 mirrors
from the body (``MCP-Protocol-Version``, ``Mcp-Method``, ``Mcp-Name``): a missing or disagreeing
one is a 400 ``HeaderMismatch``. A legacy request whose ``MCP-Protocol-Version`` names a modern
revision is the same error, since its body carries none.

**Status.** A notification is a 202 with no body. A modern error maps to the status 2026-07-28
names (an unknown method 404, a malformed or unsupported request 400); a legacy one rides a 200,
as that era's servers answer it.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Collection, Mapping
from typing import Any

from . import messages
from .messages import Server

#: The largest body read. A tool call's arguments are a few hundred bytes.
MAX_BODY_BYTES = 64 * 1024

_JSON = {"Content-Type": "application/json"}

#: Where a modern request names the tool a header must repeat.
_NAMED_METHODS = frozenset({"tools/call"})

_BASE64_OPENING, _BASE64_CLOSING = "=?base64?", "?="


def _reply(status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return status, dict(_JSON), json.dumps(payload).encode()


def _header_value(raw: str | None) -> str | None:
    """A mirrored header's value, its Base64 sentinel form decoded; None if it cannot be."""
    if raw is None or not (
        raw.startswith(_BASE64_OPENING) and raw.endswith(_BASE64_CLOSING)
    ):
        return raw
    encoded = raw[len(_BASE64_OPENING) : -len(_BASE64_CLOSING)]
    try:
        return base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None


def _header_mismatch(
    headers: Mapping[str, str], message: dict[str, Any], version: Any
) -> str | None:
    """Why a modern request's mirrored headers disagree with its body, or None if they agree."""
    if headers.get("mcp-protocol-version") != version:
        return "MCP-Protocol-Version header does not match the body's protocol version"
    method = message["method"]
    if headers.get("mcp-method") != method:
        return "Mcp-Method header does not match the body's method"
    if method in _NAMED_METHODS:
        params = message.get("params") or {}
        if _header_value(headers.get("mcp-name")) != params.get("name"):
            return "Mcp-Name header does not match the body's name"
    return None


def _status(reply: dict[str, Any], modern: bool) -> int:
    if "error" not in reply or not modern:
        return 200
    code = reply["error"]["code"]
    if code == messages.METHOD_NOT_FOUND:
        return 404
    return 500 if code == messages.INTERNAL_ERROR else 400


def answer(
    headers: Mapping[str, str],
    body: bytes,
    server: Server,
    allowed_origins: Collection[str],
) -> tuple[int, dict[str, str], bytes]:
    """One POST to the MCP endpoint: its status, headers and body. ``body`` may be read only up
    to one byte past :data:`MAX_BODY_BYTES`; anything longer is refused either way."""
    named = {name.lower(): value for name, value in headers.items()}
    origin = named.get("origin")
    if origin is not None and origin not in allowed_origins:
        return _reply(
            403,
            messages.error_reply(None, messages.INVALID_REQUEST, "origin not allowed"),
        )
    if len(body) > MAX_BODY_BYTES:
        return _reply(
            413,
            messages.error_reply(None, messages.INVALID_REQUEST, "request too large"),
        )
    try:
        message = json.loads(body)
    except ValueError:
        return _reply(
            400, messages.error_reply(None, messages.PARSE_ERROR, "parse error")
        )
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return _reply(
            400,
            messages.error_reply(
                None,
                messages.INVALID_REQUEST,
                "invalid request: one JSON-RPC request or notification per POST",
            ),
        )
    version = messages.modern_version(message)
    request_id = message.get("id")
    if request_id is not None:
        if version is not None:
            mismatch = _header_mismatch(named, message, version)
        elif named.get("mcp-protocol-version") in messages.MODERN_VERSIONS:
            mismatch = "MCP-Protocol-Version names a revision the body's _meta does not"
        else:
            mismatch = None
        if mismatch:
            return _reply(
                400,
                messages.error_reply(request_id, messages.HEADER_MISMATCH, mismatch),
            )
    try:
        reply = messages.handle(message, server)
    except Exception:  # a bug in handle answers this request, not the whole endpoint
        # The method name only; the body may carry the caller's text.
        server.log.error(  # noqa: G201
            "request %s failed", message.get("method"), exc_info=True
        )
        return _reply(
            500,
            messages.error_reply(request_id, messages.INTERNAL_ERROR, "internal error"),
        )
    if reply is None:
        return 202, {}, b""
    return _reply(_status(reply, version is not None), reply)
