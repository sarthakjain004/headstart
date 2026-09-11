# The Résumé MCP server — how to run it, and what it can and cannot see

A local MCP server that lets an agent read one Account's **Résumé document**s: list them, fetch
one whole, and inspect what is actually set in it block by block. It runs on your own machine as
a subprocess of your agent client, under your own credentials — there is no new internet-facing
route, no shared token, and nothing to deploy. The decision and its alternatives are
[ADR-0137](../adr/0137-an-agent-reads-a-resume-by-running-the-resume-tabs-own-javascript.md);
this file is the how-to.

It is **read-only**. It cannot create, edit or delete a résumé, and that is deliberate — the
browser is the working copy and owns the conflict protocol (ADR-0124).

## Install it

```bash
claude mcp add headstart-resume \
  --env HEADSTART_ACCOUNT_EMAIL=you@example.com \
  --env SUBSCRIBERS_REPO=imPoseidon/headstart-subscribers \
  --env SUBSCRIBERS_TOKEN=hf_… \
  -- python -m headstart.resume_mcp
```

Run it from a checkout where `headstart` is importable — `pip install -e .` in the repo, or
prefix the command with the interpreter of a virtualenv that has it. Add `--scope user` to make
it available in every project rather than this one.

Then `/mcp` in the client lists it, and `claude mcp get headstart-resume` shows what it was
given.

### What each variable is

| Variable | What it is |
| --- | --- |
| `HEADSTART_ACCOUNT_EMAIL` | **The address you sign in to HeadStart with.** The server hashes it through `subscription_id` — the same derivation the Space and the alerts run use — and reads that one account's directory. There is no default: an unset value is a refusal to read, never a guess. |
| `SUBSCRIBERS_REPO` | The private Subscriptions dataset, `imPoseidon/headstart-subscribers`. |
| `SUBSCRIBERS_TOKEN` | A Hugging Face token with **read** access to it. A read-only token is the right one — nothing here writes. |

The last two are the same names `alerts.run`, `alerts.bot` and the Space already read, so a
machine set up for any of those is set up for this. `docs/agents/deployment.md` covers where the
token comes from.

### When the credentials are absent

The server still starts, still lists its three tools, and prints the reason to stderr. Calling a
tool then answers with a sentence naming exactly the variables that are unset — not a traceback,
and not a client that reports "failed to connect" while telling you nothing. That is tested
(`test_without_credentials_the_server_still_lists_its_tools_and_explains_itself`).

### Node

`inspect_resume` shells out to `node`, because the reading runs the Résumé tab's own JavaScript
rather than a Python copy of it (ADR-0137). `list_resumes` and `get_resume` do not. If `node` is
not on `PATH`, `inspect_resume` says so and points at `get_resume`; it never falls back to a
second-best reading that could disagree with the tab.

## The tools

**`list_resumes`** — no arguments. Every synced Résumé document, newest edit first: id, name,
layout, when it was last edited, the account revision, and the tailored versions it carries.
Start here; the other two tools want an id from this list.

**`get_resume(document_id)`** — the stored document verbatim: the node tree, the content map,
every variant and tailoring, the layout id and the theme. The exact bytes the browser exports.
This is the raw structure, not a reading of it.

**`inspect_resume(document_id, version?)`** — what is actually set, block by block. Every block in
document order with its Component Type, the fields that type declares, their current values,
whether it prints, and which tailored versions reword it. `version` takes a Tailoring's name or
id (case-insensitive) or `master`, the default.

Reading it as a version merges that version's wording over the master and drops the blocks it
leaves out — by running `resolve()` itself, so what you see is what would print. A field the
version changed shows both values. Three different reasons a block does not print are told apart,
because they are fixed with three different switches:

- `OFF for the whole résumé` — the document's own hidden list (ADR-0128).
- `OFF for this version` — that Tailoring's hidden list.
- `not printed — it sits inside a block that is off` — the block itself is fine; its section is not.

## Two things it cannot tell you

**Only synced résumés exist here.** Account sync is per-résumé and off by default (ADR-0124,
ADR-0131). A résumé that has never had it switched on is in the person's browser and nowhere
else — this server cannot see it and cannot count it. `list_resumes` says so every time,
including when the answer is empty, because a listing that silently shows two of four is worse
than no listing.

**The account copy can be behind the browser.** It is written on three coarse events — an explicit
save, the tab going away, and at most one push every few minutes while editing — not on every
keystroke. Every tool's answer carries that sentence.

## One account, and only one

The server reads the account its own environment names. No tool takes an account, an address, a
repo or a path; every input schema is closed and the arguments are re-checked server-side, since a
client is free to ignore a schema. Under that, `resume_mcp/account.py`'s `Account` derives the id
at construction and offers no method that accepts one — so there is no parameter through which
another account could be reached, and two tests fail if that changes. See ADR-0137 §"What it may
not do".

To read a different account, change `HEADSTART_ACCOUNT_EMAIL` and restart the server. There is no
in-session way to do it, on purpose.
