# ADR-0169: An empty page ends an Oracle Board; `TotalJobsCount` does not

**Status:** accepted · **Date:** 2026-09-21 · **Amends:**
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (whose Consequences
recorded Oracle's `_SLACK_PER_PAGE` as already absorbing the tolerance it added; Oracle no longer
compares against a stated total at all below the offset ceiling, so it no longer calls
`mark_truncated_unless_negligible` — the mechanism keeps its 15 other call sites) ·
**Relates to:** [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the exclusion scope these
false verdicts were filling, which has no drain),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the per-Job grace period that
absorbs a one-off blank page)

## Context

`oracle.py`'s listing walk pages by offset and stops on the first empty page, whose own comment
calls that "the definitive end". It then *also* compared the row count against the envelope's
`TotalJobsCount` and, on a shortfall wider than `_SLACK_PER_PAGE` (2 rows per page walked), called
`mark_truncated_unless_negligible`. A truncated Board is Unauthoritative under ADR-0053 and leaves
the eviction scope entirely — an exclusion with **no bound and no drain**, so a Board short on
every run serves its closed postings indefinitely.

Eight Oracle Boards carried that verdict on **9 of 9** runs in the 2026-09-21 ten-run review, with
ratios as extreme as `read 27 of 600`. (Two different eights and a nine appear below, so to be
explicit: **8** Boards were excluded on every run *of that review's slice*; the probe described next
covers **16** Boards, **9** of which carried the verdict. The populations overlap but are not the
same set, and neither is a subset of a single run.)

## The measurement

For each Board: (a) the scraper's own `_listing()` walk, (b) an exhaustive sweep of **every** 200-row
offset window from 0 up to the stated total — past the empty page the walk stops on — deduped by
requisition `Id`. Live, 2026-09-21:

| pod | stated | walk | sweep | rows the walk missed |
| --- | ---: | ---: | ---: | ---: |
| `ialmme-test.fa.ocs` | 600 | 27 | 27 | **0** |
| `eofe-dev3.fa.us2` | 806 | 49 | 49 | **0** |
| `edca-test.fa.us2` | 373 | 43 | 43 | **0** |
| `edel-test.fa.us2` | 920 | 493 | 493 | **0** |
| `hcpd-test.fa.ca2` | 636 | 307 | 307 | **0** |
| `hdpc-dev6.fa.us2` | 1,477 | 407 | 407 | **0** |
| `eofe-dev11.fa.us2` | 1,623 | 605 | 605 | **0** |
| `etud.fa.us8` | 123 | 98 | 98 | **0** |
| `jpmc-dev3.fa` | 6,786 | 1,608 | 1,608 | **0** |
| 7 more (160–2,552 stated) | — | — | — | **0** |
| `eubt.fa.us6` | 78,431 | 10,000 | 10,000 | not evidence — see below |

**16 Boards, zero rows missed on every one** — every Board stating under 10,000. **Nine** of them
carried a sub-ceiling truncation verdict and every one was a **complete read**. The only true
positive in the whole set is `eubt`, the offset ceiling, caught by its own clause.

**`eubt` is reported but excluded from the claim.** The sweep can only read offsets the API serves,
so on a Board above the 10,000 ceiling it stops at exactly the wall the walk stops at and `lost = 0`
is true by construction rather than measured. The harness prints a `ceiling` column for this reason;
the 16 Boards the claim rests on all state under 10,000, where the sweep genuinely looks past where
the walk stopped.

`TotalJobsCount` is therefore not a count of servable requisitions. It over-states by 1.005x
(`hdjq`, 1,072 of 1,077) up to 22x (`ialmme-test`, 27 of 600), and the pages it over-states are
*sparse*, not missing — `ialmme-test` serves 4 rows in its first 200-row window, 5 in the next, 18
in the next, then ends.

The two captures in `experiment/oracle-total-overcount/artifacts/` are before and after this
change. They are **not quite the same population** — the *after* capture adds `jpmc-dev3`, so the
*before* one covers 16 Boards and shows **9** verdicts (8 sub-ceiling + the ceiling), while the
*after* one covers 17 and shows **1**, the ceiling. `jpmc-dev3`'s pre-fix verdict does not rest on
that capture: production logged `read 1608 of 6786 requisitions` for it on **9 consecutive runs**
(`scripts/runlog/scope_exclusion_persistence.py`), which is stronger evidence than one local probe.

**This reverses the evidence `_SLACK_PER_PAGE = 2` was sized on.** That constant's own comment, and
`docs/oracle/2026-09-08_api-measurement.md`, split examples into benign (`elfw` 728/733, `fa-eomf`
232/235, `egjl` 492/497) and "measured loss" (`etud` 89/114) by the *size* of the gap, clearing
`egjl` with exactly the boundary-shifted re-walk used above. Applied to `etud`, that same technique
clears `etud`. The two classes were never distinguishable by gap size, so no per-page constant can
separate them.

## Decision

**Below the offset ceiling, do not compare the walk's row count against `TotalJobsCount` at all.**
A walk that ended because a page came back empty has read the Board, however far short of the
stated total it lands. Log the gap at INFO so the inflation stays visible; report nothing.

`_SLACK_PER_PAGE` is deleted — it existed only for the comparison now removed.

Unchanged, deliberately:

- **The offset ceiling** (`_OFFSET_CEILING`, 10,000) still truncates unconditionally. It is the one
  genuine incompleteness here, it recurs every run rather than transiently, and no share of the
  remainder is negligible — `eubt` reads 10,000 of 78,431.
- **`_MAX_PAGES`** still truncates: reaching it means the Board did *not* end, we stopped reading.

## Alternatives considered

- **Widen the tolerance to a ratio.** Rejected: the benign shortfalls measured span 4.5%–80% of the
  stated total and there are **zero** measured real sub-ceiling losses, so any threshold is fitted
  to one class only. A constant cannot separate classes that do not differ on the axis it reads.
- **Probe one page past the empty one before accepting the end.** This would also *recover* from a
  mid-walk blank envelope rather than merely re-labelling it, and a blank envelope demonstrably
  exists — it is exactly what the offset ceiling returns. Rejected as speculative: no Board in the
  16 needs it, every sweep found the empties only at the tail, and ADR-0083's grace period already
  means a one-off blank cannot evict anything on its own (it takes two consecutive scrapes of that
  Board). Revisit if a mid-walk blank is ever measured.
- **Keep the constant unused for a future caller.** Rejected: its comment cites `etud` as a loss
  that has now been measured not to be one, so leaving it in place preserves a false premise.

## Consequences

- Eight Boards (at least — the review only sees each run's slice) re-enter ADR-0053's eviction
  scope, so their closed postings can finally be evicted. Expect a one-off eviction bump on the
  first run after this ships, concentrated on those Boards; ADR-0083's grace period spreads it over
  two scrapes of each Board rather than landing at once.
- The run's `scope exclusion keeps N eviction-candidate row(s) out of scope` figure should fall.
  Over the reviewed window it ran 3,339–3,882 rows across 35–42 Boards; the Oracle contribution
  leaves, while `successfactors:careers.hcltech.com` (~2,364 rows, ~65% of the total) is untouched
  by this and remains the dominant contributor.
- **A genuine sub-ceiling loss would now be silent.** That is the accepted risk, and it is bounded
  by the two arms kept above plus ADR-0083. The INFO line is the place to look if it is ever
  suspected: a Board whose served count *falls* while its stated total holds is the shape to watch.
- Oracle stops exercising ADR-0121's `mark_truncated_unless_negligible`. Fifteen other call sites
  keep it (eightfold 6, amazon/apple/bytedance/google/meta/phenom/successfactors/tiktok/uber 1 each),
  so that mechanism and its tests are unaffected.
