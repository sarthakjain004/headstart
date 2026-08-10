# ADR-0040: Role-trend ledger — frozen embedding centroids × experience bands

**Status:** accepted · **Date:** 2026-08-10

## Context

The product should show which tech role categories are growing or declining: per-group counts
("Senior Software Engineer — 3,000 jobs") re-measured every 2-hour pipeline run and charted
over time. That needs three things a search index alone doesn't have: a **stable grouping** of
jobs into role categories (trend lines are meaningless if group identity shifts between runs),
a **per-run count series** stored somewhere durable, and a **surface** in the Space UI.

## Decision

**Groups are (role family × seniority band).** The two axes come from different machinery on
purpose:

- **Role family = nearest frozen embedding centroid.** A one-off MiniBatch-k-means over the
  store's L2-normalized vectors (`scripts/embed/cluster_roles.py`, run by the manual
  `cluster-roles` workflow — the ~850 MB store stays off laptops) produces
  `data/state/role_centroids/`: `centroids.f32` (K × dim, unit-normalized, so assignment is
  one matmul + argmax) and a manifest with per-cluster labels and top titles. K comes from a
  sampled-silhouette sweep unless fixed. Labels start as deterministic token names and get
  polished (by hand or the llm-router) before the ledger ships. The contract the fit and the
  pipeline must agree on — centroid-store load/save, assignment, banding — lives once in
  `headstart/roles.py` (the `doc_prep` pattern); the script keeps only the one-off fit.
- **Seniority band = the experience columns the table already carries.** `min_years` /
  `experience_source` are written by the ADR-0009/0018 cascade at embed time; the trends step
  only bands them (unspecified / intern / entry 0–1 / mid 2–4 / senior 5–7 / staff 8+, intern
  overridden from title or `employment_type`). No new extraction; the axis sharpens whenever
  `experience.py` does. Seniority from clustering was rejected — it lives in a few title
  tokens that embeddings blur, and a pure cross-product cluster would split it mushily.

**The centroids are frozen and versioned.** Assignment against fixed centroids keeps every
trend line's identity stable run over run. Refitting re-bases the entire series, so it is an
explicit decision — dispatch the workflow, bump `version`, and the ledger carries
`centroid_version` per row so a chart never silently splices two bases.

**Counts measure the live index stock, not the run's scrape.** The per-run snapshot is
partial by design (shard budgets, board rotation), so its counts would swing for pipeline
reasons. The merge stage — after `index sync` and `index prune` — reads `id, vector,
min_years, title, employment_type` from the served table, assigns, bands, and appends
`(ts, centroid_version, family, band, count)` rows to `data/state/role_trends.csv`, which
rides the existing state upload (~K×6 rows per run, tiny forever).

**Surface: the Space.** A `/trends` endpoint serves the ledger; the UI charts families with
roll-down to bands. The Space already restarts after every run, so the chart is at most one
run stale.

## Options rejected

- **Deterministic title rules for the family axis** (the repo's usual idiom, and the original
  recommendation): explainable and stable, but chosen against — centroids catch title variety
  ("SDE II", "Software Developer") without a hand-grown rule set. The cost accepted: cluster
  labels need a naming pass, and family boundaries are as good as the embedding space.
- **Re-clustering every run**: non-comparable groups run to run — no trend lines.
- **Per-run snapshot counts**: measures the pipeline, not the market (above).
- **An LLM classifier in the 2-hour path**: the llm-router is deliberately unreachable from
  CI (`docs/LLM_API.md`); routing every run's titles through it would put private
  infrastructure on the critical path. LLM use stays one-off (cluster naming).

## Consequences

- Ships in three parts: centroids + this ADR; the `role_trends` merge step + workflow wiring;
  the Space endpoint + chart. The ledger only starts accruing when part two lands.
- A refit is a re-base: charts must segment by `centroid_version`, and old versions' rows
  stay in the ledger untouched.
- New vectors are assigned to the nearest existing family even if a genuinely new role
  category emerges; drift shows up as a family's top titles diverging from its label. The
  manifest's `count_at_fit` vs live counts is the tripwire to consider a versioned refit.
