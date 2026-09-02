# ADR-0103: Workday's 400 is an invalid session cookie — clear it, don't retry or reroute

**Status:** accepted · **Date:** 2026-09-02 · **Supersedes:**
[ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md) (which read the 400
as a throttle and put it on the retry ladder) and
[ADR-0102](0102-a-400-walls-the-origin-too-not-just-a-429.md) (which let a 400 open the spare
egress). Both aimed at the wrong mechanism.

## Context

Workday's detail pass loses 10–18% of every run's fetches, and 83–95% of that loss is a settled
HTTP 400. ADR-0098 read the 400 as a per-tenant throttle and retried it; ADR-0102, when that
retry was measured to recover nothing (`400-throttle` at 0.93 of its 2S ceiling across ten runs),
added the 400 to `egress_fallback_on` so it would rotate the egress IP. Neither worked, because
neither had the mechanism right.

A probe run **from inside GitHub Actions** — the vantage production uses, which the earlier
laptop investigation could not reach — captured the 400's actual response
([run 33609933482](https://github.com/sarthakjain004/headstart/actions/runs/33609933482), four
replicas; evidence tracked at
[`docs/workday/the-400-is-an-invalid-session-cookie.md`](../workday/the-400-is-an-invalid-session-cookie.md)):

```
HTTP/2 400
content-type: application/json;charset=ISO-8859-1        <- Workday's app, not Cloudflare
                                                         <- no cf-mitigated header
{"errorCode":"HTTP_400","httpStatus":400,
 "message":"Session cookie is invalid. Please clear your cookies and try again."}
```

The 429 that appears in the same walk, against the same tenant, minutes apart, is a *different*
actor: `cf-mitigated: challenge`, a Cloudflare HTML challenge page. ADR-0098 and ADR-0102 both
treated the two as one throttle.

The mechanism was then reproduced on demand
(`experiment/workday-400-root-cause/probe_session_cookie.py`), one `curl_cffi` session per arm:

| arm | cookies sent | result |
|---|---|---|
| cold | none | **200** |
| warm | the ones the cold request earned | 200 |
| tampered | `PLAY_SESSION`/`CALYPSO_SESSION` corrupted | **400**, that exact message |
| rerouted | valid, egressing through WARP instead | 200 |
| recovery | poisoned, then cleared | poisoned=400 · retry-same-cookie=400 · **after-clear=200** (3/3) |

Three facts decide the fix:

1. **A missing cookie is fine** (cold → 200). Workday does not require a session for this
   endpoint; it only rejects one it cannot validate.
2. **Retrying re-sends the bad cookie** (retry-same-cookie → 400). That is the whole reason
   ADR-0098's retry recovered nothing — attempts two and three carried the same dead cookie.
3. **Rerouting is orthogonal** (rerouted → 200; and a route change neither causes nor cures an
   invalid cookie). That is why ADR-0102's rotation spent itself on a status it could not help.

The blast radius follows `BaseScraper._gather_async` opening one `AsyncSession` per detail pass:
a session whose jar goes stale mid-pass 400s every *remaining* detail of that one Board and no
other — exactly the observed 83–95%-of-one-board shape, and exactly why other Boards in the same
run stay clean.

## Decision

**Revert both prior fixes, and on a detail 400 clear the session's cookies and retry once.**

- **Revert ADR-0098.** `_RETRY_ON` is gone; every Workday call uses the shared `http.TRANSIENT`.
  400 leaves the retry ladder — retrying it re-sends the cookie that caused it. The `patient`
  arm of `_resolve_instance`'s probe, which existed only to retry a 400 on the hinted instance,
  goes with it; a wrong data centre answers 422, never 400, so the sweep never needed it.
- **Revert ADR-0102.** `egress_fallback_on` is back to `frozenset({429})`. A 429 is the real
  Cloudflare rate limit and still earns a reroute; a 400 no longer does.
- **Add the cookie reset, to both passes.** When a **detail** fetch settles 400, clear the pass's
  session jar and refetch once; a recovered 200 is served and counted `cookie-reset (recovered)`
  (popped from the loss tally like ADR-0099's page recovery). The **listing** pagination
  (`_post`/`_post_async`) does the same — a long crawl outlives its session's cookie and every
  page after 400s, marking the whole Board unauthoritative (ADR-0053), so it clears and refetches
  once too; a recovered page simply succeeds, so its measure is the drop in `_paginate`'s
  "page(s) failed mid-crawl (HTTP 400)" line rather than a counter. Either way a second non-200 is
  a real loss (a detail dropped, a page raised), counted as before. One retry only — no loop.

A detail-endpoint 400 is *unconditionally* the cookie one: Workday's single genuine 400 (`limit`
above 20) is a listing concern we never send on a detail URL, and detail URLs are built from
`externalPath` values the listing just handed us. A non-cookie 400 here, if one ever appeared,
would cost one wasted refetch and then count as a loss — no worse than today.

## Consequences

- The 400s that dominated detail loss become recoverable in-pass rather than retried-in-vain or
  rerouted-in-vain. **Measured on the first post-fix run** (`33619175438` vs the pre-fix control
  `33610148010`, counted the same way): detail-pass settled HTTP 400 fell **10,044 → 0**, with 227
  details recovered across 58 Boards — the drop is larger than the recovered count because the
  first cleared 400 heals the session for every later detail on it, so the cascade never forms.
  That run's 188 residual 400s were all on the **listing** pass, which is what this ADR's listing
  reset (added the same day) then covers.
- The reset clears the jar of a session shared by up to `_DETAIL_STREAMS` concurrent coroutines.
  Clearing is synchronous between awaits, so it cannot interleave with another coroutine's
  request on the same loop; concurrent 400s each clear and refetch, which is redundant but
  convergent (the first clear fixes the session, and a subsequent clear only drops a
  freshly-set good cookie, whose absence is itself a 200). Bounded: one refetch per poisoned
  detail, once.
- `400-throttle` leaves `http._RETRY_CLASS`: its only purpose was ADR-0098's keep/revert
  measurement, its name is now known to be wrong, and no caller retries a 400 any more. The
  general `retry_on` seam stays — it is not Workday-specific — but has no live user today.
- **Not addressed here:** the Cloudflare 429. It trips at ~420 requests per (tenant, source IP)
  from every vantage measured, so narrowing the fan-out cannot help it and rerouting is the only
  lever the code has. That stays as it was. And an unexplained observation from the same run —
  160/160 paired browser fetches returned 200 while the same address's API traffic was refused
  ~99% — suggests the 429 is scoped more narrowly than the IP, and is worth its own probe before
  anything is built on it.

## Alternatives considered

- **Strip cookies from every detail request.** A missing cookie is fine (cold → 200), so never
  sending one would prevent the poison outright. Rejected as the first move: it changes every
  request rather than the failing ones, and `curl_cffi`'s session auto-manages its jar, so
  suppressing it cleanly is more invasive than clearing on the 400 we can already see. Worth
  revisiting if the reset's recovered-count shows the poison recurring within a pass often
  enough to be worth pre-empting.
- **Gate the reset on the response body** (`"Session cookie is invalid"`). Rejected: it couples
  to a localizable English string, where the status-plus-endpoint invariant is already decisive.
- **Keep ADR-0102's reroute as well.** Rejected: the rerouted arm shows a route change does
  nothing for a cookie, so it would spend rotations for no gain while the 429 in the same walk
  still needs them.
