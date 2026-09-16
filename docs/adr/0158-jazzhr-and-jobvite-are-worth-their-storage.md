# ADR-0158: JazzHR and Jobvite are worth their storage, measured at full pool

**Status:** accepted · **Date:** 2026-09-16 · **Relates to:** #379 (both scrapers, disabled on
arrival), [ADR-0144](0144-oracle-taleo-was-a-dead-end-only-for-india.md) (the precedent for
reversing a recorded "do not" verdict), `src/headstart/scrapers/registry.py` (`DISABLED_ATS`),
`docs/jazzhr/2026-09-16_full-pool-measurement.md` (the numbers)

## Context

#379 shipped `scrapers/jazzhr.py` and `scrapers/jobvite.py` complete, tested and with liveness
ledgers, then put both in `registry.DISABLED_ATS` — **on cost, not correctness**. Storage is this
pipeline's documented binding constraint, and the arithmetic in the registry comment said the two
would buy ~6,740 tech Jobs for ~12.2–12.7 GB.

That arithmetic rested on a pool nobody had finished building. Both ATSes' candidate lists came
from a **partial** Wayback sweep — jazzhr 40 of 632 CDX pages (an alphabetically-early slice),
jobvite 25 of 699 — so every figure derived from them was a lower bound of unknown tightness,
including the jobs-per-board and posting counts the storage estimate multiplies out.

This is the shape CLAUDE.md already records for iCIMS, where "running the feeder rather than
trusting that dump is what doubled the provider": a verdict reached on partial discovery, left
standing as though it were a measurement.

## Decision

Run the sweep to completion first, then decide. The full sweep found **13,950 jazzhr and 4,109
jobvite** tenants against the partial pass's 782 + 178, leaving **13,567 tenants never probed**. A
liveness pass over all of them — unioned with the Common-Crawl candidates from #463, which found
96 tenants Wayback did not — settled jazzhr at 6,177 live / 4,871 hiring and jobvite at 1,079 / 749.

Re-derived from those ledgers rather than carried forward:

| ATS | Hiring Boards | postings | storage | tech Jobs |
| --- | ---: | ---: | ---: | ---: |
| jazzhr | 4,871 (was 3,684) | 99,963 | ~10.7 GB | ~5,098 |
| jobvite | 749 | **49,573** (assumed 23,461) | ~3–4 GB (was 1.5–2) | **~3,470** (was ~1,640) |

**Both ATSes leave `DISABLED_ATS`; `join` stays.** The accepted cost is ~13.7–14.7 GB for ~8,568
tech Jobs — more storage *and* more yield than #379 priced, at a similar ratio (~1.7 MB per tech
Job against #379's ~1.9 MB).

## Consequences

**The estimate moved for one ATS and not the other, and the reason matters.** jobvite is **2.1x**
the postings assumed — it had simply never been measured at full pool. jazzhr lands on its old
~10.7 GB by *coincidence*: it gained Boards (3,684 → 4,871) and lost jobs-per-Board (27.5 → 20.5),
and the two cancelled. Reading jazzhr's unchanged total as "the old estimate held up" would be
wrong on both inputs.

**This reverses a decision, so it is written down.** #379's verdict was correct on its evidence;
what changed is the evidence, not the judgement. Recording it here is the ADR-0144 lesson — that
reversal went unrecorded, "which is what let this one repeat."

**Neither ATS shows a measurable India presence**, unchanged from #379. They are accepted on global
coverage, which CLAUDE.md's Project Scope states is the target.

**The gate this uncovered is a separate, permanent fix.** Probing 18,299 boards ungated at 432
workers drew refusals across 6 distinct `applytojob.com` tenants and wrote 2,740 `dead` rows; the
same pass at 16 drew none. `applytojob.com` is now a seeded spanning gate in `check_liveness.py`,
so the next whole-pool re-probe cannot re-inflict it. `jobs.jobvite.com` is deliberately ungated —
it never refused, and a gate for it would be configuration with no measurement behind it.

**What this does not settle.** Whether the ratio is *good* is a product question this ADR does not
answer; it only makes the price honest. If storage becomes binding again, these two are the
largest single lever available — re-disabling them is a one-line change and this ADR is the
record of what that would give back.
