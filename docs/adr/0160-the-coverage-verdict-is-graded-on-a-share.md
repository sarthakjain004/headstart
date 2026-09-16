# ADR-0160: The coverage verdict is graded on a share, not on any failure at all

**Status:** accepted · **Date:** 2026-09-16

## Context

`scrape_join` emits one run-level coverage verdict, and `merge` reports it beside the publication
receipt. It is the pipeline's **only** run-level statement about whether a run's scrape was healthy.

It was computed as:

```python
return not self.complete or any(c["failed"] or c["partial"] for c in self.coverage.values())
```

A single failed **or partial** Board, in any one of 31 ATSes, anywhere in a 20,000-Board slice,
made the whole run DEGRADED. At this scale that condition is always true. Measured on committed evidence: DEGRADED on all five runs of 2026-09-16, at unusable shares of
**0.56–0.735%** (119/130/135/112/147 of 20,000, from those runs' own join logs), and 0.655% on the
live `data/state/scrape_health.json`. Two further runs (2026-09-13, -09-15) were seen DEGRADED
while reading logs, but their shares are not in a committed file and are not relied on here.

The cost is not cosmetic. On 2026-09-12 Workday failed **80.8%** of its Board attempts and that
provider's tech output fell ~87.7% — and the verdict printed the same word, in the same shape, as
it does on a clean run. An alarm that is always on cannot raise one.

## Decision

Grade the verdict on the **unusable share** — `(failed + partial) / attempted`, over the whole run —
with three bands:

| share | verdict |
| --- | --- |
| ≤ 2% | `healthy` |
| 2–10% | `DEGRADED` |
| > 10% | `CRITICAL` |

The line now states the share it judged and, when not healthy, names the ATSes driving it.

**Incomplete or malformed shard telemetry still degrades unconditionally.** That is a different
failure from a noisy scrape — there is no share to grade when reports are missing — so it keeps its
own unconditional branch.

Thresholds come from measurement. 2% sits ~3x above the observed 0.55–0.79% baseline, which is what
stops an ordinary run tripping it; replayed against the live `scrape_health.json` the same run now
reads `healthy — 0.66% unusable`. Replayed against the 2026-09-12 outage shape it reads
`CRITICAL — 11.18% unusable; worst: workday 80.8% of 2670`.

## Consequences

- **The band is on the overall share, not per-ATS.** Per-ATS baselines are far noisier than the
  aggregate: on the live ledger `keka` sits at 9.95% and `oracle` at 4.90% while the run as a whole
  is at 0.655%. A per-ATS threshold tight enough to catch an outage would fire on those every run —
  the same defect in a new place.
- **Naming the worst ATSes carries a minimum denominator** (`_MIN_GRADED_BOARDS = 25`). `amazon` is
  a single Board and reads 100% unusable the moment it comes back short; without a floor the
  callout would name noise instead of cause.
- A single genuinely broken Board no longer shows up in the verdict at all. That is the intended
  trade — it is visible in the per-ATS coverage line and, since 2026-09-16, in `mark_truncated`'s
  own log — and it is what buys the verdict back its meaning.
- **The verdict still cannot see a whole-Board detail loss, and that is now the biggest hole in
  it.** `zwayam` lost 100% of its detail-Jobs and `taleo_be` ~77% across the five runs of
  2026-09-16, and both contributed `failed 0, partial 0` — the providers the alarm most needed to
  catch are invisible to its numerator. Banding does not fix that; it makes it sharper, because a
  run can now read `healthy` while a provider's descriptions are entirely gone. Making a whole-Board
  detail loss count as `partial` is the necessary companion change and is deliberately **not** in
  this one: it changes what `partial` *means* to every consumer of the coverage counters, and that
  deserves its own change and its own measurement.
- **Unreadable Boards still count as successful**, so they sit in the denominator and not the
  numerator (42-53 per run). The share is therefore mildly optimistic by construction. The register
  asks for a fourth `ScrapeHealth` column for them; it belongs with the `partial` change above.
- **`CRITICAL` is a label, not a channel.** `scrape_join` logs DEGRADED and CRITICAL at the same
  level, so nothing is paged differently — the band helps a human reading the line and no more.
  Its margin is also thin: the 2026-09-12 outage computes to 10.8% against a 10% band.
- The thresholds are fixed numbers and will drift as the corpus changes. The better version compares
  each ATS against its **own trailing baseline**, which `data/state/scrape_health.json` already
  makes possible — it is written every run and fetched back at the start of the next. That is the
  next step, not this one; fixed bands fix the always-red defect now without inventing a history
  format that has not been needed yet.

## Alternatives considered

- **Keep the boolean, raise nothing.** The status quo: the catastrophic run and the clean run stay
  typographically identical.
- **Per-ATS thresholds only.** Fires constantly on keka/oracle/amazon at their ordinary rates.
- **Trailing-baseline comparison now.** The right end state, but it needs a rolling history and a
  decision about how many runs to keep; doing it here would ship unproven machinery on the one
  signal that is supposed to be trustworthy.
