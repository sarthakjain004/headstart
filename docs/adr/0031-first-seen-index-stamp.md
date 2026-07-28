# ADR-0031: Stamp `first_seen` when a Job enters the index

- Status: Accepted
- Date: 2026-07-28
- Adds one column to the `jobs` table of [ADR-0019](0019-tech-corpus-search-index.md), written on the
  incremental add path of [ADR-0014](0014-search-index-ingestion-and-freshness.md). Constrained by the
  per-run upload budget that [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) and PR #76
  established. Does not change eviction, prune, or partial-harvest semantics.

## Context

The index could not answer "what showed up recently?".

The only date on a row was `posted_at` — the **company's** posting date, as reported by the ATS. It
is a poor proxy for when we indexed a Job, on two counts. It is absent on ~14% of served rows (measured over a
100-row sample of the live Space). And boards are scraped on a rotating schedule — ≤20k boards per
run, priority head plus an exploration tail (ADR-0022) — so a job posted on Monday may not be
scraped until Thursday. `posted_at > now - 2h` therefore answers a different question, on a subset
biased toward whichever boards happened to be scraped recently.

Meanwhile the pipeline computes the exact answer twice per run and discards it both times:
`embed_plan` diffs the corpus against the prior `meta.jsonl`, and `index sync` computes `plan.add`.
Only the counts survive, in the run logs (`plan: add 5754`, `8664`, `7210`, `9334` across recent
runs).

## Decision

**Stamp each row with the run's timestamp as it enters the table.** An ISO-8601 UTC string in a new
nullable `first_seen` column, matching how `posted_at` is already typed — string comparison sorts
correctly given a fixed format, which is what lets the existing recency filter work at all.

`sync` is the only place rows are ever added, so a row is stamped exactly once and nothing rewrites
it afterwards; evictions delete outright. One stamp per run, shared by everything that arrived in
it, because they all arrived in the same scrape. A Job evicted and later re-added is stamped
afresh — it is newly visible again, which is what a "new" badge should mean.

**Write-once is the whole design.** The rows added per run (~5-9k) are already being written, so the
column is nearly free. A column meaning *last* scraped would instead have to be updated on every row
of every board scraped — ~200k of 268k rows per run — and Lance is immutable-columnar, so an update
rewrites the data files holding those rows. That is a fresh ~800 MB blob every run, exactly the cost
PR #76 removed to stop the HF quota filling in ~45 runs. Per-board last-scraped already exists in
`data/state/board_cost.csv` if that signal is ever wanted: 15,727 rows instead of 268,440.

**The migration lives in `sync`, guarded on presence.** `_schema()` only reaches tables that call
creates, so the live table — created weeks ago — keeps its frozen schema, and `apply_sync` requires
rows to match it exactly. So `sync` widens the table before writing any row that carries the column,
and no-ops once it is there. Self-healing: CI, a local checkout, and production all converge without
anyone remembering to run a migration. `compact` needs no change — it rebuilds via `to_arrow()` →
`create_table`, carrying whatever schema the data has.

Rows predating the column stay null. That is honest: we do not know when we first saw them, and
`NULL >= '…'` is never true in SQL, so they drop out of every window on their own.

## Alternatives considered

- **Deriving it from `posted_at`.** Not derivable in principle — nothing in a row records when we
  scraped it, and the two dates differ by however long the board rotation took.
- **Persisting `plan.add` to a side file per run.** Keeps the table unchanged, but then every query
  needs a join against a growing pile of run manifests to answer one filter. The fact belongs on the
  row.
- **A `last_scraped` column** — rejected on the rewrite cost above.
- **A separate one-off migration workflow step.** More moving parts than the presence check, and it
  would have to be remembered for every fresh checkout.

## Consequences

The first run after deploy pays a one-time full data rewrite (~800 MB upload) when `add_columns`
widens the table. `cleanup-index` already mints a comparable blob every 2 days and the dataset sits
at 16.28 GB of 100 GB, so this is a blip.

There is a deploy-ordering hazard worth naming, because it is invisible until it bites: the Space
redeploys on merge, but the column only appears on the **next pipeline run**. A query filtering on a
column the table lacks errors *every* search, not just filtered ones. So the Space checks
`first_seen in _table.schema.names` at boot, ignores the parameter when absent, and hides the
control — the feature switches itself on when the data arrives, with no coordinated deploy. Any
future column added this way needs the same treatment.

The "new since" filter is in **hours**, not days like `posted_within`, because the window is meant to
be shorter than one pipeline cycle. It also needs no `LIKE '____-__-__%'` shape guard: that exists
for `posted_at` only because raw ATS strings like darwinbox's `21-Apr-2026` sort lexicographically
above any ISO cutoff and would leak into every window. We write `first_seen`, so it is always ISO.

The filter is added to `deploy/hf-space/app.py` only, not to `headstart.search.build_filter`. The two
builders have already diverged — `posted_within` was never added to the latter either — and this
follows that precedent rather than widening scope. Worth revisiting if a third filter diverges.
