# ADR-0258: The Space's read routes answer anyone

**Status:** accepted · **Date:** 2026-09-28 · **Supersedes:**
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)'s credential
(`AGENT_TOKEN`) and [ADR-0042](0042-signed-in-ui-saved-sets.md)'s 2026-08-13 amendment (the wall's
`ALERTS_TOKEN` exception) · **Relates to:** [ADR-0035](0035-email-job-alerts.md) (the Digest run,
a caller of `/search`), [ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md)
(what a Trends answer costs)

## Context

ADR-0253 let one agent past the sign-in wall with a shared secret, `AGENT_TOKEN`, so that Claude
Code could read HeadStart's Search, Trends and Hot through the Space MCP server. The same day the
owner widened the goal: "i want anyone to use the mcp server of headstart, if that cant be done
because of th sign in wall on search and trends then remove that from there".

A token cannot do that. `AGENT_TOKEN` is one shared secret: handing it to anyone who wants to run
the server makes it a public password, and rotating it breaks every copy at once. Per-person
credentials (the per-Account minted tokens ADR-0253 rejected) would be new Account machinery, and
each person would still have to sign in to get one. And the wall never guarded what these routes
serve: it checks identity, not entitlement. Sign-up is open to any Google address (ADR-0042), so
every answer these routes give was already one Google sign-in away from anyone.

## Decision

**The read routes join `_PUBLIC_PATHS`:** `/search`, `/facets`, `/trends`, `/hot`,
`/companies/suggest` and `/companies/lookup`. This reverses an option both ADR-0042's amendment
("Make `/search` public again") and ADR-0253 ("Make the read routes public") rejected, because
opening the routes would silently un-ship the wall's widest change. The owner reverses it on
purpose, for the reason above.

**They read, and only read.** Each accepts GET alone. The wall is keyed on the path, not the
method, so a write added to one of these paths would be public the day it landed; a test pins
every read route to GET.

**What stays walled**, unchanged: every Account route and `/signout`. The Account routes are Saved
sets, Saved jobs, the Profile and its résumé parse, résumés, `/subscribe`, and `/companies` both
ways: its GET reads the follow and hide lists, its POST follows, hides or clears. The page at `/`
still shows the door until its visitor signs in; only the JSON read routes open. A test walks the
app's URL map and asserts the wall's 401 on every route outside the pinned public set, so a new
route is walled unless someone opens it by name.

**A session still applies its own Account.** A signed-in caller's `/search` and `/facets` still
carry its follow and hide clause, which `_company_where` reads from the session as before. No
route serves one Account's records to another. An anonymous caller, the MCP server among them,
gets no clause, so a company its owner hid on the website is not hidden from an agent's search.

**No machine secret opens the wall any more.** `AGENT_TOKEN` opened only these routes, and
`ALERTS_TOKEN` opened only `/search`, so both now unlock nothing. Both leave the app with the
check that read them (`_SERVICE_TOKENS`, `_service_caller`); the Digest run stops sending
`ALERTS_TOKEN` (`alerts/space_query.py`, `alerts.yml`); README, CONTEXT.md and
`docs/email-alerts.md` stop asking for either. A Space or Actions secret still set is ignored and
can be deleted. The filter harness (`scripts/eval/verify_filters.py`) drops its session cookie
and runs anonymous, so no Account's hidden companies narrow what it checks.

The `X-HeadStart` marker and `agent-api=1` (ADR-0253) are unchanged: they describe the routes'
contract, not who may call them.

## The risk, stated plainly

- **Anonymous load on a free CPU Space.** Every new query is an encoder call and a vector search
  on the one Space the product runs on, and a Trends question not yet asked this boot costs up to
  ~1.5 s of its CPU (ADR-0251). New Trends answers are worked out one at a time under
  `_TRENDS_ANSWERING`, and the kept answers are the last 128 (`_TRENDS_KEPT`), so a caller asking
  many distinct Trends questions queues everyone's and pushes the opening views warmed at boot out.
  Nothing throttles it yet.
- **Bulk copying, page by page.** The index can be read out through `/search`. It is bounded only
  per request: `k` ≤ 100 rows and `page` ≤ 20, so one query, sort and filter set yields at most
  2,000 rows, but a caller may walk as many of those as it likes.
- **Any website can ask.** HF's edge answers a CORS preflight itself and echoes any `Origin`
  (measured 2026-09-28 on `OPTIONS`, from `evil.example.com`), so a page elsewhere can have its
  visitors' browsers call these routes. They serve nothing an anonymous caller cannot already
  read, and the session cookie is `SameSite=Lax`, which a cross-site fetch does not carry.

**Proposed follow-up: a per-client rate limit on the public routes**, generous enough for a person
or an agent, refusing with a 429 the MCP server can name. It is not built here; this change opens
the routes and removes what opening them made dead.

## Options rejected

- **Keep `AGENT_TOKEN` and hand it out.** A shared secret given to anyone is a public password
  that cannot be rotated without breaking every copy.
- **Per-Account minted tokens.** Right if a tool ever reads or writes one Account's records, and
  new Account machinery that still needs a Google sign-in first. Deferred until such a tool exists.
- **Keep `ALERTS_TOKEN` for a later rate-limit exemption.** It opens nothing today; if the limit
  ever needs to exempt the Digest run, that change can bring a credential back with that reason.

## Consequences

- Anyone can run the Space MCP server with no account, token or key.
- The page loses its "Your session expired — sign in again" message on Search, Matches and
  Trends, since those routes no longer refuse a lapsed session. A visitor whose session lapses
  with the page open now sees results without their hidden companies rather than a prompt to sign
  in; the Account routes still refuse them.
- The Space's read routes are now an open API on a free CPU Space, with the risks above.
