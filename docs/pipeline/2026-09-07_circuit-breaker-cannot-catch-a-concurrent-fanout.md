# A per-host circuit breaker cannot catch a concurrent fan-out — measured, 2026-09-07

No code change. This records why the trakstar circuit breaker is not being built, and corrects the
statistic that proposed it. It is the third recommendation from
`docs/pipeline/2026-09-07_five-run-log-review.md` to die on contact with a measurement, and the
mechanism it dies on is general enough to be worth writing down.

## What was proposed

§3a of the review ranked it fourth:

> The cost that *is* ours: 20 boards × ~95 s ≈ **1,900 board-seconds spent retrying into a host that
> was down**, because each board retries independently. A per-host circuit breaker — after N
> consecutive timeouts on `*.hire.trakstar.com`, stop trying for the rest of the run — would have
> cost ~300 s instead.

The outage itself is real and correctly described: run `34088295600`, 20 `Timeout` board errors, all
trakstar, all `curl (28) Operation timed out after 30,002 ms with 0 bytes received`, inside a window
running 05:57:11–06:02:09. Every board burned ~95 s (three 30 s attempts). Trakstar's scraped volume
fell 5,467 → 1,943. ADR-0053 excluded the failed boards from eviction scope, so nothing was wrongly
delisted, and all four boards spot-checked afterwards answer 200 in 2.2–9.6 s. The outage is
third-party and has recovered.

What does not follow is the fix.

## Why the breaker cannot fire in time

**Boards run concurrently inside a shard.** Reconstructing each failure's start from its own
`failed after {n}s` line makes this unmissable — shard 6:

| board | started | ended | took |
|---|---|---|---|
| `trakstar:exotel` | 05:58:25 | 06:00:02 | 97 s |
| `trakstar:leamseducation` | 05:58:51 | 06:00:24 | 93 s |
| `trakstar:whatfix101` | 05:59:44 | 06:01:19 | 95 s |
| `trakstar:sensedia` | 06:00:16 | 06:01:49 | 93 s |

A breaker opening after **two** completed timeouts opens at 06:00:24. By then `whatfix101` (started
05:59:44) and `sensedia` (started 06:00:16) are **already on the wire**. It prevents nothing.

That is not a quirk of one shard. Applied to all 20 failures across all 15 shards:

| breaker opens after | boards skipped | board-seconds saved |
|---|---|---|
| N = 1 completed timeout | 7 | 658 |
| N = 2 completed timeouts | **0** | **0** |
| N = 3 completed timeouts | **0** | **0** |

The proposal's own wording is "N consecutive timeouts", which is N ≥ 2 on any ordinary reading. At
N ≥ 2 the measured saving is **zero boards and zero seconds**. The 1,900 board-seconds are real;
none of them are reachable by this mechanism.

The reason generalises: **a breaker keyed on completed failures can only prevent requests that have
not yet started, and in a concurrent fan-out during a short outage, almost every request has already
started by the time the second failure is observed.** A breaker is a fine tool against a *sustained*
outage, where later boards begin long after the first ones fail. It is the wrong tool against one
that is over in five minutes — which is precisely the case the review used to justify it.

## Why N = 1 is not the rescue

N = 1 does save 7 boards and 658 board-seconds, and it is the only variant that saves anything. It
is also the variant that should not ship: one timeout would disable an entire ATS for the rest of
that shard. The review's own §3a names the hazard a sentence after the proposal — `loginext`'s feed
takes **9.6 s on a good day against a 30 s timeout**, so the margin is thin by design, and a single
slow-but-alive board would strike off every trakstar board the shard had left. Trading measured
coverage for 658 board-seconds is the wrong side of this repo's bargain.

## And the board-seconds mostly are not wall-clock

The three longest shards in the run — 3 and 12 at 27.8 min, 6 at 27.6 min — are three of the four
that hit trakstar timeouts, so the outage did land on the critical path. But board-seconds convert
to wall-clock only where concurrency has nothing else to do, and the shards disagree about how much
did:

| shard | trakstar timeouts | board-s lost | predicted | actual | over |
|---|---|---|---|---|---|
| 3 | 3 | 285 | 27.9 min | 27.8 min | **−0.1** |
| 6 | 4 | 378 | 25.9 min | 27.6 min | +1.7 |
| 12 | 3 | 285 | 22.8 min | 27.8 min | +5.0 |
| 9 (control) | 0 | 0 | 24.1 min | 25.9 min | +1.8 |

**Shard 3 absorbed 285 board-seconds of timeouts and still finished a touch under its prediction**,
while shard 9 ran 1.8 min over its own with no trakstar failures at all. Whatever sets these shards'
overage, it is not cleanly the outage, and the control shows ~1.8 min of overage is unremarkable.
The honest range for the outage's wall-clock cost is 0–3 min on the worst shard, not 1,900 s.

## What this leaves

- **No circuit breaker.** At the threshold the proposal actually specifies it is a measured no-op,
  and the only threshold that saves anything trades live boards for it.
- **The withheld-eviction path already handled the data correctly.** ADR-0053 marked the failed
  boards unauthoritative, so the 64% volume drop delisted nothing. That is the part that mattered
  and it worked.
- **The general lesson, for the next time this shape appears:** before proposing a circuit breaker,
  reconstruct request *start* times, not just failure times, and check whether the requests it would
  prevent had already begun. `failed after {n}s` makes that a subtraction. If the outage is shorter
  than the fan-out's own in-flight window, no completion-triggered breaker can help, and the honest
  answers are a shorter per-attempt timeout, cancellation of in-flight work, or accepting the cost.
- Review item 4 is withdrawn, joining items 3 and 7.
