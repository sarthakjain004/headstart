# ADR-0102: A 400 walls the origin too, not just a 429

**Status:** accepted · **Date:** 2026-09-02 · **Amends:**
[ADR-0063](0063-a-spare-egress-for-a-spent-origin-budget.md) (which statuses open the spare
egress) and [ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md) (whose
retry this explains the failure of)

## Context

Workday's detail pass loses 10.0–17.9% of every run's detail fetches, and **85–95% of that loss
is a settled HTTP 400** — 256,934 of them across the ten runs `33548262185`..`33590621111`.
ADR-0098 read the 400 as a throttle and extended `_RETRY_ON` to cover it. That retry has now been
measured over those same ten runs and **recovers nothing**: the `400-throttle` counter spent
479,165 retries against a 2S ceiling of 513,868, a ratio of **0.93**, with every individual run
between 0.92 and 0.94.

A live investigation (`experiment/workday-400-root-cause/LOG.md`, 2026-09-02) then falsified every
"the request is malformed" reading of that 400:

- The exact detail URLs production builds return **200 with full job JSON** from a laptop.
- 4,000 `externalPath` values across the two worst Boards contain **no** character outside
  `[A-Za-z0-9/_.-]` — nothing to mis-encode.
- A wrong data centre answers **422** on the detail endpoint (7 of 7 instances), exactly as
  ADR-0098 measured for the listing. A 400 is not what an instance mismatch produces.
- Board size does not predict it: the five largest Boards in a run have **0%** 400s
  (`aah/External` 5,192 details, `pwc/nonpublic_postings` 4,463, `citi/2` 4,186) while
  `ghr/us-emplsv` (3,196) is at 95%.

And it reproduced the mechanism positively: at production's own width of 25 streams, a sustained
walk trips a per-tenant limit at ~430 requests (`ghr`) and ~1,100 (`thermofisher`), then sustains
**70–90% refusal** — which is the same magnitude as the 83–95% per-Board loss production records.
From that vantage the refusal is a 429 with a Cloudflare block page; production settles 400. Why
the status differs is **not** established, and needs a probe from inside Actions.

The asymmetry that explains the wasted retries is in our own code. `workday.py` declared:

```python
egress_fallback_on = frozenset({429})
```

`http.fetch` marks a group walled — which routes every later request for that ATS through the
spare egress — only on a status in that set. So a **429 could reach the escape hatch and a 400
could not**, and the status responsible for 85–95% of all detail loss was the one status that
could never turn the spare egress on. ADR-0098's retry then spent two extra attempts against the
same penalised IP, which is a complete explanation for a recovery ratio of 0.93.

## Decision

**Add 400 to Workday's `egress_fallback_on`**, making it `frozenset({400, 429})`.

Nothing else changes. 400 is already in `_RETRY_ON`, so `http.fetch`'s stated invariant
(`egress_on` a subset of `retry_on`) still holds on every call site that passes `_RETRY_ON`.

## Alternatives considered

**Split walling from rotation.** `http.fetch` uses this one set for two jobs: `mark_walled`
(once per process, cheap) and `_rotate_for`, which fires on *every* refusal already riding the
proxy. A new `egress_rotate_on` defaulting to `egress_on` would let Workday wall on `{400, 429}`
while rotating only on `{429}`, which removes the rotation risk in Consequences below. Rejected
for now as the more complicated first move: it adds a parameter to the shared retry seam to
forestall a cost that has been projected but not measured. If the rotation count does climb, this
is the fix to reach for rather than reverting outright.

**Revert ADR-0098's 400 retry in the same change.** Its recovery is nil and dropping it would
save ~48,000 requests per run. Rejected only because doing both at once makes the next run's
wall-clock uninterpretable — neither change could be attributed. It remains the obvious follow-up.

**Leave it and accept the loss.** The status quo serves ~250k fewer descriptions per run than it
could and spends half a million pointless requests doing it. Rejected.

## Consequences

**The risk is a rotation storm, and it is the thing to watch.** Because `egress_on` also gates
`_rotate_for`, every 400 taken while already proxied now asks for a fresh egress IP. Settled 400s
outrun settled 429s by roughly 400:1 in a run. Today's ~25,000 retried 429s produce 1,340–1,508
rotations behind a 5s cooldown — about 18:1 damping — so the same damping applied to the 400
stream projects an order of magnitude more. **That is a projection from a ratio, not a
measurement.** If the rotation count climbs about tenfold, or scrape wall-clock rises, revert this
or take the split above; a `warp-svc` restart storm would cost more than the 400s do.

**Three call sites are now "marked but not retried".** The unpatient arm of `_resolve_instance`'s
sweep takes `http.TRANSIENT` (ADR-0098 deliberately leaves the sweep unretried), as do
`_page_detail` and `_page_detail_async`. `http.fetch` names and permits this case: such a request
settles on the wall it just reported while still sparing every later Board the same three
attempts. That is wanted here — a 400 on the sweep is exactly the throttle ADR-0098 identified.

**What success looks like.** The `400-throttle` ratio should fall below 0.93 as retries start
landing on a fresh IP, and Workday's share of detail loss should fall from 85–95%. Read the ratio
against that baseline, per ADR-0098's own rule — not the spare egress's "recovered" rate, which
ADR-0063's 2026-08-26 amendment already records as blind.
