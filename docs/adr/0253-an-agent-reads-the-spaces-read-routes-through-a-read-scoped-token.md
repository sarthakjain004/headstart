# ADR-0253: An agent reads the Space's read routes through a read-scoped token

**Status:** accepted · **Date:** 2026-09-28 · **Amends:**
[ADR-0042](0042-signed-in-ui-saved-sets.md) (its 2026-08-13 amendment: the wall admits a second
machine, on the read routes), [ADR-0156](0156-the-space-installs-headstart-as-a-real-package.md)
(the deploy trigger leaves out the three MCP packages) · **Relates to:**
[ADR-0035](0035-email-job-alerts.md) (the other machine caller),
[ADR-0137](0137-an-agent-reads-a-resume-by-running-the-resume-tabs-own-javascript.md) (the Résumé
MCP server), [ADR-0194](0194-job-search-absorbs-what-its-adapters-copy.md) (one serving
implementation) · **Plan:** `docs/mcp/2026-09-28_space-mcp-server-plan.md`

## Context

The owner wants an agent, Claude Code in this repository first, to answer questions such as "remote
backend roles in Bengaluru paying over ₹30 lakh", "is Stripe hiring more or less than two weeks
ago?" and "which companies are expanding fastest this week?" from HeadStart's own index, with the
numbers the browser shows for the same question.

Every one of those answers is computed by the deployed Space: `JobSearch` for Search and its Facet
counts, `TrendHistory` and `line_reading` for Trends, the Hot ranking made once at boot, and a few
rules that live only in `deploy/hf-space/app.py` (the `board=`/`family=`/`role=` hand-off clause, a
family's predecessors, "a company is all its Boards"). All of it sits behind the Google sign-in
wall. The one machine credential, `ALERTS_TOKEN`, opens `/search` alone, by ADR-0042's amendment,
"so a leaked token buys a search rather than an account".

Measured on 2026-09-28 (the plan's §4): the served table is 4,045 MB; a Space boot took 4 min 13 s,
155 s of it ranking Hot; the default `/trends` payload is 406 kB.

## Decision

### Where the agent's server runs

Three topologies were weighed:

- **A. A local stdio MCP server that reads the deployed Space's read routes** over HTTPS.
- **B. A local server that answers in-process** over local copies of the served table and the
  Trends state.
- **C. An MCP endpoint served by the Space itself**, a `/mcp` route with a stdio bridge.

**A.** It gives answers identical to the browser's by construction, because the Space runs the one
implementation (ADR-0194). B needs the 4 GB table and torch on a laptop, pays 44–155 s to rank Hot,
and would be a third copy of the rules that live only in `app.py`; `scripts/ui/serve.py`, the second
copy, has already drifted from it (it skips the family-predecessor rule). C pays off only once a
remote client exists, such as a claude.ai connector. Until then it turns every change to a tool's
wording into a Space deploy, a restart costing about four minutes of availability, and adds an
internet-facing JSON-RPC surface. A keeps C cheap for later: the tools depend only on their Space
client, so moving them behind a Space route is a new adapter, not a rewrite.

### The credential: `AGENT_TOKEN`

A new Space secret, `AGENT_TOKEN`, separate from `ALERTS_TOKEN`, so each can be rotated alone and
neither widens the other. The wall's single path set becomes `_SERVICE_TOKENS`, a map from each
secret to the paths it admits:

- `ALERTS_TOKEN` keeps exactly `/search`.
- `AGENT_TOKEN` admits `/search`, `/facets`, `/trends`, `/hot` and `/companies/suggest`, and
  `/companies/lookup` once that route exists (below). These are the read routes that answer
  without an Account: `_account_gate` returns nothing without a session email, so no Account's
  follow or hide clause applies to them and no Account's records are reachable through them.

Unchanged from the amendment: an unset secret admits nobody, and a presented token is compared in
constant time on latin-1 bytes. If the two secrets are set equal, one secret would open both path
sets, so `AGENT_TOKEN` is ignored and boot prints one loud line saying so. That is not fatal: Search
is the product, and a misconfigured agent secret must not take it down.

**Blast radius.** A leaked agent token reads what any Google-signed-in visitor can already read
(sign-up is open to any Google address, ADR-0042), nothing Account-scoped, and cannot write.
Rotating it is one Space secret change, which restarts the Space, and one change on the agent's
machine.

**The cost, stated plainly:** an agent's search applies no Account's hidden-company list. The
server's instructions and its search tool's description say so.

### The app marks its own replies

An `after_request` hook adds `X-HeadStart: app; agent-api=N` to every response the app produces.
Flask 3.1.3 runs the hook on an unhandled-exception 500, on a 404 and on the wall's 401 as well as
on a route's own answer (measured, and pinned by tests on all four). The header says two things:

- **The app answered**, not HF's edge in front of a booting or sleeping Space. A client can then
  tell an app failure, which retrying will not fix, from a Space that is starting, which it will.
- **Which agent contract the app serves.** `_AGENT_API_VERSION` names it. It goes up whenever the
  Space-side contract an agent relies on changes: what `strict=1` means, the lookup route, the
  suggestion fields. A client that needs version N stops on an older app instead of having a newer
  argument silently ignored.

The header is introduced at `agent-api=0`. The version becomes 1 in the change that lands the
contract below, so `agent-api=1` is only ever served by an app that has all of it.

### Strictness and company lookup (landing in the next change)

These land in the change after this one, with `agent-api=1`, and this record is updated then:

- **`strict=1` on `/search` and `/facets`.** Today a filter value outside its whitelist is dropped
  and the search runs wider, with only a log line, so that a stale bookmark never errors. Under
  `strict=1` every such silent drop, re-scope or widening is refused instead: a caller's error
  answers `400 {"error": "invalid filter", "detail": …}` naming the value and the accepted ones, and
  a state of the deployment (no role assignments loaded, a column the table has not migrated yet)
  answers 503. Without `strict`, behaviour is unchanged.
- **What a suggestion is.** Each `/companies/suggest` item gains its `match` kind (`exact`,
  `prefix`, `words`, `typo`, `joined` or `alias`) and its `board_keys`, so the rule "accept a
  name only when it matches exactly" lives with the ranking that produces the match.
- **`/companies/lookup?board=…`** answers the directory company for any of its Board keys,
  case-blind, through the map `/trends` already uses to accept any Board of a pick, and refuses a
  key the directory does not hold. `AGENT_TOKEN` admits it.
- **`/facets` gains `newest_tick`**, the newest Trends tick, which dates the data an answer came
  from. The page does not read it.

### The deploy trigger leaves out the MCP packages

`deploy-space.yml` keeps its `paths` list and appends three negated patterns after the positive
ones: `src/headstart/space_mcp/**`, `src/headstart/resume_mcp/**` and
`src/headstart/mcp_protocol/**`. These packages run on an agent's machine and the Space never
imports them, so a change to one would restart the Space for nothing. Negations, because `paths`
and `paths-ignore` cannot both be set for one event, and a negation must follow a positive pattern.

This departs from ADR-0156's "the tree that ships" for the **trigger only**. The copy still ships
the whole `src/headstart` tree, so the Space receives these packages with the next real deploy.
ADR-0156 kept the trigger whole because a newly Space-relevant module left off a curated list would
ship late; that cannot happen for a package the Space does not import. If the Space ever imports
one of them, its negation must go in the same change.

## Options rejected

- **Widen `ALERTS_TOKEN`.** It is an Actions secret. Putting it on a laptop and widening it undoes
  the amendment's "a leaked token buys a search".
- **The person's session cookie.** The only credential under which the agent sees exactly the
  person's browser, hidden companies included, but it is a whole-Account, write-capable credential
  kept in a file and pasted by hand every 30 days or sooner (`PERMANENT_SESSION_LIFETIME`).
- **Per-Account minted tokens.** Right for a second person or a claude.ai connector, and new Account
  machinery for one user today. Deferred until either exists.
- **Make the read routes public.** Rejected for the reason ADR-0042's amendment gave for `/search`:
  it silently un-ships the wall's widest change.

## Consequences

- Merging this changes nothing until the owner sets `AGENT_TOKEN` in the Space's secrets (which
  restarts the Space) and configures the local server. Unset admits nobody, so this is safe to merge
  first.
- The Space's read routes become a contract with a client outside the browser. `agent-api` names
  its version; the Space tests pin each secret's exact path set, the agent token's refusal on the
  Account routes, and the header on all four kinds of reply.
- Every reply carries one more header. The browser does not read it.
