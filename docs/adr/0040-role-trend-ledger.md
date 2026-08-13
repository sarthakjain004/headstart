# ADR-0040: Role-trend ledger — frozen embedding centroids × experience bands

**Status:** accepted · **Date:** 2026-08-10 · **Amended by:** [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md) — the ledger gains a `metric`
axis (stock/new), the chart plots share of index rather than raw counts, and named roles can
be watched by title pattern

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
the centroid `version` per row so a chart never silently splices two bases.

**Counts measure the live index stock, not the run's scrape.** The per-run snapshot is
partial by design (shard budgets, board rotation), so its counts would swing for pipeline
reasons. The merge stage — after `index sync` and `index prune` — reads `vector, min_years,
title, employment_type` from the served table, assigns, bands, and appends
`(ts, version, family, band, count)` rows to `data/state/role_trends.csv`, which rides the
existing state upload (a few dozen rows per run, tiny forever).

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

## Amendment (2026-08-11): raw clusters are not the taxonomy

The first fit (version 1, K=40 chosen by the sweep, 435,186 vectors, sampled silhouette
**0.032**) shipped and was read before any ledger was written. It showed two things the
original decision assumed away:

- **The clusters absorb the tech filter's creep, and it is not small.** Cluster 38 (1,077
  rows) was *retail* front-end ("Teammate Endzone & Loyalty (Front End)"); cluster 23 (5,595)
  was work-from-home data-entry spam; clusters 34/35/39/19 (~70,000 combined) were
  manufacturing, civil and facilities engineering (Quality, Supplier Quality, Project,
  Building, Site, Field Service, Operating Engineer). ADR-0017's filter is recall-biased by
  design, so this is expected — but charting it unchanged would put "Retail Front End" on a
  tech-trends graph.
- **The family axis partly re-derives the seniority axis.** Clusters 30 (Senior SWE), 9
  (SWE/Staff), 29 and 13 (Lead/Principal SWE) and 36 (Senior mixed) are one family split by
  level — precisely what the band axis exists to express. Charting them separately fragments
  the largest trend line five ways and double-counts seniority.

So **k-means output is raw material, not the taxonomy**. Two changes:

1. **Refit at a fixed higher K** (version 2, K=72) — deliberately *not* by silhouette sweep,
   which measures separation rather than the merge-friendliness actually wanted; on this data
   a higher-K sweep would simply return its lowest candidate. Finer clusters mix fewer roles,
   so the curation below has cleaner material.
2. **A curated cluster → family map**, `config/role_families.json`, in **git rather than the
   manifest**: it is reviewable content, unlike the generated centroids, and a PR diff is the
   right place to argue about what counts as a family. `roles.load_families` validates it hard
   — the version must match the fit, no cluster may be mapped twice, and **every** cluster must
   land in a family or in `non_tech`, because an unmapped cluster would silently vanish from
   every chart. Clusters mapped to `non_tech` are excluded from the role groups and counted
   into one unbanded `(non-tech, all)` ledger row per run: a standing ADR-0017 filter-health
   series, free.

Curating the map is therefore part of shipping a fit. A refit re-bases both the centroids and
the map (both carry the version), which is the re-base this ADR already required.

**Outcome of version 2** (K=72, 438,424 rows, sampled silhouette 0.038). The finer fit
separated cleanly where version 1 blurred: Java, Python, mobile, SRE, cloud, network, DBA and
data-centre roles each got their own cluster instead of being folded into a generic
"developer" blob. Curated into **24 families**, the largest being software-engineering (27.7%,
itself the merge of 20 clusters k-means had split by seniority and phrasing), AI/ML (6.9%) and
systems-engineering (5.3%).

**22.6% of the served index (98,949 rows) mapped to non-tech** — industrial and manufacturing
engineering (Process, Quality, Automation, semiconductor fab), civil and facilities (Project,
Site, Building, Water/Wastewater), field service and maintenance, gig/data-entry listings, and
retail. That is a direct measurement of ADR-0017's recall bias, which had never been
quantified; the `(non-tech, all)` series now tracks it every run, and it is the number to
watch if the tech filter is ever tightened.

## Consequences

- Ships in three parts: centroids + this ADR; the `role_trends` merge step + workflow wiring;
  the Space endpoint + chart. The ledger only starts accruing when part two lands.
- A refit is a re-base: charts must segment by `centroid_version`, and old versions' rows
  stay in the ledger untouched.
- New vectors are assigned to the nearest existing family even if a genuinely new role
  category emerges; drift shows up as a family's top titles diverging from its label. The
  manifest's `count_at_fit` vs live counts is the tripwire to consider a versioned refit.
