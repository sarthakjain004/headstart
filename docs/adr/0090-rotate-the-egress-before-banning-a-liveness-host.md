# ADR-0090: Rotate the egress before banning a liveness host

**Status:** accepted · **Date:** 2026-08-27 · **Relates to:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress this reuses, and its
per-platform daemon recipe), [ADR-0012](0012-liveness-ledger.md) (the ledger whose `unknown` rows
this is trying to stop producing), [ADR-0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md)
(why a rotation can be expected to land a genuinely different address)

## Context

`scripts/validate/check_liveness.py` answers a 429 in two ways. It **eases** the gate — halving
the request rate for everything sharing that host's limit — and, when easing has hit the 1 req/s
floor or the host sends a ban-length `Retry-After`, it **trips the breaker**: every remaining
board behind that gate short-circuits to `UNKNOWN` for the next 30 minutes, or for whatever the
`Retry-After` said.

The breaker is the right shape for a rate limit and the wrong shape for a per-IP block, and the
ledger records which one we actually meet. Two ATSes dominate the `unknown` column:

| ledger | rows | `unknown` |
| --- | --- | --- |
| workable | 22,547 | **21,428** |
| personio | 9,411 | **5,178** |

Workable's is the scar the gate's own comment names: `apply.workable.com` answers a Cloudflare
per-IP ban with a ~20h `Retry-After`, and one careless `--force` run blanked ~16k boards for a
day. No amount of slowing down clears that, because the thing being metered is the address. The
breaker's whole contribution there is to convert a recoverable situation into a lost run.

## Decision

**A 429 that would ban a host tries the refusal from a different egress address first.** The
pacing ladder is untouched — an ordinary 429 still eases, because easing is what a real rate limit
responds to and a rotation is not free. Only the bottom rung changes: before `trip`, ask
`headstart.spare_egress` for a different address, and ban only if one cannot be had.

Reusing the scrape's spare egress rather than building a second one is most of the value. It
already owns the per-platform daemon recipe (`launchctl kickstart -k` on macOS,
`systemctl restart warp-svc` on Linux, each under `sudo -n`), the SOCKS5 readiness handshake, the
rotation gate, the cooldown, and the coalescing that keeps hundreds of liveness workers meeting
one wall to a single restart. The checker contributes the policy; ADR-0063 contributes the
mechanism.

Three details are load-bearing.

**The egress group is the gate key.** A gate spans whatever shares one rate limit — an exact host,
or a listed domain like `jobs.personio.de`. Routing must span exactly the same set, so both now
come from one `_gate_key()`; a key computed separately in each place is a key that can drift, and
a drifted group would wall one spelling of a host while routing another.

**Recovery lifts the ban *and* the easing.** Either alone is close to pointless. A ban left up
means the boards still short-circuit and the rotation bought nothing. Easing left in place
throttles the new address against the old one's exhausted quota: 22k workable boards at the 1 req/s
floor is ~6h against ~1.5h at the seeded 4 req/s. Recovery returns to the gate's *seeded* spacing,
not to no pacing — the seed was measured against a healthy origin.

**A 429 from a host we were redirected to never rotates.** See below; this is the guard that keeps
the mechanism from being a liability.

**There is no allowance.** A gate that keeps being refused keeps being moved, for as long as the
refusals continue. The first draft capped it at three addresses and then banned, reasoning that a
host still refusing from a third is refusing us rather than the address. That reasoning is fine and
the arithmetic is not: the first real sweep spent all three inside ~300 boards, and the ban then
short-circuited **20,916 of the remaining 21,228** without sending a request. Rotating on is
degraded throughput; banning is total loss of everything behind the gate.

The real bound is `spare_egress`'s own rotation cooldown — at one restart per five seconds a gate
cannot spend a run bouncing the daemon, and between restarts it keeps probing at its own pace. That
this is enough is the reason the cap is gone rather than merely raised: a cap needed a credit rule
to stay usable across a long run, the credit rule had to be earned by traffic actually answered
rather than elapsed time to resist laundering, and none of that has to exist.

What survives from the counting is *identity*, for the log rather than for a bound. Recovery is
idempotent per address, keyed on how many distinct addresses the run has been given, because a herd
coalesced onto one restart arrives holding one address between them — and because a restart need
not land a new address at all. Measured on that sweep: two rotations, `1 moved, 1 repeated`.

## Why the redirect guard is not optional

Personio's 5,178 `unknown` rows look like the same problem and are not. A tenant that has left
personio need not 404: a departed subdomain that is still routed answers `/xml` with a 307 to
`personio.com`, and *that* property's wall answers 429. This is the dead-tenant tombstone of
`docs/personio/2026-08-26_the-429-is-a-dead-tenant-tombstone.md` (n = 22 of 22), and reading it as
personio's own rate limiter is precisely the false premise that got PR #312 — a rotate-on-429
change — reverted.

Measured 2026-08-27 against live boards, three sampled `unknown` rows each:

| | direct | via WARP |
| --- | --- | --- |
| `apply.workable.com/api/v1/widget/accounts/{t}` | 200 | 200 |
| `{t}.jobs.personio.de/xml` (plain curl) | 429 | **429** |

Personio is the counter-case the guard exists for: no address reaches it, so rotating there would
spend a `--force` pass restarting the tunnel across 5,178 boards and rescue none of them.
`_redirected_off_host()` separates it mechanically rather than by hostname allowlist — compare the
settled URL's host against the one asked for. Verified live the same day: `13c-venture` (departed)
reports off-host, `bayiko` and `bavaria-alm` (still on personio) do not.

## What the first real sweep showed, and what it did not

The workable row above is a precondition, not a result — it says WARP addresses are not
blanket-refused on that host, sampled while the direct route was answering 200. It does **not**
show rotation rescuing a banned run, and the first sweep to actually reach that state, the same
day, showed it does not:

- 21,228 boards attempted. **40 real 429s**, then a bot-wall challenge, then the ban. 20,916 of
  them short-circuited without a request being sent. Net: 409 boards recovered to `live`, 61 to
  `dead`.
- Rotation fired and behaved as designed — two rotations, `1 moved, 1 repeated`, three charges
  against the allowance, then an honest ban. **Every address was challenged**, direct and WARP
  alike. Re-probed afterwards, both routes answer 429.

So the mechanism is correct and bounded, and workable is not the case it rescues. Its wall is a
bot-wall *challenge*, not a per-origin rate limit: a Cloudflare-reputation judgement about the
client, and a WARP exit is a worse reputation than a residential address, not a better one. The
lever there is the challenge solver (`_cloudscraper_fetch`, which a separate defect has kept from
ever running) or the browser path, not a second IP.

Two structural things the sweep exposed, neither introduced by this change and neither fixed here:
passes 2–4 are worthless once a gate bans, because the 1800s cooldown outlives all three (they ran
in 0.2s each, short-circuiting every board); and a final pass rewrites every still-unknown row's
`checked_at`, restarting the 3-day TTL on boards it never actually probed.

## Consequences

- A per-origin block costs seconds and a fresh address instead of a run's worth of boards — where
  the limiter really is keyed on the address. Where it is a reputation challenge, the ladder now
  spends three addresses finding that out before it bans. That is the price of the mechanism and
  it is bounded; the alternative was banning without ever asking.
- Rotation restarts the WARP daemon process-wide, so it briefly severs in-flight proxied requests
  for any *other* walled gate — and, uncapped, a fully-walled gate does that roughly every five
  seconds for as long as the run lasts. `spare_egress`'s gate and cooldown bound the rate; the
  checker's multi-pass retry re-probes whatever it costs. Anything else on the machine using WARP
  will notice.
- No WARP, no change: every `spare_egress` entry point returns None or False on a machine without
  it, and the ladder bans exactly as it did before. WARP is dialled *before* a group is marked
  walled, so such a run is never recorded as having spent an egress it never had. That is also the
  default state in the tests — an early run of the suite kicked the local daemon four times before
  they stubbed it.
- The pass report deliberately does not print `spare_egress.report()`'s rescue rate. Its
  denominator counts settles whose status is in the caller's `egress_on`, which is empty here, so
  the rate reads 100% however badly rotation is going. Distinct addresses handed out, and per gate
  how many refused it in a row and whether it ended up banned, are printed instead — all three can
  come out bad.
- **Not addressed, deliberately.** Those personio `unknown` rows are dead tenants and this change
  does not reclassify them; `p_personio` still reads a 200 from `personio.com` as the board's own
  page. That is a verdict-classification fix with its own evidence base, and belongs in its own
  change.
