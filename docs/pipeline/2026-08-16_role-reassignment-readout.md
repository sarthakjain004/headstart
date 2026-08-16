# ADR-0057 readout: the software-engineering "decline" is largely reassignment

**Date:** 2026-08-16 · **Question** (the handoff's live thread): is the apparent
software-engineering stock decline real market churn, or rows changing family without the job
changing? ADR-0057's transition ledger answers this per-tick going forward; this is the
retrospective answer for the runs that already happened, via LanceDB time-travel
(`diff-role-assignments` workflow, versions 2 → 57 ≈ 2026-08-15 06:00 → 2026-08-16 06:00 UTC,
centroid version 2, k=72).

## Result

Of ~226,000 rows present in both versions, **1,028 changed family (0.46%) in one day** — same
id, same open posting, different family. The net flow is one-directional out of
software-engineering: **−957 rows/day net**, received by hardware-embedded (+327 gross from SWE),
systems-engineering (+166), ai-ml (+102), security-engineering (+60), architecture (+55),
data-engineering (+32), and a long tail — every one of the top 15 gross transitions has
software-engineering as its source. tech-leadership is the only other net loser (−40).

That magnitude matches the observed stock decline (the investigation that triggered this was a
~622-row SWE drop read as market churn). The reassignment theory is **confirmed, measured, and
material**: a trend read on family stock alone cannot distinguish this drain from closures.

## Mechanism (consistent with, not proven by, this diff)

The plausible driver is ADR-0050 description upgrades: a job first embedded from title-only
metadata lands in the software-engineering catch-all; when its description arrives it is
re-embedded, the vector moves toward the job's true content, and the frozen-centroid assignment
corrects into a specific family. The pipeline upgrades ~1,300 docs/run, of which ~1,000/day flip
family — the right scale. The #142 flap does not contribute here: a flapped id keeps its vector,
so its re-add lands in the same family (and an id absent from either version is excluded from
the shared-id diff entirely).

## Direct confirmation from the first real per-tick entry

Run 31934961378 (2026-08-16 08:57 UTC, the second post-b44e918 run — the first with a prior
snapshot to diff against) logged its own transition tick directly:
`163 of 227,544 rows changed family (0.07%), 22 transition rows | top: software-engineering ->
hardware-embedded 83, -> ai-ml 26, -> architecture 16` — the same directional signature as the
one-day retrospective diff above, from the mechanism ADR-0057 was built to measure, not from
LanceDB time-travel. `role_assignments.parquet` (2.19 MB) and `role_reassignments.csv` (1,522
bytes of real transitions, not the test-pollution junk #146 fixed) are both confirmed live on
the HF dataset.

## Consequences

- **Any per-family trend conclusion must subtract reassignment flow** — `role_trends`' stock
  series alone overstates SWE decline by ~957/day under current upgrade volume. The per-tick
  `data/state/role_reassignments.csv` now accumulates that correction going forward.
- The drain should decay as the backlog of description-less SWE rows shrinks — worth re-running
  the workflow diff in a week; if the rate hasn't fallen, the mechanism story above needs
  revisiting (it is inferred from scale, not traced per-row).
- Test-isolation note: local `data/state/role_{assignments.parquet,reassignments.csv}` were
  being written by the test suite until #146 — treat any local copy predating it as junk; the HF
  dataset is authoritative.

Reproduce: `gh workflow run diff-role-assignments.yml -f versions="OLD NEW"` (bare dispatch
lists versions). Output lands in the run's step summary.
