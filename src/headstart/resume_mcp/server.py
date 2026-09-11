"""The Résumé MCP server — three read-only tools over one Account's Résumé documents.

Run as ``python -m headstart.resume_mcp`` and spoken to over stdio. The transport is
JSON-RPC 2.0, newline-delimited, which is all MCP's stdio transport is; it is written out here
rather than taken from the `mcp` SDK because that SDK brings pydantic, anyio, starlette and
uvicorn to a local subprocess that answers four method names, and this repo's base install is
two packages. Nothing here needs a dependency the test suite does not already have — there is
no `importorskip` in `tests/test_resume_mcp.py` and there is not meant to be one.

**Read-only, on purpose.** ADR-0136 §"What it may not do": the browser is the working copy, a
push carries a revision the store checks, and a writer here would be a second client of that
conflict protocol with none of the recovery the tab has. An agent that could rewrite someone's
employment history silently is also not what was asked for.

**One Account.** Every tool's schema is closed (`additionalProperties: false`) and none of
them names an account, an address or a path; the arguments are checked against the schema here
too, because a client is free to ignore it. Under that, `account.Account` holds the id and
offers no method that takes one. See ADR-0136.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .account import Account, Unconfigured, open_account
from .inspection import Unreadable, inspect, render

NAME = "headstart-resume"
VERSION = "1.0.0"

#: The revision of MCP this speaks. A client asking for another is answered with this one,
#: which is what the spec says to do — it may then decide it cannot talk to us.
PROTOCOL_VERSION = "2025-06-18"

#: Said once per answer, because it is the difference between this data and the screen the
#: person is looking at, and an agent that does not know it will confidently report stale
#: words as current ones.
FRESHNESS_NOTE = (
    "The browser holds the working copy. This account copy is written on coarse events — an "
    "explicit save, the tab going away, and at most one push every few minutes while editing "
    "— so it can be behind what is on screen right now."
)

#: Said by `list_resumes` only, where the omission is invisible and therefore a trap.
SYNC_NOTE = (
    "Only résumés with account sync switched on appear here. Sync is per-résumé and off by "
    "default, so a résumé that lives only in the browser is not in the account's dataset at "
    "all — this server cannot see it and cannot tell you how many there are."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_resumes",
        "description": (
            "The Résumé documents this account keeps a synced copy of — id, name, layout, "
            "when it was last edited, and how many tailored versions it carries. Start here: "
            "the ids the other two tools need come from this list. Only synced résumés are "
            "visible; unsynced ones live only in the person's browser."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_resume",
        "description": (
            "One Résumé document whole, as the stored JSON: the node tree, the content map, "
            "every variant and tailoring, the layout id and the theme. The exact bytes the "
            "browser exports. Use inspect_resume instead if you want to read what the résumé "
            "says — this is the raw structure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The id from list_resumes, e.g. 'rm8k2p1a9x'.",
                }
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "inspect_resume",
        "description": (
            "What is actually set in a résumé, block by block: every block in document "
            "order with its component type, the fields that type declares, their current "
            "values, whether it prints, and which tailored versions reword it. Read as the "
            "master by default; pass `version` to read it as one tailoring, which merges "
            "that version's wording over the master and drops the blocks it leaves out."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The id from list_resumes.",
                },
                "version": {
                    "type": "string",
                    "description": (
                        "A tailoring's name or id, or 'master' (the default) for the "
                        "untailored résumé. list_resumes and inspect_resume both name them."
                    ),
                },
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
]


class ToolFailure(Exception):
    """Something the caller should read and act on — reported as a failed tool result with a
    sentence in it, never as a JSON-RPC error. A protocol error says the server is broken; a
    missing token, an unknown id or an absent `node` are all answers to the question asked."""


# ---- the tools ------------------------------------------------------------------------


def _document(account: Account, arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = arguments.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise ToolFailure("document_id is required — take one from list_resumes.")
    document = account.document(document_id)
    if document is None:
        raise ToolFailure(
            f"This account keeps no synced résumé with id {document_id!r}. "
            "list_resumes has the ids it does keep. " + SYNC_NOTE
        )
    return document


def list_resumes(account: Account) -> str:
    documents = account.documents()
    if not documents:
        return (
            "This account has no synced Résumé documents.\n\n"
            f"{SYNC_NOTE}\n\nIf you expected one here, check that syncing is switched on for "
            "it on the Résumé tab."
        )
    lines = [f"{len(documents)} synced Résumé document(s), newest edit first:", ""]
    for document in documents:
        tailorings = document.get("tailorings") or []
        names = ", ".join(
            f"{t.get('name')!r}" for t in tailorings if isinstance(t, dict)
        )
        lines.append(f"· {document.get('name')!r} — id {document.get('id')}")
        lines.append(
            f"    layout {document.get('layoutId')} · last edit "
            f"{document.get('updatedAt')} · account revision {document.get('rev', 0)}"
        )
        lines.append(
            f"    {len(tailorings)} tailored version(s)"
            + (f": {names}" if names else "")
        )
    lines += ["", SYNC_NOTE, "", FRESHNESS_NOTE]
    return "\n".join(lines)


def get_resume(account: Account, arguments: dict[str, Any]) -> str:
    document = _document(account, arguments)
    return f"{FRESHNESS_NOTE}\n\nThe stored document, verbatim:\n\n" + json.dumps(
        document, indent=2, ensure_ascii=False
    )


def inspect_resume(account: Account, arguments: dict[str, Any]) -> str:
    document = _document(account, arguments)
    version = arguments.get("version") or "master"
    if not isinstance(version, str):
        raise ToolFailure("version must be a tailoring's name or id, or 'master'.")
    try:
        facts = inspect(document, version)
    except Unreadable as exc:
        raise ToolFailure(str(exc)) from exc
    return f"{render(facts)}\n\n{FRESHNESS_NOTE}"


def call(account: Account, name: str, arguments: dict[str, Any]) -> str:
    """Dispatch one tool call. Unknown arguments are refused rather than ignored: the schemas
    are closed, and a silently dropped argument is how a caller ends up believing it asked for
    something it did not — an `account` among them."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        raise ToolFailure(f"no such tool: {name}")
    allowed = set(tool["inputSchema"]["properties"])
    extra = sorted(set(arguments) - allowed)
    if extra:
        raise ToolFailure(
            f"{name} takes {sorted(allowed) or 'no arguments'}; it was given {extra}. "
            "This server reads one account — the one its own configuration names — and no "
            "tool takes an account, an address or a path."
        )
    if name == "list_resumes":
        return list_resumes(account)
    if name == "get_resume":
        return get_resume(account, arguments)
    return inspect_resume(account, arguments)


# ---- the transport --------------------------------------------------------------------


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


def handle(
    message: dict[str, Any], account: Account | Unconfigured
) -> dict[str, Any] | None:
    """One request in, one response out — or None for a notification, which takes no reply.

    `account` is either the bound Account or the reason there isn't one. The server starts
    either way: a client whose server exits on a missing variable reports "failed to connect",
    which tells whoever has to fix it nothing at all.
    """
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:  # a notification — `initialized`, `cancelled`, anything else
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "version": VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        if isinstance(account, Unconfigured):
            return _result(request_id, _text(str(account), failed=True))
        try:
            text = call(account, params.get("name"), params.get("arguments") or {})
        except ToolFailure as exc:
            return _result(request_id, _text(str(exc), failed=True))
        except Exception as exc:  # noqa: BLE001 — a traceback down stdio is a dead server
            return _result(
                request_id,
                _text(f"{type(exc).__name__}: {exc}", failed=True),
            )
        return _result(request_id, _text(text))
    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin: TextIO, stdout: TextIO, account: Account | Unconfigured) -> None:
    """The stdio loop. Every line is one JSON-RPC message; every reply is flushed as it is
    written, because a client blocked on a buffered answer looks exactly like a hung server."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            reply: dict[str, Any] | None = _error(None, -32700, f"parse error: {exc}")
        else:
            reply = handle(message, account) if isinstance(message, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main() -> None:
    try:
        account: Account | Unconfigured = open_account()
    except Unconfigured as exc:
        # stderr, never stdout: stdout is the protocol, and one stray line closes the session.
        print(f"{NAME}: {exc}", file=sys.stderr, flush=True)
        account = exc
    serve(sys.stdin, sys.stdout, account)
