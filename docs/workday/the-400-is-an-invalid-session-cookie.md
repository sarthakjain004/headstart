# Workday's detail 400 is an invalid session cookie, not a throttle

**Settled 2026-09-02** from inside GitHub Actions (run
[33609933482](https://github.com/sarthakjain004/headstart/actions/runs/33609933482), four
replicas) plus an on-demand reproduction from a laptop. This **supersedes the reading in
`400-is-a-throttle-not-a-bad-request.md`**, which was right that the 400 is not a malformed
request and right about the 429 beside it, and wrong about the 400 itself.

## The finding

The 400 is **Workday's own application rejecting a session cookie it no longer accepts**:

```
HTTP/2 400
content-type: application/json;charset=ISO-8859-1
server: cloudflare
cf-cache-status: DYNAMIC
                                    <- note: no cf-mitigated header

{"errorCode":"HTTP_400","errorCaseId":"5E2132MTJUOROT","httpStatus":400,
 "message":"Session cookie is invalid. Please clear your cookies and try again.","messageParams":{}}
```

The 429 recorded *in the same walk, against the same tenant, minutes apart* is a different actor
entirely — Cloudflare, with `cf-mitigated: challenge` and an HTML challenge page. Two statuses,
two mechanisms, and the whole of ADR-0098 and ADR-0102 treats them as one.

## Reproduced on demand

`experiment/workday-400-root-cause/probe_session_cookie.py`, against
`workday:ghr/us-emplsv`, one `curl_cffi` session per arm:

| arm | cookies sent | result |
|---|---|---|
| cold | none | **200** |
| warm | the ones the cold request earned | 200 |
| tampered | `PLAY_SESSION`/`CALYPSO_SESSION` corrupted | **400**, that exact message |
| rerouted | valid, but egressing through WARP instead | **200** |

Three consequences follow directly, and the first two are the important ones:

- **A missing cookie is fine.** Cold returns 200. Workday does not require a session for this
  endpoint; it only objects to one it cannot validate.
- **Changing the egress IP does not invalidate a cookie**, and does not fix an invalidated one.
  The rerouted arm carried a valid cookie over WARP and got 200.
- **Retrying is structurally incapable of recovering it.** The same session re-sends the same bad
  cookie. Measured 3/3:

      poisoned=400   retry-with-same-cookie=400   after-cookies-cleared=200

## The listing pass has it too — measured, not inferred

The first post-fix run (`33619175438`) left 188 residual 400s, every one on the **listing**
pagination (`page(s) failed mid-crawl`), which the detail-pass fix did not touch. Rather than
assume the same mechanism from the status alone, the tamper test above was repeated against the
listing `POST .../jobs` endpoint on `ghr/us-emplsv`:

| listing POST | result |
|---|---|
| warm session | 200 |
| `PLAY_SESSION` tampered | **400**, no `cf-mitigated`, body `"Session cookie is invalid. Please clear your cookies and try again."` |
| cookies cleared | 200 |

Same actor, same body, same recovery. The listing reset (`_post`/`_post_async`) is the same fix
on the other pass; its measure is the drop in the two places a persisting listing 400 still
surfaces — `_paginate`'s mid-crawl page-failure line, and a first-page raise out of `_exhaust`.

## What this explains

- **ADR-0098's retry recovers nothing** — measured at ratio 0.93 of its 2S ceiling across ten
  consecutive runs, a number that never had an explanation. It now has one: attempts two and
  three re-send the cookie that caused attempt one to fail.
- **The per-Board shape.** `BaseScraper._gather_async` opens one `AsyncSession` per detail pass,
  so a session that goes bad mid-pass 400s every remaining detail of *that Board* and no other.
  That is exactly the observed 83–95%-of-one-board pattern, and exactly why other Boards in the
  same run are untouched.
- **Why a Board can be clean forever.** Nothing poisons the cookie on a short pass. `aah` walled
  70 times with zero 400s because its passes never reached the state that invalidates one.

## What the runner vantage did *not* show

The open question that motivated the run — "production settles 400 where a laptop settles 429" —
turns out to have been the wrong frame. **The runner settles 429 too**, on the direct route, in
all four replicas at both widths:

| replica | egress | width | control (`aah`) | direct (`ghr`) | spare egress |
|---|---|---|---|---|---|
| 1 | 20.168.103.55 (LAX) | 25 | trips #426, 429 | trips #438, 429 | trips #384, **400** |
| 2 | 20.55.15.7 (IAD) | 25 | trips #420, 429 | trips #420, 429 | trips #420, 429 |
| 3 | 74.235.143.213 (IAD) | 12 | trips #428, 429 | trips #420, 429 | trips #438, 429 |
| 4 | 135.119.237.65 (IAD) | 12 | trips #437, 429 | trips #420, 429 | trips #420, 429 |

All four are distinct Microsoft/Azure addresses (AS8075), so these are four samples, not one.
Two things worth keeping:

- **The trip point is ~420 requests, everywhere.** Laptop, runner, both tenants, width 12 and
  width 25 alike. Cloudflare's rate limit is on request *count* per (tenant, source IP), not on
  concurrency — which is why narrowing the fan-out to 12 (`spare_egress.stream_width`) cannot
  help, and the measurement says so directly.
- **The control refuses too.** `aah/External` is 0%-400 in production and trips here at #426–437.
  It rules out "this tenant is configured differently" as the discriminator, and it is a reminder
  that this probe's load is not production's.

The 400s that did appear came from long-lived, high-volume sessions: replica 1's spare-egress arm
(45), and the browser arm's background pressure walk on replicas 3 and 4 (331 and 12) after
15,150 and 7,253 requests on one session. Volume and session age, not route.

## The browser arm, and an unplanned result

Each of 40 paths was fetched twice back to back — once by `curl_cffi`, once by a real Chrome on
the same address — while a background walk held the tenant at production width. In **all four
replicas, all 40 pairs came back `api 200 -> browser 200`**, while the concurrent pressure walk
on the same address was being refused (4,694 / 7,096 / 15,150 / 7,253 × 429).

So a second connection from the same IP was served normally while the first was refused ~99% of
the time. That points at Cloudflare's limit being scoped more narrowly than the source IP — per
connection, or per session — which is worth its own probe before anything is built on it. It is
recorded here as an observation, not a conclusion.

## What to do about it

The fix the evidence supports is **clear the session's cookies on a 400 and retry once**, which
turns a terminal 400 into a 200 (measured 3/3). Everything currently aimed at this status is
aimed at the wrong mechanism:

- **ADR-0098** retries the 400 through the ordinary ladder, which re-sends the bad cookie.
- **ADR-0102** (merged 2026-09-01, `1c1f8b9`) added 400 to `egress_fallback_on`, so a 400 now
  rotates the egress IP. The rerouted arm above shows a route change neither causes nor cures an
  invalid cookie, so this spends rotations on a status they cannot help — while the 429 in the
  same walk still needs them.

Both should be revisited together rather than one at a time, since they interact.
