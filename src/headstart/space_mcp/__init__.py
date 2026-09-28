"""A local MCP server that reads the deployed HeadStart Space for an agent (ADR-0253).

Three read-only tools — `search_jobs`, `read_trends`, `hiring_now` — served over stdio to an agent
on the same machine. Every answer comes from the Space's own read routes, reached with a
read-scoped `AGENT_TOKEN`, so its numbers are the website's.

`server.py` is the tools and the entry point; `space_client.py` is how a request reaches the
Space; `company_scope.py` is what a typed company means to each tool; the `*_answer.py` modules
each read and render one tool's answer; `scraped_text.py` is how text from employers' job boards
appears in one. How to install it: `docs/agents/space-mcp-server.md`.
"""
