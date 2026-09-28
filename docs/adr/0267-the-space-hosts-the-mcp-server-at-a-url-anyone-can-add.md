# ADR-0267: The Space hosts the MCP server at a URL anyone can add

**Status:** accepted · **Date:** 2026-09-28 · **Supersedes in part:**
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (its
rejection of topology C, an MCP endpoint served by the Space, and its deploy-trigger negations for
`space_mcp` and `mcp_protocol`) · **Relates to:**
[ADR-0258](0258-the-spaces-read-routes-answer-anyone.md) (the public read routes the tools read),
[ADR-0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md) (how a
caller is counted), [ADR-0137](0137-an-agent-reads-a-resume-by-running-the-resume-tabs-own-javascript.md)
(the hand-written protocol loop) · **Amended by:**
[ADR-0290](0290-a-merge-deploys-the-space-only-when-it-changes-what-the-space-loads.md) (the deploy
trigger lists only what the Space loads)

## Context

The owner wants anyone to be able to use HeadStart's MCP server. Until now it was a local stdio
process, which Claude Code and Claude Desktop can run but claude.ai's web and mobile apps cannot: a
custom connector there is a URL that Anthropic's cloud calls. The options, their measurements and
their sources are `docs/mcp/2026-09-28_hosted-mcp-endpoint-options.md`. The owner chose its
recommendation, **A: `POST /mcp` on the existing Space, answered in process**, over a separate MCP
Space calling this one over HTTPS (B, which needs a paid plan to create, adds a hop, and loses
every caller's address) and staying local-only (C, which serves no web or mobile user).

ADR-0253 rejected a Space route because every change to a tool's wording would restart the Space,
costing about four minutes of availability, and because it added an internet-facing JSON-RPC
surface. Both objections have weakened. Deploys now roll with no downtime (measured 2026-09-28),
and since ADR-0258 the read routes answer anyone, so `/mcp` exposes nothing an anonymous caller
cannot already read.

## Decision

**One implementation, two transports.** `mcp_protocol.messages.handle` (dict in, dict out) answers
every MCP message. `mcp_protocol.stdio.serve` carries it over a subprocess's stdin and stdout, as
before. `mcp_protocol.streamable_http.answer` carries it over one HTTP POST. It is framework-free:
headers and bytes in, status, headers and body out. `space_mcp.server.build_server(fetch=…)` builds
the same registry either way. On the Space, `space_client.wsgi_fetch(app)` is its `Fetch`: each
tool's read is a request to the app in process, with no cookie, so no Account's state reaches an
answer even from a signed-in browser. This adapter is the one the e2e tests already ran against the
real `app.py`, promoted to production, so no user is served through an untested adapter. The route
in `app.py` checks its limits and hands the request to `answer`.

**The rename.** `stdio.py` held `handle`, `Server` and `ToolFailure`, which are no longer about
stdio once a second transport exists (CLAUDE.md §3). They moved to `mcp_protocol/messages.py`, and
`stdio.py` keeps only `serve`. Against its neighbours: `messages` names what `handle` takes and
returns, and it is neither a near-synonym nor a near-homograph of `stdio`, `streamable_http` or
`tool_arguments`. Every import moved with it, `resume_mcp` included; `handle`'s tests moved to
`tests/test_mcp_protocol_messages.py`. The handshake-era constants are now named for their era,
`LEGACY_VERSIONS` and `NEWEST_LEGACY`.

**What the endpoint answers.** One `application/json` reply per POST, or a 202 with no body for a
notification. No SSE stream and no session: a sent `Mcp-Session-Id` is ignored. Both eras allow
this. GET and DELETE meet Flask's 405, since the route is POST only. The body is read to at most
64 KiB, and a longer one is a 413.

These defaults each come from the options doc, and the owner can flip any of them:

1. **Both protocol eras.** A request whose `params._meta` carries
   `io.modelcontextprotocol/protocolVersion` is modern (2026-07-28). It is answered statelessly:
   `server/discover`, `resultType: "complete"` on every result, the server's identity under
   `_meta["io.modelcontextprotocol/serverInfo"]`, and `ttlMs: 3600000` with `cacheScope: "public"`
   on `server/discover` and `tools/list`, since the tool list is the same for everyone and changes
   only with a deploy. An unsupported modern version is `-32022`, listing every version spoken.
   `initialize` and `ping` are unknown methods there. Its mirrored headers are checked:
   `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` for `tools/call`, which may use the Base64
   sentinel form. A missing or disagreeing header is a 400 `-32020`. A modern error maps to the
   status 2026-07-28 names: an unknown method is 404, `-32603` is 500, and anything else is 400.
   Anything without that `_meta` key is legacy (2025-11-25), with today's handshake, and its
   JSON-RPC errors ride a 200. A legacy body whose `MCP-Protocol-Version` header names a modern
   revision is a `-32020`. Why both: claude.ai's connector setup probes with a legacy `initialize`,
   its chats speak 2026-07-28, and a legacy-only server was measured failing claude.ai's modern
   requests with 400s. Stdio gains the modern era too, for free. *Flip:* modern-only, if
   Anthropic's setup probe moves to 2026-07-28. A modern request missing
   `io.modelcontextprotocol/clientCapabilities` is answered rather than refused, since no tool
   needs a client capability.
2. **The Origin allowlist.** Answered: no `Origin` (a server-side client such as claude.ai's
   connector or Claude Code), `https://claude.ai`, `https://claude.com`, and the Space's own
   `https://imposeidon-headstart-search.hf.space`. Anything else is a 403 with a JSON-RPC error and
   no `id`. The check is the only gate against other sites, because HF's edge answers CORS preflight
   itself and reflects any `Origin`. The app prints each distinct `Origin` the first time it sees
   one in a boot (at most 50), with whether it was allowed and whether the caller's address is in
   Anthropic's range, so the first real connection shows what Anthropic sends. *Flip:* widen or
   narrow the set in `_MCP_ORIGINS`.
3. **Budgets.** These are conservative for a free 2 vCPU Space.
   - **30 requests in any 60 s per address** (`_MCP_LIMIT`), counted at the last
     `X-Forwarded-For` entry as ADR-0262 counts the read routes. The refusal is a 429 with
     `Retry-After`, the `X-HeadStart` marker, and a sentence. One tool call reads two to five routes
     in process, so 30 outer requests already mean more reading than the 60 direct route requests
     one address may make. A person's chat makes a few calls a minute.
   - **300 in any 60 s shared by `160.79.104.0/21`** (`_ANTHROPIC_LIMIT`), Anthropic's published
     egress range. Every claude.ai user arrives from it, so a per-address limit sized for one
     person would throttle them all together or not at all, depending on how many addresses
     Anthropic uses. Without OAuth there is no per-user key, because `clientInfo` is
     unauthenticated. The range is one caller with ten people's budget.
   - **At most 4 `/mcp` requests at once** (`_MCP_AT_ONCE`), across every caller. Each can fan out
     to two to four reads on threads, so 4 costs about what four people searching in the page at
     once do. A fifth waits up to 10 s for a place, then gets a 503 with `Retry-After: 10`.
   - **The tools' own reads are not counted again.** They reach the read routes in process with no
     forwarding header. Counted, every MCP user would be one caller: Werkzeug's test client sends
     no `REMOTE_ADDR`, so the key would be the empty string. `wsgi_fetch` marks each read with the
     WSGI environ key `headstart.space_mcp.in_process_read`, and `_limit_each_caller` skips a
     marked request. A caller writes only `HTTP_*` environ keys, through its headers, so it cannot
     set this one; a test pins that. The client's per-process `RequestBudget` (60 a minute) is not
     used in process, since there it would be one budget for everyone. Each call gets its own.
   - **`/mcp` is not a write.** It is a POST, but it commits nothing, so the per-caller write limit
     (#592) leaves it to its own.

   *Flip:* the numbers are constants beside the route, and a test pins them.
4. **Module naming**, as above. *Flip:* another name for `messages.py`.

**The deploy trigger.** `deploy-space.yml` no longer negates `src/headstart/space_mcp/**` or
`src/headstart/mcp_protocol/**`, since the Space imports both. ADR-0253 required this "in the same
change". `resume_mcp` keeps its negation, since the Space still never imports it. A tool change now
deploys the Space, and deploys roll without downtime.

**Cost at the Space.** Importing the two packages and building the server on top of Flask takes
about 9 ms and 1.7 MB of peak RSS, measured locally three times on 2026-09-28. That is against a
boot of 4 min 13 s. Each tool call runs the same routes a browser search does.

## Options rejected

- **B, a separate MCP Space** and **C, local only**: see Context and the options doc.
- **Counting the in-process reads under the outer caller's address.** It would need the address
  threaded through the `Fetch` port into a header, and a header is what a caller can forge. The
  outer request is the unit a caller controls, so it is the one counted.
- **One global budget for `/mcp`.** One busy caller would lock everyone else out.
- **SSE replies and sessions.** Nothing here streams progress or asks the client for input, and
  both eras allow a single JSON reply.

## Risks, stated plainly

- **A booting Space answers with HF's edge error, not a sentence.** The stdio server waits out a
  boot and says the Space is starting. A hosted client meets the edge directly. Whether the
  pipeline's `restart_space` rolls like a deploy has not been measured; if it does not, claude.ai
  sees errors for about four minutes after each pipeline run.
- **The Anthropic range is one budget.** At 300 a minute, heavy claude.ai use could meet it and
  refuse everyone there together. The Space's logs will show 429s if it does.
- **The Origin log is the only evidence of what Anthropic sends** until a real connection is made.
  If claude.ai's in-chat client sent an `Origin` outside the list, every chat call would be a 403.
  The first connection's log line settles it.
- **Prompt injection is unchanged in kind and wider in audience.** The same tools quote scraped
  text the same way (`scraped_text`), but anyone can now call them, and read-only tools may run
  without per-call confirmation.

## Consequences

- The connector URL is `https://imposeidon-headstart-search.hf.space/mcp`, with no sign-in. The
  how-to, including the limits, is `docs/agents/space-mcp-server.md` §"Use it without installing".
- Tests: `tests/test_mcp_protocol_streamable_http.py` covers the adapter: both eras, Origin, 202,
  413 and the header errors. `tests/test_mcp_protocol_messages.py` covers the modern era in
  `handle`. `tests/test_space_mcp_against_space_app.py` covers initialize, then tools/list, then
  tools/call for every tool against the real app, legacy and stateless. `tests/test_space_app.py`
  covers the public-path set, POST only, the limits, and the in-process exemption.
- PRIVACY.md says `/mcp` counts addresses as the read routes do.
