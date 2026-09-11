"""A local MCP server that reads one Account's Résumé documents (ADR-0137).

Three read-only tools — `list_resumes`, `get_resume`, `inspect_resume` — served over stdio to
an agent running on the same machine, under that person's own credentials. No new
internet-facing surface and no shared token: the server is a subprocess of the client.

`server.py` is the tools and the transport, `account.py` is the one Account it may read, and
`inspection.py` (with `inspect_document.js`) is the block-by-block view, built by running the
Résumé tab's own JavaScript rather than a Python copy of it.

How to install it in a client, and what it does when the credentials are absent:
`docs/agents/resume-mcp-server.md`.
"""
