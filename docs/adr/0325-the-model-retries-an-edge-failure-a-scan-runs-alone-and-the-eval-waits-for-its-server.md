# ADR-0325: The model retries an edge failure, a scan runs alone, and the eval waits for its server

**Status:** accepted; revised the same day by the code review of the PR that shipped it, before
anything relied on it (the scan place now waits for abandoned reads before a scan starts) ·
**Date:** 2026-09-29 · **Amends:**
[ADR-0276](0276-a-hosted-mcp-call-ends-at-its-deadline-and-no-caller-holds-every-place.md) (the
`/mcp` places, and its claim that the hosted eval ran) · **Relates to:**
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (the hosted MCP
server), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the `X-HeadStart` marker the installed server retries on),
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(the description keyword's cost)

## Context

The round-2 critique of the hosted MCP server (2026-09-29, 6.0/10) found three faults this ADR
settles: Hugging Face's edge fails hosted calls and clients give up (its P0-1), every claude.ai
user shares two places that description scans hold for 20–45 s (P1-7), and the hosted eval cannot
run (P1-8).

**The edge fails about one call in seven, before the app sees it.** 10 of 71 hosted tool calls,
and 3 of 34 pings, came back in 0.7–1.6 s as HTTP 502 with Hugging Face's HTML page (the page
itself says "500"). The unrelated `baw-appie-httpbinorg.hf.space` failed 5 of 26 beside our 7 of 26
in the same minutes, and the Space's run log shows none of those requests. We cannot fix the edge.
Claude Code 2.1.212 sends one POST per tool call and shows the model `Streamable HTTP error: Error
POSTing to endpoint: <!DOCTYPE html>…` (ADR-0276 measured the same for a 429 and a 503). A real
`claude -p` met the page twice on `find_company` and told the user HeadStart "may be
misconfigured".

**A description scan is CPU-bound.** Measured on the hosted `/mcp` from one address, 2026-09-29
05:11–05:15 UTC, a `search_jobs` call with `keyword_in: description` and a distinct keyword each
time, so no kept answer served it:

| Calls at once | Wall time of each |
| --- | --- |
| one scan | 18.4 s, 18.1 s, 15.9 s |
| two scans | 35.4 s and 35.6 s; 29.2 s and 32.7 s |
| one fast search (no keyword) | 5.2 s, 5.2 s |
| one fast search beside one scan | 7.7 s (the scan: 19.1 s) |

Two scans at once each took about twice one alone, so running them together finished neither
sooner. The critique's pair on a replica 23 s after its boot took 46–47 s each, past the 45 s
deadline, and so failed both. A fast search beside one scan slowed by 2.5 s. Under ADR-0276 a scan
held one of the 4 places, 2 of them per caller, and Anthropic's whole range is one caller. Two
claude.ai users running scans held the range's share for 20–45 s, and every other claude.ai call
waited 10 s and got a 429.

**The hosted eval never reached a tool.** `space_mcp_eval.py --http` scored 0 of 21 with 0 tool
calls. Claude Code 2.1.212's `-p` starts the run before an HTTP server connects: the init event
shows `"status": "pending"` and `tools: []`. With `MCP_CONNECTION_NONBLOCKING=false` it waits and
connects. A run whose server failed to connect was scored as the model's miss. ADR-0276 cited a
results doc for a run that could not have happened. And the verifiers checked arguments: t08
("Rust" in the title) passed while 15 of 24 rows it would show were "Trust" and "Thrusters".

## Decision

**1. The server's instructions tell the model to retry an edge failure.** One sentence joins
`INSTRUCTIONS` (`space_mcp.server._INSTRUCTIONS_EDGE_RETRY`, 198 characters, last, where it was
measured; the whole stays under the 2,048 Claude Code reads):

> A Hugging Face error page (it says 500) or an HTTP 502 or 503 is a passing fault in Hugging
> Face's edge, not HeadStart; every tool only reads, so retry the same call up to twice before
> reporting it.

Every tool only reads, so a repeated call changes nothing. The sentence names the page by what the
model sees (the page's "500"), since Claude Code's error text carries the body, not the status.

*Measured with a control* (`scripts/eval/space_mcp_edge_retry.py`). Each run is one `claude -p`
task against a Streamable HTTP endpoint on the measuring machine. The endpoint serves the real
server (`build_server()`, reading the deployed Space) and answers the first two `tools/call`
POSTs of the run with the page a real edge 502 carried (`space_mcp_edge_retry_hf_502_page.html`), with
HTTP 502 and no `X-HeadStart`. It answers everything else. The two arms differ only in the
instructions. The hosted connector would have been the weaker control. Its edge fails at random,
about 14% of calls, so ten tasks meet one or two failures and the two arms meet different ones.
And the `after` arm could not run there before the sentence was deployed. The run: 10 iteration
tasks (t01–t05, t09, t14, t16, t19, t21), each run twice per arm, `claude -p` 2.1.212, the
default model, 2026-09-29:

| Arm | Runs | Recovered by retrying | Recovered elsewhere | Gave up |
| --- | --- | --- | --- | --- |
| before (no sentence) | 20 | 14 | 5 | 1 |
| after (the sentence) | 20 | 20 | 0 | 0 |

A run recovered when some call after the second failure was answered: "by retrying" when the
call right after that failure repeated the failed tool, "elsewhere" when it went to another
tool. Without the sentence the model mostly retried anyway. The run that gave up (t04,
`hiring_now` twice) told the user "The HeadStart data service is down right now", as the
critique's run did. In the 5 runs that recovered elsewhere, `find_company` failed twice and the
model went to `search_jobs` or `company_profile` without the lookup it had asked for. With the
sentence, all 20 repeated the failed call and reached an answer; in one (t16) the repeat was
refused for its arguments, and a later search was answered. The measured gain is small, 1
give-up in 20 against 0. It is how one model behaved, not a guarantee. Claude Code's transport
sent exactly one POST per tool call in every run, so every retry was the model's.

**2. The installed server is the path that retries the edge itself.** `space_client.SpaceClient`
already treats a reply without `X-HeadStart` as the edge and re-reads it for up to the call's 45 s
(waits of 5, 10, 20 s), so a `uvx` or checkout install never shows the model an edge page unless
the edge fails that whole time. `docs/agents/space-mcp-server.md` §Limits says so beside the edge
failure rate.

**3. A description scan runs alone, on a place of its own.** `/mcp` reads the body before its
limits (it already did, for the refusal's id, ADR-0276), and `streamable_http.tool_call` names
the tool a `tools/call` asks for. A `search_jobs` call whose `keyword` is set and whose
`keyword_in` is `description` or `both` (`search_jobs.scans_descriptions`) takes the one scan
place (`_MCP_SCANS_AT_ONCE = 1`) instead of one of the 4 places. It waits up to 10 s, as any call
does. Then it gets a 503 JSON-RPC error with `Retry-After: 20`: "HeadStart runs 1
description-keyword search at a time, and another is running; retry in about 20 s, or match the
keyword in titles (keyword_in: title), which is fast." Every other call keeps the 4 places, 2 per
caller, with Anthropic's range one caller, unchanged.

Nor does a scan start while any in-process read is running past its call's deadline. A call
that answers at the 45 s deadline leaves its reads running (ADR-0276 cannot stop them), and a
scan started then would share the CPU with them, as the critique's cold-replica pair did. So a
scan that has its place then waits, within the same 10 s, until `space_client.AbandonedReads`
(the count `wsgi_fetch` keeps for its cap, now shared with the route) is empty. Past that it gives
the place back and gets a 503 with `Retry-After: 60`: "HeadStart is still finishing an earlier
search that ran past its time limit, and starts a description-keyword search only once it has;
retry in about a minute, or match the keyword in titles (keyword_in: title), which is fast."
Every abandoned read counts, a fast call's too, since each burns the same CPU. A fast call does
not wait for them: its reads are refused only at `wsgi_fetch`'s cap of 2, as before.

Why one: the table. A second scan at once finishes neither sooner, and past a cold boot it makes
both miss the deadline. Queued, the first finishes in about 18 s and the second is told to come
back when it will be free. Why a place of its own: a fast call never waits behind a scan, which is
what cost claude.ai users their share. The range still shares 2 fast places. At 1–8 s a call,
those turn over many times a minute. A caller can now hold 2 fast places and the scan place at
once, 3 of 5. The CPU is bounded tighter than before, at 1 scan against ADR-0276's possible 4.
*Flip:* `_MCP_SCANS_AT_ONCE`. A full-text index on descriptions would make a scan a fast call and
retire the place (ADR-0274 names it). A larger fast share for the range is the other flip ADR-0276
names, for when a 429 "at once" from the range shows up in use.

**4. The hosted eval waits for its server and judges truth.**

- `--http` runs `claude` with `MCP_CONNECTION_NONBLOCKING=false` (`run_env`).
- The parser reads the server's status from the init event. A run whose server was not
  `connected` is an `error`, not judged: the model had no tools, so the run says nothing about the
  model or the tools. The summary scores only judged runs ("correct: 19 of 20 judged") and names
  the rest on a line of its own, first ("not judged: 1 of 21 (t05) — MISSED").
- `title_keyword_rows` (t08) checks the arguments as `tool_args` does, then reads every row the
  meeting call returned back from `/job` by the ids it printed, and needs the keyword in each
  title where a word starts. That mirrors the keyword's own rule since
  [ADR-0299](0299-a-keyword-word-matches-where-a-word-starts-and-quotes-keep-a-phrase.md) on
  purpose, and is looser than a whole word: "rust" is in "Rust-based" and "Rustacean", not in
  "Trust". A verifier stricter than the product would fail rows the product promises. The rule
  is restated rather than imported, so a bug in the compiler cannot hide here.
- `hot_top` (t04) also fails an answer whose first-named hiring_now row is one the tool flagged
  (`hiring_now.FLAG_MARK`): leading with a row the answer then disowns is the answer's fault.
- `trend_sign` (t03, t15) also needs the answer to say that opened and closed cover only part of
  the window, when `/trends`' `turnover_since` falls after the window's start: the day counting
  began in any usual spelling, or the days counted within one day.
- `--repeat N` runs the set N times, prints each pass's summary as it ends, and tallies each
  task.

ADR-0276's sentence citing a results doc now says the run could not happen as shipped, and points
here.

## Recommendation for the owner: a retrying proxy (not built)

The instruction makes the model retry. It does not make an edge failure invisible, and it costs a
turn. The fix that does is a proxy the connector URL points at, which retries the edge before any
client sees it. It is outward-facing infrastructure (a new public host and an account), so it is
the owner's call. The design:

- **Where.** A Cloudflare Worker (the free plan: 100,000 requests a day, CPU time counted only
  while the Worker computes, not while it waits on a fetch). The Oracle box that hosts the router
  would need a public port, a domain and a certificate, where it opens only port 22 today.
- **What it does.** `POST /mcp` only. It forwards the body and the headers the protocol reads
  (`Content-Type`, `Accept`, `Origin`, `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`) to
  `https://imposeidon-headstart-search.hf.space/mcp`. A reply carrying `X-HeadStart` is the app's,
  so it passes through untouched, 429 and 503 included. A reply without it, a 500, 502 or 503, is
  the edge, so the Worker sends the same body again, up to twice, 0.5 s and then 1.5 s later,
  within 8 s in all. Every tool only reads, so a resend is safe. The edge answered in 0.7–1.6 s,
  so three tries fit. If the third fails too, the Worker answers a JSON-RPC error with the
  request's id and code 502: "Hugging Face's edge failed this request three times; HeadStart did
  not see it. Retry in a minute." That is ADR-0276's refusal shape.
- **Who the caller is.** The Space counts a caller by the last `X-Forwarded-For` address, which
  behind a Worker is Cloudflare's, so every user would share one budget. The Worker sends the
  caller's own address (`CF-Connecting-IP`) in a header signed with a secret the Space holds, and
  `_client_address` trusts that header only with the secret. Anthropic's range is then read from
  the real address, as now.
- **Measure it.** Run the hosted eval and a 100-ping burst through the Worker. The yardstick is an
  edge page reaching a client: 14% of calls now, and it should be 0 unless the edge fails three
  times running.
- **The owner's steps.** A Cloudflare account, one Worker deployed with `wrangler`, the secret set
  on both sides, and the Worker's URL published as the connector. The `hf.space` URL keeps working.

## Options rejected

- **Retrying in the app.** The app never sees an edge-failed request.
- **A larger share of the 4 places for Anthropic's range, in place of the scan place.** It lets
  claude.ai users hold 3 scans' worth of CPU, and a scan is CPU-bound: a third scan at once slows
  all three. Fast calls would still queue behind scans.
- **Two scan places.** Measured: two at once took 29–36 s each against 16–18 s alone, and both
  passed the deadline on a cold replica. One queued behind the other finishes sooner.
- **Telling a scan to wait longer for its place.** Claude Code gives up on a request at 60 s, and
  a call already waits up to 10 s for a place and then has 45 s (ADR-0276). A longer wait leaves
  the scan no time.
- **Scoring a run whose server did not connect as a fail.** That scores the harness, and it hid
  the whole hosted run behind 0 of 21.

## Risks, stated plainly

- **One scan place serves about three scans a minute** for everyone. The refusal points at the
  fast alternative, a title keyword, and gives the wait. A 503 "description-keyword search at a
  time" in use is the sign to build the full-text index.
- **The instruction may make a model retry a real HeadStart failure.** The sentence names Hugging
  Face's page and 502/503. The app's own refusals carry JSON-RPC sentences with their own advice,
  and its 503 "busy" is worth a retry anyway.
- **The span check reads dates and day counts.** An answer that says "since last Thursday" fails
  it. The detail names what it wanted, so a false fail is visible.
- **`hot_top`'s headline is the first row the answer names.** An answer that opens "The site ranks
  Bosch first, but that is re-counting" fails it. Leading with the row, even to disown it, is
  still leading with it.

## Consequences

- `tests/test_space_app.py` pins the scan place (1, a 20 s retry, a 60 s retry while reads past
  their deadline run), tells a scan from the body, checks that a scan waits only for its own place
  and a full house of fast calls does not keep it out, and that a scan does not start while a read
  past its deadline runs. `tests/test_space_mcp_space_client.py` covers `AbandonedReads`,
  `tests/test_mcp_protocol_streamable_http.py` covers `tool_call`, and
  `tests/test_space_mcp_tools.py` checks the instructions' budget with the new sentence.
- `tests/test_space_mcp_eval.py` covers the init status, `run_env`, a run left unjudged and
  unscored, `title_keyword_rows` (read back by id, "Trust" failing), `starts_a_word`, the flagged
  headline, the turnover span, the summary's "not judged" line and `tally`.
  `tests/test_space_mcp_edge_retry.py` covers the control's endpoint, its outcomes and its arms.
- `docs/agents/space-mcp-server.md` §Limits names the scan place, the edge failure rate, the
  instruction and the installed server as the path that retries the edge.
