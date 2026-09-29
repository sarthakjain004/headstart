"""One tool of the Space MCP server, whole: what the client is told about it and what answers it.

A :class:`SpaceTool` keeps a tool's name, title, description, input schema, annotations, the one
sentence the server's instructions say about it, its size budget and its answer function together,
so a tool is one module under `space_mcp/tools/` and adding one touches nothing else but the
registry (`space_mcp/tools/__init__.py`). The registry's contract tests
(`tests/test_space_mcp_tools.py`) hold every registered tool to the same rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from headstart.mcp_protocol.messages import READ_ONLY_ANNOTATIONS
from headstart.space_mcp.space_client import SpaceClient

#: An answer: the Space as this call reads it, and the call's arguments (checked against the
#: schema and with its defaults filled) -> the text the model reads.
Answer = Callable[[SpaceClient, dict[str, Any]], str]

#: The most any answer may run: under Claude Code's 10,000-token warning even for text dense with
#: links and ids, which tokenizes nearer 3 characters a token than 4. A tool's own `max_chars` is
#: at most this.
ANSWER_CEILING_CHARS = 30_000

#: A Space tool only reads, but its world is open (ADR-0334): what it answers is postings written
#: by thousands of employers, as a web search's is pages written by their sites, and it quotes
#: them. `openWorldHint` tells a client that, so one that treats open-world results as untrusted
#: text treats these so too, as every answer's own "data, not instructions" line asks.
SPACE_TOOL_ANNOTATIONS = {**READ_ONLY_ANNOTATIONS, "openWorldHint": True}


@dataclass(frozen=True)
class SpaceTool:
    """One tool: its listing, its place in the server's instructions, its budget, its answer."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    #: One sentence for the server's instructions: when an agent should reach for this tool.
    when_to_use: str
    answer: Answer
    #: The longest answer this tool should give at its largest allowed input; the server cuts
    #: anything past it (a guard for a tool whose rendering grows), and a test renders the
    #: largest input to prove the tool stays under it.
    max_chars: int
    annotations: Mapping[str, bool] = field(
        default_factory=lambda: dict(SPACE_TOOL_ANNOTATIONS)
    )
    #: Per argument, how the words a caller sent are read before the schema check: a category's
    #: label or retired id becomes the id its enum lists (`role_families.resolve`, ADR-0274). A
    #: reader hands back a value it cannot read unchanged, for the check to refuse, or raises a
    #: `ToolFailure` saying what it accepts.
    argument_readers: Mapping[str, Callable[[Any], Any]] = field(default_factory=dict)

    def listing(self) -> dict[str, Any]:
        """The tool as `tools/list` serves it."""
        return {
            "name": self.name,
            "title": self.title,
            "annotations": dict(self.annotations),
            "description": self.description,
            "inputSchema": self.input_schema,
        }
