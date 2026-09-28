# A hosted MCP endpoint for HeadStart: options

**Status:** accepted, Option A, as [ADR-0267](../adr/0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) · **Date:** 2026-09-28 · **Builds on:** the Space MCP server
(draft PR #793, `src/headstart/space_mcp/`), `docs/mcp/2026-09-28_space-mcp-server-plan.md` §7.1
and §13, ADR-0253 and its public-read-routes amendment

The owner wants anyone to be able to use HeadStart's MCP server. Today it is a local stdio process
that anyone with Python can install, but claude.ai (web, desktop, mobile) cannot run one: a custom
connector is a **URL** that Anthropic's cloud calls [^1][^2]. How should HeadStart serve one?

## What the client side requires

- **claude.ai.** A remote server over Streamable HTTP. Authless (`none`) is "supported by
  default". Results are capped at about 150,000 characters, and a tool call times out after
  240 s. Traffic comes from `160.79.104.0/21`. A Free plan may add one custom connector [^1][^2][^3].
  Every tool must carry `readOnlyHint` and `destructiveHint` [^4], and our tools already do.
- **Protocol era.** Anthropic's docs do not name a revision. Two third-party logs, from 2026-09-13
  and 2026-09-26, show the in-chat client sending `server/discover`, `tools/list` and `tools/call`
  on **2026-07-28** with no session. The connector-setup probe used a legacy `initialize` on
  2025-11-25 [^5][^6]. A legacy-only server answered claude.ai's `MCP-Protocol-Version: 2026-07-28`
  with a 400 and failed intermittently [^7]. Claude Code probes HTTP servers for 2026-07-28 and falls
  back when they lack it [^8]. **So a hosted endpoint must be dual-era.**
- **Transport** [^9][^10]. Both eras allow one `application/json` reply per POST, no SSE, no
  session; GET and DELETE get 405, a notification 202, a present-but-invalid `Origin` 403. Modern
  requests mirror `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` into headers: a mismatch is
  400 `-32020`, an unsupported version 400 `-32022` listing `supported`, an unknown method 404
  `-32601`. Modern results carry `resultType: "complete"`, and `server/discover` and `tools/list`
  add `ttlMs` and `cacheScope` [^11][^12]; `ping` and `initialize` are legacy-only.

## Measured for this doc (2026-09-28)

- **The unchanged `stdio.handle` already serves the legacy era from Flask.** A probe route passing
  the JSON body to `handle` answered `initialize`, `notifications/initialized` (202), `tools/list`
  and `tools/call` in under 2 ms, the tools reading the app in process. `server/discover` returns
  `-32601` today, so no modern client can use it.
- **In-process sub-requests work under the threaded dev server** (`app.run`, Flask 3.1.3): 8
  concurrent outer requests, each fanning out 4 nested `test_client().get` calls on a thread pool
  as `search_jobs` does, finished in 0.12 s, correct, and **no inner request inherited the outer
  caller's session**.
- **The inner request reaches the app as `127.0.0.1` with no `X-Forwarded-For`**, so a limit keyed
  on the address would count every MCP user as one client.
- **HF's edge answers CORS preflight itself and reflects any `Origin`.** `OPTIONS /mcp` from
  `Origin: https://evil.example.com` got a 200 without our `X-HeadStart` marker, allowing that
  origin, POST and the requested headers. Any website can POST JSON to the Space from a visitor's
  browser and read the answer, so the app's own `Origin` check is the only gate (the newly public
  read routes included).
- Importing `headstart.space_mcp.server` costs about 68 ms and 14 MB RSS, measured locally.

## Protocol work, needed by A and B

1. **Era dispatch in `handle`, not in a transport.** A request whose `params._meta` carries
   `io.modelcontextprotocol/protocolVersion` is modern: `server/discover`, the `-32022` check,
   `resultType`, `ttlMs`/`cacheScope: "public"` (one tool list for everyone) and `serverInfo` in
   the result `_meta`. Anything else keeps today's legacy path. Stdio gains 2026-07-28 for free
   (the plan's §7.5 follow-up).
2. **`mcp_protocol/streamable_http.py`**, framework-free:
   `answer(method, headers, body, server, allowed_origins) -> (status, headers, body)`: the Origin
   gate, 405, a body-size cap, JSON parsing, 202, the modern header checks, JSON-RPC error to HTTP
   status. No `Mcp-Session-Id` is minted; one sent is ignored.
3. With two transports, `stdio.py` no longer names `handle` and `Server` honestly (CLAUDE.md §3):
   they move to e.g. `mcp_protocol/messages.py`, and `stdio.py` keeps `serve`.

## The options

### A. `/mcp` on the existing Space, answered in process

**What it takes:**

- A POST route in `deploy/hf-space/app.py` that calls `streamable_http.answer`, with `/mcp` added
  to `_PUBLIC_PATHS`.
- `space_mcp.server.build_server(fetch=…)`.
- `space_client.wsgi_fetch(wsgi_app)`, the e2e tests' `flask_fetch` promoted to production, with
  Werkzeug imported lazily so the base install stays at two packages.
- `deploy-space.yml` drops its `space_mcp` and `mcp_protocol` negations. ADR-0253 requires this
  "in the same change" once the Space imports them.
- A new ADR and a how-to section.

**Cost:**

- Boot and memory barely change: 68 ms and 14 MB against a 4 min 13 s boot.
- Each tool call runs the same routes a browser search does.
- A tool-wording change becomes a Space deploy. Deploys roll with no downtime (measured
  2026-09-28), so the cost is build time, not availability. Whether the pipeline's
  `restart_space` also rolls has not been measured. If it does not, claude.ai sees HF edge errors
  during each boot.

**Rate limit:**

- Exempt in-process sub-requests from the limiter, marked by a WSGI `environ` key no outside
  caller can set (not a header).
- Budget the outer `/mcp` request instead: per client, plus a global cap on concurrent
  `tools/call` to protect 2 vCPU [^13].
- **All claude.ai users arrive from one Anthropic `/21`**, so a per-address limit sized for one
  person throttles them all together; `clientInfo` is unauthenticated [^3], so without OAuth there
  is no per-user key.
- Do not use the client's per-process `RequestBudget` (60 a minute) in process: it would become
  one budget for everyone.

**Security:**

- Authless, reading only what the public routes already serve. The inner requests carry no
  cookie, so no Account state reaches an answer even from a signed-in browser.
- Allow a missing `Origin`, `https://claude.ai`, `https://claude.com` and the Space's own origin;
  answer 403 to anything else. The check matters because the edge reflects every origin.
- The prompt-injection surface is unchanged (same tools, same `scraped_text` quoting); the
  audience is wider, and read-only tools may run without per-call confirmation [^4].

**One implementation:** one `REGISTRY` and one `call`, over two transports (stdio and HTTP) that
both run through `handle`.

### B. A separate small MCP Space calling the main Space over HTTPS

**What it takes:** a new `deploy/hf-mcp-space/` (Dockerfile, a ~30-line app on the same
`streamable_http` and `build_server` with `urllib_fetch`), its own deploy workflow and a new Space.
The protocol work is the same as A's.

**Cost and security:**

- **Creating a Docker Space now requires a paid plan** (PRO) [^13]; whether the owner has one is
  unchecked.
- An extra HTTPS hop per route read, plus the new Space's own 48-hour sleep (`gcTimeout: 172800`,
  from the Space API) and cold start.
- The main Space sees every MCP user as one address, so the per-client limit collapses into one
  shared budget unless a secret between the Spaces returns, undoing what the public-routes change
  just removed.
- **Its isolation is illusory.** Encoding, LanceDB and Trends still run on the main Space; only
  JSON-RPC parsing and rendering move, measured under 1 ms.

**What it gains:** a tool change never redeploys the main Space, and `SpaceClient`'s edge wait
works as designed, so a booting main Space gets the honest "the Space is starting" reply instead
of an edge error.

### C. Stay local-only; publish install instructions

No hosted work. Claude Code users already have one command, and a `.mcpb` bundle could reach
Claude Desktop [^14]. Web and mobile users are **not** served, so C fails the goal as stated. It
stays in place alongside A or B either way.

## Testing, for A (B identical against its own URL)

- **Unit:** `tests/test_mcp_protocol_streamable_http.py` (403, 405, 202, `-32020`, `-32022`, the
  modern 404, both eras); modern-era cases in the protocol tests.
- **End to end:** `tests/test_space_app.py` POSTs `/mcp` to the real app in both eras.
- **Live:** `npx @modelcontextprotocol/inspector@2.8.0 --cli https://imposeidon-headstart-search.hf.space/mcp --transport http --method tools/list`,
  then `tools/call` [^3].
- **Conformance:** `npx @modelcontextprotocol/conformance@0.2.0-alpha.11 server --url … --scenario <s>`
  for `server-stateless`, `caching`, `http-header-validation`, `tools-list`, `server-initialize`
  and `ping` (stable 0.1.16 has no 2026-07-28 scenarios). `dns-rebinding-protection` needs a
  localhost URL, so it runs locally; the image, resource, prompt and input-required scenarios
  target the reference server's fixtures and go in `--expected-failures`.

## What the owner does

Nothing on HF: no secret. In claude.ai, **Customize → Connectors → Add custom connector**, the URL
`https://imposeidon-headstart-search.hf.space/mcp`, **No sign-in** [^2]. Claude Code users need no
Python: `claude mcp add --transport http headstart-space <url>` [^8].

## Recommendation: A

- **Seam.** `handle` (dict in, dict out) is where the protocol meets a transport; with stdio and
  Streamable HTTP as two adapters it becomes a real seam, not a hypothetical one. The `Fetch` port
  gains a second *production* adapter, and it is the one the e2e tests already run against the
  real `app.py`, so no user is served through an untested adapter.
- **Depth.** `streamable_http.answer` hides Origin, headers, era and status mapping behind one
  call; the Flask route stays about five lines.
- **Locality.** Era rules in one protocol module for both servers, tools in one registry, search
  rules in the Space.
- **B does not pay for itself:** a hop, a second deployable, a possible paid plan and a credential
  problem, for isolation that misses the expensive work.
- **Both of the plan's objections to a Space route (§7.1) have weakened.** Deploys roll without
  downtime, and the read routes are public anyway, so `/mcp` exposes nothing an anonymous caller
  cannot already read.

## Questions for the owner

1. **Dual-era or modern-only?** Dual-era recommended (claude.ai's setup probe is legacy).
2. **The Origin allowlist** above; the first real connection should log the `Origin` it receives.
3. **Budgets** for `/mcp` per client, for Anthropic's `/21`, and a global concurrency cap, agreed
   with the rate-limit branch.
4. **The module rename:** `mcp_protocol/messages.py`, or another name.

## Sources

[^1]: <https://claude.com/docs/connectors/building/> (transports, 150k characters, 240 s)
[^2]: <https://claude.com/docs/connectors/custom/add-unlisted> (No sign-in, one connector on Free);
    <https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp>
[^3]: <https://claude.com/docs/connectors/building/authentication> (`none` by default,
    `160.79.104.0/21`); <https://claude.com/docs/connectors/building/testing> (Inspector, Origin
    pitfalls, `clientInfo` unauthenticated)
[^4]: <https://claude.com/docs/connectors/building/mcp>;
    <https://claude.com/docs/connectors/building/review-criteria>
[^5]: <https://github.com/anthropics/claude-ai-mcp/issues/1027> (third party, 2026-09-13)
[^6]: <https://github.com/openmobilehub/credentagent/issues/200> (third party, measured 2026-09-26)
[^7]: <https://github.com/maldandan78/telegram-mcp/pull/3> (third party, 2026-09-14)
[^8]: <https://code.claude.com/docs/en/mcp> (negotiation, `--transport http`)
[^9]: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
[^10]: <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>;
    <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
[^11]: <https://modelcontextprotocol.io/specification/latest/changelog>;
    <https://modelcontextprotocol.io/specification/2026-07-28/server/discover>
[^12]: <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching>
[^13]: <https://huggingface.co/docs/hub/spaces-overview> (CPU Basic 2 vCPU/16 GB; paid plan for
    Docker Spaces)
[^14]: <https://claude.com/docs/connectors/building/mcpb>
