"""MCP's stdio transport for every HeadStart MCP server (ADR-0137): newline-delimited JSON-RPC over
a subprocess's stdin and stdout, each message answered by `messages.handle`.

It is how a server runs on the user's own machine. The Space's hosted endpoint is the other
transport (`streamable_http`, ADR-0267); both answer through the same `handle`, so a protocol
revision is one change for both. Every line here is written through the server's own logger, to
stderr, and names types and sizes — never the caller's text.
"""

from __future__ import annotations

import json
from typing import Any, TextIO

from . import messages
from .messages import Server


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
            reply: dict[str, Any] | None = messages.error_reply(
                None, messages.PARSE_ERROR, f"parse error: {exc}"
            )
        else:
            if isinstance(message, dict):
                try:
                    reply = messages.handle(message, server)
                except Exception:  # one bad request must not end the session
                    # The method name only; the message may carry the caller's text.
                    server.log.error(  # noqa: G201
                        "request %s failed", message.get("method"), exc_info=True
                    )
                    reply = messages.error_reply(
                        message.get("id"), messages.INTERNAL_ERROR, "internal error"
                    )
            else:
                # Type and size only. INFO, not WARNING: this is per message, and ADR-0039
                # bounds annotations per loop.
                server.log.info(
                    "non-object JSON-RPC message dropped: %s of length %d",
                    type(message).__name__,
                    len(line),
                )
                reply = messages.error_reply(
                    None, messages.INVALID_REQUEST, "invalid request: not a JSON object"
                )
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
