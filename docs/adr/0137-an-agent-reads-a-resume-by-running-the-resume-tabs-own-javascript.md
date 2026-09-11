# ADR-0137: An agent reads a Résumé document by running the Résumé tab's own JavaScript

**Date:** 2026-09-11
**Status:** Accepted

## Context

The Résumé tab (ADR-0123) builds a structured document an agent would be useful against: read it,
check it, talk about what is in it. The ask was for agents to "get the resume directly" and "view
what's already set in the data of the resume tab" — so, two capabilities, not one. Getting the
stored JSON is easy. *Reading* it is not, and that is the whole of this decision.

Three surfaces were considered for exposing it — a local MCP server, a documented in-page JS API,
and an HTTP token API. The MCP server was chosen: it runs on the person's own machine as a
subprocess of their agent client, under their own credentials, so it adds no internet-facing
surface, no shared token, and no new authenticated route on the Space.

That choice creates the hard problem. **A Résumé document does not mean what its JSON says.**

- What one block holds — which fields, what they are called, what kind each is — comes from the
  Component Type catalogue in `resume_components.js`. The JSON stores `{"n3": {"text": "…"}}`; that
  a `bullet` has a `text` field labelled "Sentence" and a `role` flag labelled "Opening summary
  sentence" is in the catalogue, not in the record.
- What one *version* says comes from `resume_document.js`'s `resolve(doc, tailoringId)` and
  `contentOf`: a Tailoring stores only differences, and merging them over the master is what turns
  a difference set into a résumé (ADR-0124).
- What one version *prints* comes from `resolve`'s recursive prune over two hidden lists — the
  document's and the Tailoring's (ADR-0128).

All three are JavaScript. The server is Python, because `alerts.store.Store` — the client for the
Subscriptions dataset, with the paths, the traversal guard and the "unreadable is not absent" rule
already in it — is Python and must not be written a second time.

## Options

**A. Re-implement `resolve` and the catalogue in Python.** No subprocess, one language. And a
second implementation of this feature's central rule, plus a hand-copy of a catalogue that gained
four Component Types in the eight days before this was written (ADR-0130, ADR-0135). This repo has
been bitten by exactly this shape repeatedly — a page-break class list that disagreed with the one
the printer used, two spellings of a keyword match that disagreed about `C++`. The drift would be
silent and it would be in the direction that matters: the agent confidently reporting a sentence
the résumé does not print.

**B. Shell out to Node and run the real functions.** One implementation. Node is already a hard
dependency of this repo's test suite (`node --test tests/js/*.test.js`, a pinned Node in CI), and
`tests/js/resume_harness.js` already demonstrates loading these browser scripts into a fresh `vm`
context with no build step — so the marshalling seam is about forty lines of JavaScript, not a
port. The costs are a subprocess per call and a JSON hop.

**C. Return the raw structure and do not resolve**, marking variants and hidden nodes so the agent
can see what is overridden. Honest and cheap, and it answers "get the résumé" completely. It does
not answer "view what's already set": the caller is handed the model and told to apply the rules
itself, which is the same drift as A with the drifting copy inside somebody's context window.

## Decision

**B.** `resume_mcp/inspect_document.js` loads `resume_components.js` and `resume_document.js` into
a `vm` context and answers with facts — every block, its type's declared fields, their values under
the requested version, whether it prints, which versions reword it. `inspection.py` runs it and
renders the answer; it decides nothing about the model, only about indentation and wording.

C is not discarded, it is the *other tool*. `get_resume` returns the stored document verbatim and
needs no Node at all, so the raw structure is always reachable; `inspect_resume` is the reading.

**There is no Python fallback reading.** When `node` is absent the tool says so and points at
`get_resume`. A second-best answer that quietly disagrees with the Résumé tab is precisely what
this decision refuses, and shipping one as a degradation path would reintroduce option A wearing an
apology.

### What it may not do

**One account, structurally.** The server reads the account named by its own environment
(`HEADSTART_ACCOUNT_EMAIL`, hashed through the same `subscription_id` the Space and the alerts run
use) and no other. This is a property of the design, not a rule to remember:

- No tool's input schema names an account, an address, a repo or a path, and every schema is closed
  (`additionalProperties: false`). The arguments are re-checked against the schema server-side,
  because a client is free to ignore one.
- `account.Account` derives the id at construction and exposes exactly two methods, neither of
  which takes an account. There is no parameter anywhere in that module through which another could
  be named, so adding one means editing the file that says why not.

Two tests fail the moment that is violated — one on the schemas, one on `Account`'s own signatures.
A security review on 2026-09-11 found no cross-account read path anywhere in this feature; this
server does not become the first.

**Read-only.** The browser is the working copy and always is (ADR-0124 decision 3). A push carries
`rev + 1` and is refused unless that is exactly one past what the store holds; the refusal is
recovered by keeping both copies and telling the user, which is a conversation the Résumé tab can
have and a subprocess cannot. A write tool here would be a second client of that protocol with none
of the recovery, and its failure mode is an agent silently rewriting someone's employment history.

**No dependency.** MCP's stdio transport is newline-delimited JSON-RPC 2.0 over a subprocess's
stdin and stdout. The `mcp` SDK brings pydantic, anyio, starlette and uvicorn to serve four method
names; this repo's base install is two packages. The transport is written out in `server.py`, so
the tests need nothing CI does not already install and none of them is an `importorskip`.

## Consequences

- One implementation of `resolve`, of `contentOf` and of the component catalogue. A Component Type
  added tomorrow is reported by this server the day it is added, with its real fields and labels,
  with nothing to update here.
- A subprocess per `inspect_resume` call — ~30 ms measured locally, against a tool call a person
  waits on. Not a cost worth a second implementation.
- Node becomes a requirement for one of three tools, on the user's own machine. `list_resumes` and
  `get_resume` do not touch it.
- The JavaScript half is tested in the JavaScript suite
  (`tests/js/resume_mcp_inspect_document.test.js`), which CI runs unconditionally on a pinned Node;
  the Python half is tested in `tests/test_resume_mcp.py`, whose one integration test skips when
  `node` is absent. Splitting them that way is what keeps the model's own rules under a test that
  always runs.
- The server needs the `alerts` extra (`pip install -e ".[alerts]"`), which is what carries
  `huggingface_hub`. `store` imports it lazily, so a base install imports this package fine and
  then fails on the first tool call; `open_account` checks for it at the door and names the
  install instead.
- Three limitations are reported in every answer rather than left to be discovered: only résumés with
  account sync switched on exist in the dataset at all (ADR-0124, ADR-0131) — a listing that shows
  two of four is a trap — and the Account copy is written on coarse events, so it can be behind the
  browser. And `Store` answers `None` for absent, corrupt and Hub-unreachable alike while
  `resumes_for` skips what it cannot parse — so the id listing, which lies about neither, is
  what separates "not there" from "there and unreadable" instead of the two being reported as
  one.
