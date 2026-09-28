"""A local MCP server that reads the deployed HeadStart Space for an agent (ADR-0253).

Read-only tools — today `search_jobs`, `read_trends` and `hiring_now` — served over stdio to an agent
on the same machine. Every answer comes from the Space's own read routes, reached with a
read-scoped `AGENT_TOKEN`, so its numbers are the website's.

`tools/` is the tools, one module each, registered in `tools.REGISTRY` — adding one is a module and a
line (its docstring says how); `space_tool.py` is what a tool is; `server.py` serves the registry;
`space_client.py` is how a request reaches the Space; `company_scope.py` is what a typed company
means to a tool; `role_families.py` is the categories a tool may name; `scraped_text.py` is how
text from employers' job boards appears in an answer. How to install it, and to add a tool:
`docs/agents/space-mcp-server.md`.
"""
