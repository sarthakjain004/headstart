# ADR-0136: The value gate keeps one dimension, because the data has one

**Status:** accepted · **Date:** 2026-09-11 · **Declines to extend ADR-0064.** The gate is
unchanged; this records why, so the question is not reopened from scratch.

## Context

`docs/pipeline/2026-09-10_five-run-log-review.md` §3 found `teamtailor:waymaneducation-1710232669`
producing **56,527 postings a run for a net tech change of −30** — about 3% of the corpus-wide
scrape total — and proposed that ADR-0064's value gate "has a time dimension and no volume
dimension".

The diagnosis is right. The gate opens at `_GATE_FLOOR_S = 900.0` because a shard's wall clock is
set by its slowest single Board, so it asks *"is this Board too slow?"*. This Board is **fast** —
`board_cost.csv` measures 562 s for 56,527 postings — so it sits under the floor and its yield is
never consulted, even though at the 12 tech jobs `board_priority.csv` credits it that yield is
**1.28 tech/min, below the gate's own 2.0 threshold**. It fails the value test and escapes on the
floor alone.

The proposed fix was a second dimension: gate on rows produced as well as minutes spent.

## Decision

**No second dimension. Park the one Board.**

ADR-0064's floor was not tuned, and its own comment says so — *"which is why this is a threshold
and not a tuned parameter"*. It was read off a gap: of the 12 Boards over 15 min, tech-yield-per-
minute ran 124, 24, 20, 7.1, 5.7 and then fell to 1.3, 0.9, 0.5, 0.3, 0.2. Two populations, one
obvious line between them.

A volume threshold was held to the same test, over the 421 slow Boards of runs
`34450830376..34470668397` — 787,027 raw rows, joining `scrape_run`'s `slow board` lines to
`board_priority.csv`'s `last_tech_jobs` (Workday's log keys are full URLs and were normalised to
the ledger's `tenant/site` form; without that, 60% of the sample drops out and the answer changes).

| Axis | Largest multiplicative gap | Shape |
|---|---|---|
| Rows per tech job | ~2.1-2.6x, mid-distribution | 8676, 4711, 4468, 2123, 2042, 1642, 1062, 962, 842… a smooth decay |
| Raw rows | **5.5x, after rank 1** | no other gap in the distribution exceeds 1.1x |

**Neither axis supports a threshold.** On yield there is no gap to sit in, so any number chosen
would be exactly the tuned parameter ADR-0064 refused. On raw volume the split is clean and has
**one member above it** — every other Board is ≤10,246, and that 10,000 cluster is Oracle's
offset cap, an artifact rather than volume. A gate matching one Board is a park with extra
machinery, and it would carry a threshold that quietly gates the next Board to cross it without
anyone reading its postings.

So: `PARKED_BOARDS` gains one entry, and the gate is left alone.

## Consequences

- The gap this review identified is **real and still open**. A Board that is fast, enormous and
  barren is not caught by anything today, and will not be until a second one appears — at which
  point there is a population to find a line in, and this decision should be revisited.
- Wayman is **not** the worst Board by rows-per-tech-job — `icims:securitycareers-aus.icims.com`
  is, at 8,676 rows per tech job against Wayman's 4,711. It is parked because of its **absolute
  volume**, which is where the gap is; the worse-yielding Boards are small enough that gating them
  would save little.
- The decision rests on a sample biased toward slow Boards: `scrape_run` logs `slow board` only
  at `_SLOW_BOARD_S = 120.0`, so a Board producing tens of thousands of rows in under two minutes
  is invisible to it. At ~98 rows/s that means anything under ~11,760 rows. The bias runs *against*
  finding a volume population, so it weakens rather than manufactures this conclusion — but a
  future revisit should widen the sample rather than reuse this one.
- Costs nothing to reverse. The gate is untouched; adding a dimension later starts from the same
  place, with better data.
