# ADR-0081: The spare-egress pool is deep, not 1–3 addresses

**Status:** accepted · **Date:** 2026-08-21 · **Amends:**
[ADR-0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) (its pool-depth
measurement — "one to three addresses" per colo, a rotation returning a genuinely different IP
"~11 times in 30" — and its consequence that WARP is worse for diversity than the direct route it
replaces) · **Relates to:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md),
[ADR-0078](0078-width-narrows-once-the-origin-has-walled.md)

## Context

ADR-0067 (2026-08-19) measured, via three dedicated probe workflows on 18–30 jobs, that a WARP
rotation (`systemctl restart warp-svc`) returns a genuinely different egress IP only ~11 times out
of 30 (37%), because each colo carries "one to three" addresses. That number is load-bearing in
production, not just documentation: `spare_egress.stream_width()` clamps a walled group's fan-out
to `_WALLED_STREAM_WIDTH = 12` specifically because "a working [spare egress] supplies one to three
IPs... a shard that can rotate is not owed the wide width." Several docstrings, a domain-doc
paragraph in `CONTEXT.md`, and three test docstrings all repeat the 1–3 / ~11-of-30 numbers as
current, settled fact.

The 2026-08-21 ten-run fan-out review
(`docs/pipeline/2026-08-21_ten-run-fanout-review.md`, §6a) parsed every `spare_egress` rotation
line out of the regular nightly pipeline's own shard logs — not a dedicated probe, organic
production traffic — across 150 shard-runs (10 runs × 15 scrape shards) spanning ~13 hours the day
after ADR-0067 shipped. The result disagrees with ADR-0067's pool-depth number by two orders of
magnitude of sample size, and by direction.

## What was measured

> **Mechanism established 2026-08-27 by [ADR-0092](0092-resolve-through-warp-not-before-it.md).**
> The depth measured here is real and is a property of **IPv6**, which this ADR names in the
> line below and ADR-0067 never did. Both counts were right about their own pool. Which one a
> caller draws from is decided by whether the hostname is resolved locally (`socks5://`, A
> record, shallow IPv4) or by WARP (`socks5h://`, AAAA, deep IPv6).

- **12,702 rotation events, 11,007 distinct IPv6 addresses.** The single most-reused address
  landed 7 times out of 150 shard-runs — nowhere near "one to three."
- **Per-shard churn (unique IPs ÷ rotation events) is 0.93–1.00 in effectively every one of the
  150 shard-runs.** Most shards never repeat an address across 40–205 rotations in one run.
- **Colo-pinning still holds, exactly as ADR-0067 found — but its per-colo pool-size table does
  not.** Every shard's rotations stayed within one colo for its whole life (IAD, MSP, SJC, LAX,
  ORD, SEA, DFW, and once SCL) — nothing here contradicts the pinning behavior. The pool-size
  breakdown is a different matter: ADR-0067 measured LAX and SJC at a single address apiece,
  unable to rotate at all. Both colo codes appear repeatedly in this review's data (SJC 2,085
  rotation-landings, LAX 1,550) with the same 0.93–1.00 churn as every other colo — so the
  per-colo table, not only the aggregate "one to three," is contradicted specifically for the two
  colos ADR-0067 named.
- **Rescue rate corroborates it independently.** `rescued / (rescued + walled)` — the number
  `spare_egress.report()` already prints — was ~100% in every run sampled, both ATSes that opt in:
  run `32443588900` workday 42,818/42,818 (100%), eightfold 1,139/1,139 (100%); run `32466424417`
  workday 41,306/41,362 (99.9%). A rotation that mostly returned the same, already-walled address
  could not sustain a 100% rescue rate. The IP-diversity evidence and the rescue-rate evidence were
  gathered independently and agree.
- **Not re-measured here:** ADR-0067's direct-route comparison ("30 distinct direct IPs for 30
  jobs; the same 30 jobs shared just 11 WARP IPs" — i.e. WARP diversity worse than direct). This
  review did not capture direct-route IP counts, so that specific consequence is not confirmed or
  denied by this data — only flagged as resting on the now-corrected pool-depth premise and
  probably not holding either. Worth checking if anything still depends on it.

## Decision

**Correct the record rather than silently editing history.** ADR-0067 stays as the historical
measurement it was — three dedicated probes, 2026-08-19, 18–30 jobs — and its colo-pinning finding
is not disputed. What's corrected is the pool-depth number it reported, and everything downstream
that repeats "1–3 addresses" or "~11 times in 30" as current operative fact: on the far larger
sample available two days later from real production traffic, it isn't.

Not re-running ADR-0067's probes to adjudicate which measurement is "right" before correcting
anything: 12,702 organic-traffic events already outweigh 30 synthetic ones as a sample, and every
independent angle here (address count, per-shard churn, colo stability, rescue rate) agrees with
the others. A single anomalous day producing that much internal consistency across four unrelated
signals is not the parsimonious explanation.

## Consequences

- **`_WALLED_STREAM_WIDTH = 12` loses its stated justification.** The clamp's own comment already
  called it "extrapolated, not measured here" before this ADR; this one removes the specific
  premise ("a shard that can rotate is not owed the wide width" because the pool is tiny) it was
  extrapolated from, without supplying a replacement number. Whether a walled group can now sustain
  wider fan-out — since it draws from a deep pool and gets rescued ~100% of the time — is an open,
  separate question, worth its own live measurement (in the spirit of ADR-0047's own worked
  example) before the constant is touched. **Not decided here.**
- **"Rotation counters are not a health signal" still holds, for a different reason.** Not because
  rotation "usually" returns a repeat (it doesn't), but because a bare rotation count still can't
  say whether the fresh IP got past the origin — only the rescue rate answers that, and it now says
  the fresh IP almost always does.
- Every docstring/comment citing "1–3 addresses" or "~11 times in 30" as the current state of the
  world should point here, or drop the specific number while keeping the still-true mechanism
  (compare addresses, don't count rotations).

## Alternatives considered

- **Wait for more data before correcting anything, in case this is a one-off day.** Rejected: the
  sample (150 shard-runs) is already 5x ADR-0067's probe count, spans 13 hours and two ATSes, and
  all four independent angles agree with each other. That much internal consistency is not what an
  anomalous day looks like.
- **Re-run ADR-0067's dedicated probes today for a clean apples-to-apples comparison before
  writing anything.** Worth doing if the discrepancy needs a controlled mechanistic explanation —
  why did deliberately back-to-back `systemctl restart` calls two days ago see mostly the same
  address, when organic production traffic today does not? Flagged as a follow-up, not done here:
  the production evidence is already strong enough to correct the documentation, and doesn't need
  the mechanism explained first to be true.
