# Eightfold PCSX: no client-side lever fixes the replica instability

**Date:** 2026-08-23 · **Follow-up to:** `pcsx-replica-instability.md` (#142, PR #144),
`sitemap-primary-evaluation.md` (#150 proposal, not yet built) · **Related:**
[#145](https://github.com/sarthakjain004/headstart/issues/145) (day-bucket targeted re-sweeps,
parked), the ADR-0053 scope-exclusion ratchet (separate finding, this session)

This document closes out a question `pcsx-replica-instability.md` and `sitemap-primary-evaluation.md`
left open: is there a request-level parameter, header, or connection strategy that pins a crawl to
one backend replica, removing the instability at its source instead of compensating for it? Tested
directly against `careers.qualcomm.com`. Short answer: no — and the reason why is now a
measurement, not an inference.

## What was tried

Reverse-engineered the real frontend rather than guessing parameter names: downloaded all 47
eagerly-loaded JS chunks from the live `/careers` page, located the webpack chunk-id/name/hash map
inside one of them, and used it to resolve and fetch the lazily-loaded `careers-page` route bundle
(108 KB) directly by URL. The actual `/api/pcsx/search` call builder lives in a shared store module
this pass didn't localize before returns diminished; abandoned in favour of direct API testing,
which turned out to be far more decisive.

Tested every Elasticsearch/OpenSearch-shaped parameter this class of backend would plausibly honour:
`preference` (both a fixed string and the literal `_local`, which is ES's actual mechanism for
pinning a query to one shard copy), `routing`, `consistent_read`, `sort_by=id` (confirmed ignored,
consistent with `sitemap-primary-evaluation.md`'s own "doors probed and closed" list), and two
different `seed` values. None changed the instability signature — same two orderings, same
unstable-id counts, regardless of which of these was sent.

## The finding: pod identity confirms the architecture directly

`pcsx-replica-instability.md` inferred a load-balancer-plus-replicas architecture from indirect
evidence (no session affinity observed, day-tied ordering, stable counts vs unstable order) and
labelled it explicitly as **inferred**, not measured. The response headers hand over the serving
pod's identity for free: `X-EF-IID: prod3-www7-65ff96fbd5-{pod}`. Correlating it against the
returned ordering turns that inference into a direct observation.

40 identical requests to the same offset (`careers.qualcomm.com`, offset 0) hit **30 distinct
application pods** — a large, stateless fleet with no session or pod affinity. Nine pods were hit
twice in that short burst; **four of the nine returned both observed orderings on their own
repeat hits** — the same pod, e.g. `prod3-www7-65ff96fbd5-qm52n`, answered from ordering #1 once
and ordering #2 the next time. Across all 40 responses there were exactly **two** distinct
orderings, not three-plus as the earlier doc's illustrative diagram (`replica A / B / C`) suggested
— though that diagram was always generic, not a literal count claim.

```text
30 distinct app pods observed, all stateless, none sticky
  ↓ (each pod queries indifferently)
exactly 2 distinct orderings, and the SAME pod flips between both
```

This is the decisive fact: **the instability is not at the application-pod layer.** It sits one
layer further down, in whichever two backends the pods themselves query. No app-tier signal —
cookie, header, connection reuse, query parameter — can select a replica, because the pods
answering the request don't get to choose one either. A keep-alive `requests.Session` (tested at
n=15 across 3 offsets) showed no more stability than a fresh connection per request, which is
consistent with this: session reuse pins your connection to one pod, and pinning the pod doesn't
pin the replica.

## Conclusion

There is no available client-side fix that removes the tie-break at source. This closes the branch
of investigation `pcsx-replica-instability.md` left implicitly open ("we probed for a fix on the
server side: no sort parameter makes the order deterministic") — the negative result now extends
past sort parameters to session/routing/preference parameters as well, and is grounded in a direct
measurement of *why* (a stateless pod fleet with no control over which backend it queries), not
just an exhausted parameter list.

What remains standing, unchanged by this result:

- **`sitemap-primary-evaluation.md`'s trust-gate architecture** is still the right fix if built —
  it doesn't compensate for the instability, it changes surface entirely (sitemap batch-generation
  "can't be touched by replica ordering," per that doc). This investigation didn't test anything
  that bears on that decision either way.
- **Raising `_MAX_SWEEPS`** is a plausible cheap lever on the current API path — with a genuine
  2-replica lottery, each extra sweep should improve coverage geometrically. Not measured here;
  needs a clean, unthrottled multi-sweep crawl this investigation's rate-limited environment
  couldn't produce reliably.
- **The downstream consequence is separate and, this session found, live in production today:**
  a board marked non-authoritative by `_api_search`'s existing sweep-and-report mechanism is
  excluded from `index sync`'s eviction scope *entirely* (ADR-0053, `boards -= excluded` in
  `index.py`), with no bound and no drain — unlike the sibling ADR-0046 collapse guard, which
  ADR-0055 gave a bounded drain for exactly this failure mode. Measured against the live board
  (via the scraper's own `_description` detail call, not sitemap presence — a first attempt using
  sitemap absence as the oracle was itself invalidated when `nab.eightfold.ai`'s sitemap turned out
  to be a different, broken index): `careers.qualcomm.com` currently serves **105 confirmed-dead
  rows** (postings the board's own API no longer lists and whose detail page returns nothing),
  `careers.micron.com` 29, oldest first-seen 2026-08-01 — 22 days served with no removal path,
  across 21 consecutive production runs where the same handful of boards were excluded every time.
  This is a real, presently-accreting cost independent of whether #145 or #150 gets built, and it
  argues for a bounded/proportional exclusion (mirroring ADR-0055) as the actual priority — the
  replica instability itself may never be fully closable, but its consequence for the served table
  does not have to be permanent.

## Raw exploration notes

Session transcript-level detail (chunk map extraction steps, full pod-correlation dump) is kept
separately rather than folded in here, since none of the mechanics matter once the two findings
above are established:
`scratchpad/eightfold_api_recon.md` in this investigation's working directory.
