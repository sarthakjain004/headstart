"""The Résumé MCP server — three read-only tools over one Account's Résumé documents.

Run as ``python -m headstart.resume_mcp`` and spoken to over stdio. The transport is
JSON-RPC 2.0, newline-delimited, which is all MCP's stdio transport is; it is written out by hand
in :mod:`headstart.mcp_protocol.stdio`, shared with every HeadStart MCP server, rather than taken
from the `mcp` SDK, because that SDK brings pydantic, anyio, starlette and uvicorn to a local
subprocess that answers four method names, and this repo's base install is two packages. Nothing
here needs a dependency the test suite does not already have — there is no `importorskip` in
`tests/test_resume_mcp.py` and there is not meant to be one. This module is the three tools and
what binds them, and the one Account, into that loop.

**Read-only, on purpose.** ADR-0137 §"What it may not do": the browser is the working copy, a
push carries a revision the store checks, and a writer here would be a second client of that
conflict protocol with none of the recovery the tab has. An agent that could rewrite someone's
employment history silently is also not what was asked for.

**One Account.** Every tool's schema is closed (`additionalProperties: false`) and none of
them names an account, an address or a path; the arguments are checked against the schema here
too, because a client is free to ignore it. Under that, `account.Account` holds the id and
offers no method that takes one. See ADR-0137.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .. import log
from ..mcp_protocol import stdio
from ..mcp_protocol.stdio import ToolFailure
from .account import Account, Unconfigured, open_account
from .inspection import Unreadable, read_document, render

#: stderr only (headstart.log's handler), so a diagnostic line never lands in the protocol.
_log = log.get(__name__, __spec__)

NAME = "headstart-resume"
VERSION = "1.0.0"

#: The revision of MCP a client is answered with when it names none, or one the shared loop does
#: not speak — the loop's newest. A client naming one the loop speaks gets that one instead.
PROTOCOL_VERSION = stdio.NEWEST

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

#: Every tool here only reads (ADR-0137 §"What it may not do"), and says so to the client.
READ_ONLY = stdio.READ_ONLY_ANNOTATIONS

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_resumes",
        "title": "List synced résumés",
        "annotations": READ_ONLY,
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
        "title": "Get a résumé's stored document",
        "annotations": READ_ONLY,
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
        "title": "Inspect what a résumé says",
        "annotations": READ_ONLY,
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


# ---- the tools ------------------------------------------------------------------------


def _document(account: Account, arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = arguments.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise ToolFailure("document_id is required — take one from list_resumes.")
    document = account.document(document_id)
    if document is not None:
        return document
    # None is three facts at once (absent, corrupt, Hub unreachable) and reporting all three
    # as the first would tell someone their résumé is gone during an outage. The listing
    # settles it: an id that is filed and still will not read is a record that exists.
    if document_id in account.ids():
        raise ToolFailure(
            f"This account has a résumé filed under {document_id!r} but it could not be "
            "read — the record is corrupt, or the Hub did not answer. It is not missing. "
            "Try again, and if it persists the file needs looking at directly."
        )
    raise ToolFailure(
        f"This account keeps no synced résumé with id {document_id!r}. "
        "list_resumes has the ids it does keep. " + SYNC_NOTE
    )


def list_resumes(account: Account, arguments: dict[str, Any]) -> str:
    documents = account.documents()
    # `resumes_for` skips a record it cannot parse, silently. The id listing is the count that
    # does not lie, so the gap between them is reported rather than absorbed — the same reason
    # SYNC_NOTE exists, one layer in.
    unreadable = len(account.ids()) - len(documents)
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
    if unreadable > 0:
        lines += [
            "",
            (
                f"{unreadable} further record(s) are filed under this account and could not "
                "be read — corrupt, or the Hub did not answer. They are NOT listed above."
            ),
        ]
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
        facts = read_document(document, version)
    except Unreadable as exc:
        raise ToolFailure(str(exc)) from exc
    return f"{render(facts)}\n\n{FRESHNESS_NOTE}"


#: Tool name -> the function that answers it. Beside `TOOLS` rather than inside it, because
#: `TOOLS` is serialised to the client and a function is not JSON. `test_every_tool_has_a
#: _handler` pins the two together, since the failure of an `if`-cascade here was a tool that
#: listed and then ran a different tool's body.
HANDLERS = {
    "list_resumes": list_resumes,
    "get_resume": get_resume,
    "inspect_resume": inspect_resume,
}


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
    return HANDLERS[name](account, arguments)


# ---- the transport --------------------------------------------------------------------


def _server(account: Account | Unconfigured) -> stdio.Server:
    """This server as the shared loop sees it, bound to the one Account — or to the reason there
    isn't one, which every call then reports."""
    unconfigured = account if isinstance(account, Unconfigured) else None
    return stdio.Server(
        name=NAME,
        version=VERSION,
        tools=TOOLS,
        call=lambda name, arguments: call(account, name, arguments),
        log=_log,
        unconfigured=unconfigured,
    )


def handle(
    message: dict[str, Any], account: Account | Unconfigured
) -> dict[str, Any] | None:
    """One request in, one response out — or None for a notification (`mcp_protocol.stdio`).

    `account` is either the bound Account or the reason there isn't one. The server starts
    either way: a client whose server exits on a missing variable reports "failed to connect",
    which tells whoever has to fix it nothing at all.
    """
    return stdio.handle(message, _server(account))


def serve(stdin: TextIO, stdout: TextIO, account: Account | Unconfigured) -> None:
    """The stdio loop (`mcp_protocol.stdio.serve`), bound to this server."""
    stdio.serve(stdin, stdout, _server(account))


def main() -> None:
    # headstart.log writes to stderr, never stdout: stdout is the protocol, and one stray
    # line closes the session.
    log.setup()
    try:
        account: Account | Unconfigured = open_account()
        # The hashed subscription id, never the address it is derived from.
        _log.info("%s %s serving account %s", NAME, VERSION, account.id)
    except Unconfigured as exc:
        _log.warning("%s: %s", NAME, exc)
        account = exc
    serve(sys.stdin, sys.stdout, account)
