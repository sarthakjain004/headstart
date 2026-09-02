# ADR-0102: A 400 walls the origin too, not just a 429

**Status:** superseded by [ADR-0103](0103-workdays-400-is-an-invalid-session-cookie-clear-it.md) (the 400 is a stale session cookie a route change cannot fix; reverted) · **Date:** 2026-09-02 · **Amends:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (which statuses open the spare
egress) and [ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md) (whose
retry this explains the failure of)

## Context

Workday's detail pass loses 10.0–17.9% of every run's detail fetches, and **83–95% of that loss
is a settled HTTP 400** — 256,934 of them across the ten runs `33548262185`..`33590621111`.
ADR-0098 read the 400 as a throttle and extended `_RETRY_ON` to cover it. That retry has now been
measured over those same ten runs and **recovers nothing**: the `400-throttle` counter spent
479,165 retries against a 2S ceiling of 513,868, a ratio of **0.93**, with every individual run
between 0.92 and 0.94.

A live investigation then falsified every "the request is malformed" reading of that 400. The
full measurement is tracked at
[`docs/workday/400-is-a-throttle-not-a-bad-request.md`](../workday/400-is-a-throttle-not-a-bad-request.md)
(the probes themselves live under the gitignored `experiment/workday-400-root-cause/`):

- The exact detail URLs production builds return **200 with full job JSON** from a laptop.
- 4,000 `externalPath` values across the two worst Boards contain **no** character outside
  `[A-Za-z0-9/_.-]` — nothing to mis-encode.
- A wrong data centre answers **422** on the detail endpoint (7 of 7 instances), exactly as
  ADR-0098 measured for the listing. A 400 is not what an instance mismatch produces.
- Board size does not predict it: the five largest Boards in a run have **0%** 400s
  (`aah/External` 5,192 details, `pwc/nonpublic_postings` 4,463, `citi/2` 4,186) while
  `ghr/us-emplsv` (3,196) is at 95%.

And it reproduced the mechanism positively: at production's own width of 25 streams, a sustained
walk trips a per-tenant limit at request 431 (`ghr`) and at 656 or 1,135 (`thermofisher`, on two
walks two minutes apart — so the threshold is not a fixed quota), and refuses **72–98%** of
everything after the trip. That is the same band as the **68–97%** loss production records on the
ten boards contributing most of its 400s.
From that vantage the refusal is a 429 with a Cloudflare block page; production settles 400. Why
the status differs is **not** established, and needs a probe from inside Actions.

The asymmetry that explains the wasted retries is in our own code. `workday.py` declared:

```python
egress_fallback_on = frozenset({429})
```

`http.fetch` marks a group walled — which routes every later request for that ATS through the
spare egress — only on a status in that set. So a **429 could reach the escape hatch and a 400
could not**, and the status responsible for 83–95% of all detail loss was the one status that
could never turn the spare egress on. ADR-0098's retry then spent two extra attempts against the
same penalised IP, which is a complete explanation for a recovery ratio of 0.93.

## Decision

**Add 400 to Workday's `egress_fallback_on`**, making it `frozenset({400, 429})`. Offered the
choice between this and the `egress_rotate_on` split below, the maintainer chose this shape
deliberately, with the rotation risk stated — so the split is a follow-up, not an oversight.

400 is already in `_RETRY_ON`, so `http.fetch`'s stated invariant (`egress_on` a subset of
`retry_on`) holds on every call site that passes it.

**That set has three consumers, not one**, and an earlier draft of this ADR wrongly said nothing
else changed. Beyond `mark_walled` and `_rotate_for`, a walled group also narrows its fan-out:
`spare_egress.stream_width` returns `min(ceiling, _WALLED_STREAM_WIDTH = 12)`, which Workday reads
for both `_PAGE_STREAMS` and `detail_streams` (25 each). In practice this changes nothing, and the
reason is measured rather than assumed: **Workday already walls on a 429 in all 15 shards of every
run** (`spare_egress` "spending this shard's spare egress" appears 15 times in run `33590621111`),
so the width is already 12 today. What this ADR changes is *when* the wall lands, not whether.

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

**Leave it and accept the loss.** The status quo loses ~25,000 descriptions per run to this
status and spends ~48,000 retries per run failing to recover them (256,934 and 479,165
respectively across the ten-run window). Rejected.

## Consequences

**The risk is a rotation storm, and it is the thing to watch.** Because `egress_on` also gates
`_rotate_for`, every 400 taken while already proxied now asks for a fresh egress IP.

Sized against the direct measurement, after two earlier drafts of this ADR sized it against the
wrong quantity — first a settled-count ratio (~10x), then a retry count that is process-wide
across every ATS (2.7x). Neither is what reaches `_rotate_for`. The shard logs report the calls
themselves: `spare egress rotations: attempted A, succeeded S, throttled T, abandoned B`. Summed
over the ten runs `33548262185`..`33590621111`:

| quantity | window | per run |
|---|---|---|
| `rotate()` calls (`attempted + throttled`) | 159,449 | ~15,900 |
| rotations actually performed (`succeeded`) | 15,182 | ~1,518 |
| of those calls, cooldown-`throttled` | 144,267 | **90%** |

(An earlier count here read 157,314 / 14,774 because the pattern that gathered it required the
line's trailing `, abandoned B` clause, which `spare_egress.report` omits when that count is zero
— so seven of the 150 shard lines were silently skipped. That is precisely the silence-as-zero
failure the analyser half of this change fixes, committed by the same hand on the same day. Every
field after `attempted` is optional and must be matched as such.)

**That 90% is what keeps this from being a 4x restart storm**, though the mechanism is subtler
than "the gate is shut". Reading `spare_egress.rotate`: a `throttled` caller waits out only the
*remaining* cooldown, and then either a peer's rotation lands it on a fresh IP or it rotates
itself. It is the `abandoned` path — 12,134 calls, **7.6%** — that gives up and rides the spent
IP. So `throttled` is "queued behind the cooldown", not "refused", and `attempted + throttled`
approximates the call count rather than counting it exactly.

What follows is still that **`succeeded` cannot scale with demand the way calls do**: rotations
are serialised behind a 5s cooldown, and the ten runs already sit well inside that ceiling. So
admitting ~4x the callers should raise restarts by a small factor, not fourfold, and the larger
cost is aggregate *waiting* on the cooldown — bounded per caller but concurrent within a shard.

Read **run wall-clock** against the 51–73 min these ten runs recorded — and the scrape stage's
own critical-path share against its 22.0–36.8 min, which is the half this change can move — and
watch `succeeded`
(~1,518/run today). Revert this, or take the split below, if wall-clock rises materially or
`succeeded` climbs toward its call volume — that last would mean the cooldown stopped binding and
the demand model applies after all.

**Three call sites are now "marked but not retried".** The unpatient arm of `_resolve_instance`'s
sweep takes `http.TRANSIENT` (ADR-0098 deliberately leaves the sweep unretried), as do
`_page_detail` and `_page_detail_async`. `http.fetch` names and permits this case: such a request
settles on the wall it just reported while still sparing every later Board the same three
attempts. That is wanted at all three: a 400 on the sweep is exactly the throttle ADR-0098
identified, and `_page_detail`'s two are the ADR-0099 public-page fallback — a different host path
on the same tenant, so a 400 there is the same origin refusing us and is worth recording as such
even though that one request cannot be retried into success.

**What success looks like.** The `400-throttle` ratio should fall below 0.93 as retries start
landing on a fresh IP, and Workday's share of detail loss should fall from 83–95%. Read the ratio
against that baseline, per ADR-0098's own rule — not the spare egress's "recovered" rate, which
ADR-0063's 2026-08-26 amendment already records as blind.
