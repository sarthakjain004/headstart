# ADR-0057: Record each row's family assignment, and report the rows that moved

**Status:** accepted · **Date:** 2026-08-16 · **Amends:**
[ADR-0040](0040-role-trend-ledger.md) / [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md)
— the ledger and its metrics stand; what changes is that a *reassignment* stops being
indistinguishable from a closure

## Context

`role_trends` re-derives every served row's family from its vector on every tick. Frozen centroids
make that stable **for a given vector** — but not for a given *Job*. ADR-0050's description
backfill re-embeds rows; the new vector can fall nearer a different centroid; the Job changes
family while keeping its `first_seen` (which ADR-0050 preserves on purpose). In a stock series that
is indistinguishable from the old row closing and an unrelated one opening elsewhere.

That is not hypothetical. Investigating a reported decline over 2026-08-11..16:

- `software-engineering` fell 68,199 → 67,294 while **every other family rose**.
- Title-matched watch roles — centroid-independent by construction (ADR-0051) — were **flat over
  the same window (+70, +0.2%)**, and grew +7.1% over the longer window while centroid-SWE fell.
  Title-matched SWE postings did not decline; centroid-assigned ones did.
- At tick 2026-08-15T20:48, `hardware-embedded` gained **817 rows while the whole index gained
  566**. A family cannot outgrow the index unless rows arrived *from another family*.
- Net of a known darwinbox injection, total tech was **+9 — flat**: SWE's −622 and peers' +672
  nearly cancel, the signature of redistribution across ~20 families.

Two hypotheses were tested and **refuted**, which is why this ADR is narrow: eightfold's
`data.count` is not depressed for our client (identical totals via `curl_cffi` and a real browser:
2,605 / 3,685 / 3,408 / 1,710 / 1,380), so the ADR-0053 truncation guard is judging against a
correct total; and the loss is not migration into `ai-ml` specifically (SWE+ai-ml combined is
−504, and ai-ml's gain is exactly its peer-baseline expectation).

The measurement itself is sound: non-watch stock reconciles **exactly** to the index row count at
every tick checked (diff 0, six ticks). The defect is not in counting. It is that counting alone
cannot express the difference between "this Job closed" and "this Job is now filed elsewhere".

## Decision

**Record `id → family` each tick, diff it against the previous tick, and write the transitions to
their own ledger.** New module `headstart.ingest.role_assignments`, driven from `role_trends`
(which already assigns every row — it only lacked the `id` column and the memory):

- `data/state/role_assignments.parquet` — the current tick's snapshot, overwritten each run,
  stamped with the centroid version and written via a temp-then-replace so a killed run leaves the
  previous snapshot rather than a half-written one.
- `data/state/role_reassignments.csv` — append-only `ts,version,family_from,family_to,count`.

Only ids present on **both** sides count as transitions; ids on one side are genuine adds or
evictions and counting them here would re-introduce the confusion this exists to remove. A
different centroid version yields `None` rather than a diff, so a re-base is never reported as
every Job in the corpus moving at once. Watch roles are excluded — they are title matches layered
over the taxonomy, so movement between them is a title edit, not a reassignment.

An **unstamped** snapshot is rejected exactly like a mismatched one: the guard exists for files
whose provenance cannot be vouched for, and one with no stamp at all is the least vouchable.

The snapshot is written **before** the ledger, deliberately. The ledger is append-only, so
appending first and then failing to save would leave the next tick diffing against a stale
snapshot and re-appending the same transitions — inflating the series with duplicates that are
indistinguishable from real repeated moves. This order can instead lose one tick's transitions,
which under-reports once and stays truthful.

The whole diff is wrapped: a diagnostic must never sink a run that already scraped and embedded.

## Alternatives considered

- **A `role_family` column on the served table.** Family would travel with the row and could not
  drift out of sync, and the API could filter on it. Rejected for now: it coerces a diagnostic
  about the *taxonomy* into the served contract, forcing a README §"The served table" update with
  worked examples, a Space redeploy, and reliance on `tests/test_readme_schema.py` — which
  `importorskip`s pyarrow and therefore **skips in CI**, so a green run would not prove the docs
  were updated. Worth revisiting if the family becomes a user-facing filter.
- **Emit transition counts only, store nothing.** Smallest change, but cannot attribute *which*
  Jobs moved and does not survive a lost artifact — and attribution is the point.
- **Do nothing and read the trend as a market signal.** This was the working conclusion for part of
  the investigation and it was wrong; the watch-role comparison and the 817-vs-566 arithmetic both
  contradict it.

## Consequences

- A family's fall can now be decomposed into closures versus reassignments, so ADR-0040/0051's
  series become interpretable over time rather than only within a tick.
- Two new files ride the HF state round trip. The snapshot is ~290k rows of `(id, family)`,
  zstd-parquet — small next to the embedding store, and overwritten rather than appended. No
  allow-list needs changing: the upload pushes `data/state` whole and `state_fetch` takes
  `data/state/*`. The cost of that generality is that `scrape-plan` also pulls the snapshot it has
  no use for — the same reason `data/descriptions` is deliberately kept out of that glob. If the
  snapshot ever grows enough to matter, narrowing the scrape-plan fetch is the lever.
- The first run after this lands reports no transitions by design (nothing comparable yet), so the
  series begins on the second run.
- It does **not** fix the separate finding that 54% of evicted ids are re-added within hours
  (issue #142). That flapping corrupts delta signals independently and is tracked there.
