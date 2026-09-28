"""The Space MCP server's tools, one module each, and the one list that registers them.

**Adding a tool** is one new module and one line here:

1. `space_mcp/tools/<tool_name>.py` — the module's name is the tool's name. It ends with
   ``TOOL = SpaceTool(...)``: the title, the description (the rule that matters most in its first
   sentence; at most 2,048 characters, where Claude Code cuts), a closed input schema written
   only in keywords JSON Schema draft-07 and 2020-12 read alike, the one ``when_to_use`` sentence
   the server's instructions will carry, the answer function, and the answer's ``max_chars`` at
   the tool's largest input.
2. Add ``<tool_name>.TOOL`` to :data:`REGISTRY` below; its place is its place in `tools/list`.
3. If it reads a Space route no tool read before: add the route to `SpaceRoute`, make sure the
   Space admits `AGENT_TOKEN` on it (`deploy/hf-space/app.py`'s token map, ADR-0253), and raise
   the Space's agent contract version with this server's `AGENT_API` when the route is new
   contract.
4. Tests: `tests/test_space_mcp_tools.py` holds every registered tool to these rules without
   being told about it; add the tool's behaviour to `tests/test_space_mcp_server.py` and, where
   it depends on what the real app answers, `tests/test_space_mcp_against_space_app.py`.
5. Say what it does in `docs/agents/space-mcp-server.md`, and give the evaluation a task for it.

**A tool that writes, or reads one Account's records, is not an addition but a decision:** every
tool today is read-only and Account-free, the credential opens read routes only, and the contract
tests pin both. Such a tool needs its own credential design first (ADR-0253; the plan's §13).
"""

from __future__ import annotations

from headstart.space_mcp.space_tool import SpaceTool
from headstart.space_mcp.tools import hiring_now, read_trends, search_jobs

#: Every tool the server offers, in the order `tools/list` serves them.
REGISTRY: tuple[SpaceTool, ...] = (
    search_jobs.TOOL,
    read_trends.TOOL,
    hiring_now.TOOL,
)
