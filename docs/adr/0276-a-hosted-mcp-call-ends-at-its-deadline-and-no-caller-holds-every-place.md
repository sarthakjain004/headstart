# ADR-0276: A hosted MCP call ends at its deadline, and no caller holds every place

**Status:** accepted; its places amended by
[ADR-0325](0325-the-model-retries-an-edge-failure-and-a-description-scan-runs-alone.md) (a
description scan has a place of its own), and its "still finishing" sentence by
[ADR-0320](0320-a-description-keywords-rows-are-found-once-literal-first-and-named-by-row-id.md) ·
**Date:** 2026-09-29 · **Amends:**
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (its budgets and its
refusal bodies) · **Relates to:**
[ADR-0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md) (how a
caller is counted), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the client's deadline and waits)

## Context

An independent critique of the hosted MCP server (2026-09-29, round 1, items P0-4, P1-7 and P1-8)
found three faults in how `POST /mcp` behaves under load. This ADR settles them. P1-8 asked for
the eval to run against the hosted endpoint. That run could not happen as this ADR shipped it:
Claude Code's `-p` left the hosted server "pending", so every task failed with no tool call.
[ADR-0325](0325-the-model-retries-an-edge-failure-and-a-description-scan-runs-alone.md) fixes the
runner and records the first hosted run that reached the tools.

1. **The deadline did not hold in process.** A tool call builds a `SpaceClient` with a 90 s
   deadline. On the Space its reads go through `space_client.wsgi_fetch`, which called the app
   synchronously and ignored `timeout_s`. A description-keyword search measured 102–125 s through
   the hosted endpoint, and the direct `/facets` read behind it measured 118.6 s.
2. **One caller could hold every place.** `_MCP_AT_ONCE` allowed 4 requests at once across all
   callers. The per-address limit counts requests a minute, not places held. So four slow calls
   from one address would hold all four places for two minutes.
3. **Refusals were not JSON-RPC.** A 429 or 503 on `/mcp` carried `{"error":…,"detail":…}`, the
   read routes' shape, with no request id.

Measured on 2026-09-29, against a local run of the real `app.py` with its heavy dependencies
stubbed as its tests stub them (`/search` held for 150 s), and against probe servers:

- **Clients stop waiting at 60 s.** Claude Code 2.1.212's HTTP transport aborted the call at 60 s
  with "The operation timed out." Its debug log shows `timeoutMs: 60000`. The MCP Inspector 2.8.0
  CLI stopped with "Request timed out after 1m00s". With `MCP_TOOL_TIMEOUT=120000`, Claude Code
  waited, and the model read the server's own deadline sentence at 90 s. claude.ai allows 240 s
  per tool call (claude.com/docs/connectors/building).
- **Neither protocol revision defines a rate-limit or overload answer.** The 2025-11-25 and
  2026-07-28 Streamable HTTP texts name 400, 403, 404 and 405, each with a JSON-RPC error body. They
  name no 429 or 503. The 2026-07-28 error-code rules say implementations SHOULD NOT use `-32000`
  to `-32019` (legacy) and MUST NOT invent codes in `-32020` to `-32099` (reserved for the spec).
  JSON-RPC 2.0 reserves `-32768` to `-32000` and leaves every other integer to the application.
- **Clients pass a refusal's body through verbatim and do not retry it.** For a non-2xx POST,
  Claude Code 2.1.212 gave the model `Streamable HTTP error: Error POSTing to endpoint: <body>` as
  an error result. It sent one POST for a 429 and one for a 503. The Inspector CLI printed the
  same body with the status. A 200 carrying a tool result with `isError` reached the model as the
  bare sentence.
- **Nothing identifies one claude.ai user.** Claude Code over HTTP sends the legacy `initialize`
  and no `Mcp-Session-Id`, since this server mints none. The 2026-07-28 revision removes sessions,
  and claude.ai's chats speak it (ADR-0267). A server there should neither mint nor echo a session
  id. The request's `clientInfo` is unauthenticated and names the client program, not the person.

## Decision

**1. A call ends at its deadline, in process too, and the deadline is 45 s.** `wsgi_fetch` runs
each read on a thread of its own (`space-mcp-read`) and waits at most `timeout_s`. Past that it
raises `DeadlinePassed`. The tool then answers with a result carrying `isError` and this sentence:
"HeadStart did not answer within this call's 45 s, so it stopped waiting. Narrow the filters, or
ask for the concise detail, and try again: a description keyword over a broad search is the
slowest kind." `build_server` gives an in-process read the call's whole deadline, because there is
no connection to lose there. Over HTTPS one attempt is still cut at 20 s and retried. `SpaceClient` also answers `DeadlinePassed`, not
"the Space is starting", when earlier reads in the same call have used up the time, since the app
has plainly answered.

`CALL_DEADLINE_S` is 45 s, down from 90 s. An answer after 60 s reaches no Claude Code or Inspector
user, and the route may first wait up to 10 s for a place. So 10 + 45 s plus the edge's round trip
stays under 60 s. The same constant bounds the stdio server's wait for a booting Space, which now
gives up at 45 s. At 90 s the model saw "The operation timed out." at 60 s, and the server went on
working for 30 s for no one. *Flip:* `CALL_DEADLINE_S`. A longer deadline for Anthropic's range
alone, where claude.ai waits 240 s, would need the route to pass the caller's range into the call.
That is worth it only if the Space's logs show claude.ai calls ending at the deadline.

**What happens to the abandoned read.** A Python thread cannot be stopped, and the read is a
LanceDB query or a facet count in native code. It runs to its end on its own thread and keeps a CPU
busy all the while. The slowest route measured 118.6 s, so an abandoned read can run about 75 s
past a 45 s deadline on one of the Space's two vCPUs. It also slows the website's own requests. It
holds no `/mcp` place, because its call has already answered. **The bound:** while
`ABANDONED_READS_CAP` (2) reads that outlived their call are still running, `wsgi_fetch` starts no
new read. It refuses at once with `SpaceBusy`: "HeadStart is still finishing earlier searches that
ran past their time limit; try again in a minute, with narrower filters if this is a
description-keyword search." Reads already running when the cap is reached may still be abandoned.
So the most abandoned at once is 1 plus the reads in flight at that moment: at most 4 places times
the two reads `search_jobs` sends in parallel. Each abandonment and each abandoned read's finish is
logged as a warning, with the route and never a parameter, so the Space's logs show how long the
tail runs. *Flip:* `ABANDONED_READS_CAP`.

**2. At most 2 of the 4 places per caller.** `headstart.serving.concurrency_limit.ConcurrencyLimit`
holds both caps behind one lock. It sits beside `rate_limit`, which bounds how often a caller asks,
while this bounds how much it holds. A request waits up to `_MCP_PLACE_WAIT_S` (10 s) until fewer
than 4 places are held in all and fewer than 2 by its caller. If its own share is full, it gets a
429: "at most 2 at a time from one address; retry when one of them is answered". If every place is
held, it gets a 503: "HeadStart is busy". A caller is counted as the request limit counts it: its
last `X-Forwarded-For` address (ADR-0262), or `anthropic` for `160.79.104.0/21`.

**Anthropic's range is one caller for places too.** Every claude.ai user shares that range, and
nothing per-user reaches the server (Context). A key the caller chooses, such as a session id or
`clientInfo`, would let one caller multiply itself by varying it, the same reason ADR-0262 counts a
signed-in caller as its Account. So claude.ai users together hold at most 2 places. A chat that
fires parallel slow calls cannot take the endpoint, and direct callers keep 2. The cost is that
claude.ai users contend for 2 places among themselves. A few-second call frees its place quickly,
and a caller waits up to 10 s for one. *Flip:* a larger share for the range, such as 3 of 4, or
OAuth, which would give a per-user key.

**3. A refusal is a JSON-RPC error carrying the request's id.** `streamable_http.refusal(body,
status, message)` reads the id from the body when it parses, else uses `null`, and answers
`{"jsonrpc":"2.0","id":…,"error":{"code":<status>,"message":…}}`. The route adds `Retry-After`,
and the app's `X-HeadStart` marker rides along as on every reply. The code is the HTTP status
itself, 429 or 503. No revision defines one, and 429 and 503 fall outside every reserved range. To
read the id, the route now reads the body (at most 64 KiB) before its limits instead of after. Both
eras get this shape, since both carry JSON-RPC errors over HTTP errors elsewhere (400 in the modern
era, 403 in both).

## Options rejected

- **A 200 with a tool result carrying `isError` for a refused `tools/call`.** The model reads that
  most cleanly (measured). But it hides a refusal behind a success status from HTTP-aware clients
  and from the Space's own request log. It cannot answer `initialize` or `tools/list`. And it would
  be the one refusal shaped unlike the 403, 413 and 400 refusals beside it.
- **Keeping 90 s.** It exceeds the measured 60 s at which the two TS-SDK clients stop waiting. The
  deadline would then never be the thing that speaks to them.
- **A bounded pool of read threads, counting active and abandoned reads together.** It caps work
  just as well. But a pool full of abandoned reads makes a fast read queue behind them until its own
  deadline, and then it is told to narrow filters it never needed to narrow. The cap on abandoned
  reads refuses at once, with the sentence that is true.
- **Holding the caller's `/mcp` place until its abandoned reads finish.** That would tie the route's
  places to the client's threads. The abandoned-read cap bounds the same CPU with no coupling.
- **Per-session places inside Anthropic's range.** No session exists to key on (Context).

## Risks, stated plainly

- **An abandoned read still costs its CPU.** The cap bounds how many start while others run past
  their deadline, not what one costs. ADR-0274's count-only `/facets` shrinks the tail for a
  concise answer. `detail: "full"` still asks for every option's count, which took 98.7 s under a
  description keyword, so it is the likeliest call to be abandoned. The warning lines measure it.
- **claude.ai loses calls that would have finished between 45 and 240 s.** The slow calls measured
  so far took 102–125 s, which is past 90 s too, so the old deadline rescued none of them.
- **The Anthropic range's 2 places may bind before its 300-a-minute budget.** Two places turning
  over every 1–3 s serve roughly 40–120 calls a minute. A 429 "at once" from the range in the
  Space's log is the sign.
- **Clients still show the refusal as raw JSON.** Claude Code prefixes the body with "Streamable
  HTTP error". The message inside reads cleanly to the model, but it is not a first-class MCP
  error there.

## Consequences

- The limits are pinned in `tests/test_space_app.py`: 4 in all, 2 per caller, a 10 s wait, and
  both refusal shapes with the request's id. `tests/test_serving_concurrency_limit.py` covers the
  caps. `tests/test_mcp_protocol_streamable_http.py` covers `refusal`, and that its codes fall
  outside the reserved ranges. `tests/test_space_mcp_space_client.py` covers the in-process
  deadline, the abandoned-read cap (a refused read never reaches the app), an exception in the app
  raised on the caller's thread, and the sentence when the reads have used the time.
  `tests/test_space_mcp_server.py` covers an in-process read being given the whole deadline.
- `scripts/eval/space_mcp_eval.py --http URL` registers the hosted endpoint instead of the stdio
  server. The eval counts the two new sentences as infrastructure, not as refusals the agent should
  correct.
- `docs/agents/space-mcp-server.md` §"Use it without installing" states the new limits and
  deadline.
