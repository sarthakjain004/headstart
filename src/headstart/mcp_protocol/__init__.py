"""How a HeadStart MCP server speaks the protocol (ADR-0137, ADR-0267): `messages` answers one
JSON-RPC message in either protocol era, and `stdio` and `streamable_http` are the two transports
that carry it, so a protocol revision is absorbed in one module rather than once per server.
"""
