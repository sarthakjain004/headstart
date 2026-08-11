# ADR-0032: LLM access goes through the router, behind a password

- Status: Accepted; the password/IP gate is superseded by
  [ADR-0041](0041-profile-stored-extraction.md)'s per-Account cap (the router path itself stands)
- Date: 2026-07-28
- Establishes how any HeadStart feature reaches an LLM. Scopes the first such feature, the
  **Résumé query**, against the filter-then-rank search design of
  [ADR-0008](0008-local-lancedb-vector-store.md) and the
  deployment shape of [ADR-0020](0020-free-tier-deployment.md). Operational recipes live in
  `docs/LLM_API.md`; the vocabulary (**Query**, **Search filter**, **Résumé**, **Résumé query**) is
  in `CONTEXT.md`.

## Context

The first LLM-backed feature is résumé-driven search: a user pastes their **Résumé**, an LLM writes
one **Résumé query** from it, that fills the search box, the user edits it, and the ordinary search
runs. Nothing about ranking, filtering, or the index changes — the whole feature is one endpoint
that turns text into a query string.

Three facts constrain how it is built.

**The LLM is reached through the router, not a provider.** A LiteLLM deployment on the Oracle box
already holds the provider keys and runs a fallback chain — if one provider rate-limits, it switches
to another. Every LLM call in this project goes through it, so that model choice, keys and cost live
in one place and swapping providers is a router config change rather than an edit across callers.

**The router is deliberately not public, and it is not to be modified for this feature.** It binds
`127.0.0.1:4000`; only port 22 is open. A Space is an ephemeral, outbound-only container, so it
reaches the router by dialling out through an SSH tunnel (`docs/LLM_API.md` — kept untracked, since
it names private infrastructure and this repo is public; the box address itself lives only in the
`LLM_ROUTER_SSH` Space secret). The router already
holds the provider keys and runs its own fallback chain; everything this feature needs —
gating, degradation, secrets — happens on the HeadStart side.

**The Space is public and the quota is ours.** Search costs only CPU, so the one existing control —
`_MAX_K = 100` — was enough. An endpoint that spends router quota per call is a different exposure:
anyone can `curl` it in a loop.

## Decision

**One router client module, `src/headstart/llm_router.py`.** Base URL, master key, model name,
timeout and error mapping sit behind a single `ask()`. It lives in `src/headstart/` and is copied
into the Space at deploy time exactly as `geo.py` already is, because `deploy/hf-space/app.py`
performs a `snapshot_download` and loads the encoder at import and therefore cannot be imported by a
test — anything that needs tests must live outside it.

**Our client does not retry.** The router already runs a provider fallback chain; a retry loop on
top of it would re-send a request the router is *already* failing over, turning one rate-limit into
several. Absence of retry here is a decision, not an oversight.

**`resume_query.query_for(resume, ask=...)` is one function.** Input validation, the prompt, reply
cleanup, and a post-check that the returned Query carries no years or salary all sit behind it. The
`ask` seam is real by the two-adapter rule: router-backed in production, a stub in tests, which is
what lets the module be tested without a tunnel.

**The password gate lives at the route, not in the feature — validated once per IP.** It gates *any*
LLM-backed endpoint, so putting it inside the résumé module would force the next feature to duplicate
it or import résumé code to check a password. Compared with `hmac.compare_digest`, held in a Space
secret, never logged. A correct password approves the caller's IP, held in an in-memory set, so later
calls from that IP need no password; the set empties whenever the Space sleeps or restarts, and
re-entering the password once after a cold start is the accepted cost. The IP is read from the
*rightmost* `X-Forwarded-For` entry — the one appended by the platform's own proxy — because the
leftmost entries are client-supplied and trivially spoofed. Even so, an IP is shared (NAT) and
forgeable in principle: this is a gate, not authentication, and nothing behind it may assume an
identity.

**The tunnel degrades, it never kills.** The recipe in `docs/LLM_API.md` runs `set -e` and starts the
app only after the tunnel is up; taken literally, a router outage would stop the container booting
and take **search** down with it. The app starts regardless; only the LLM endpoint fails, with 503.

## Alternatives considered

- **A provider SDK straight to the vendor.** Simplest — one HTTPS call, no tunnel, nothing stateful
  in a container that sleeps. Rejected because it puts a second set of provider keys outside the
  router and forfeits the fallback chain that already exists.
- **Per-IP rate limiting instead of a password.** Bounds the accidental and the casual, but not a
  determined caller rotating addresses. A password gates strangers outright; the feature is a
  private beta, so nobody legitimate is inconvenienced.
- **A metered public allowance.** The right answer if this were a product feature for real job
  seekers. It is not yet — deciding the audience first is what made the password sufficient.
- **The router call inline in `resume_query`.** Follows "no abstraction for single-use code", but a
  second caller is stated intent rather than speculation, and it would leave the résumé module owning
  HTTP plumbing.
- **File upload with server-side parsing.** Better UX than pasting, at the cost of parser
  dependencies in a cold-start-sensitive image and a PII file at rest. Pasting tests the hypothesis
  without either, and any richer intake can be added on top without changing anything behind it.
- **Returning results directly, hiding the generated Query.** Fewer clicks, but a misread résumé
  becomes bad results with no way to see why. Showing an editable Query matches the "explicit at the
  UI" principle the search design is built on.

## Consequences

Cold start grows — tunnel setup is added to every wake of a Space that already takes about a minute
on the free tier.

**The prompt is the fragile part.** A Résumé is saturated with exactly what a **Query** must not
contain: years of experience, salary history, locations. A drifting prompt would quietly reintroduce
the LLM query-parser that `CLAUDE.md` defers — and it would do so *inside the embedding*, where no
**Search filter** shows it and no one would notice. Hence the post-check in `resume_query`: the
invariant is enforced in code, not merely requested in a prompt.

The password is a **gate, not authentication**. It identifies nobody, so it supports no per-user
limits, and once leaked it stays leaked until the Space secret is rotated. Adequate for a private
beta and nothing more; opening this feature to the public is a new decision, not a config change.

`scripts/eval/judge_pool.py` still calls `Anthropic()` directly and is now the one exception to the
router rule. It predates this ADR; it should migrate.
