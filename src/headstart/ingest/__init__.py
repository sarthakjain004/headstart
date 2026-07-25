"""The 6-hourly ingest pipeline — every stage `.github/workflows/pipeline.yml` runs (ADR-0028).

One module per stage step, in run order::

    plan_scrape     stage 1  select this run's board slice, bin-pack it into scrape shards
    scrape          stage 2  (matrix) scrape one shard's boards into a fragment
    join_shards     stage 3  union the scrape fragments into one snapshot
    filter_tech     stage 3  keep the tech subset (ADR-0017)
    update_ledgers  stage 3  blend this run's measurements into the priority/cost ledgers
    plan_embed      stage 3  diff, tokenize, bin-pack the new Docs into embed shards
    embed_jobs      stage 4  (matrix) embed one shard's Docs into a fragment
    merge_shards    stage 5  concatenate the embed fragments onto the store
    index           stage 5  sync -> prune -> compact the LanceDB table

Each is run as ``python -m headstart.ingest.<module>``. They live here rather than under
``scripts/`` because they are the product's pipeline, not one-off tooling: being importable
makes them unit-testable without ``importlib`` path-loading, and keeps the run's 12 entry
points from being scattered across five ``scripts/`` subdirs mixed in with R&D scripts.

Alongside them, the three helper modules with no consumer outside this package::

    binpack     LPT packing + shard sizing, shared by both planners
    embed_prep  Doc build / English gate / typed metadata, shared by embedder and planner
    index_plan  Pure add-evict and prune planners for the jobs table (no LanceDB import)

Genuinely shared logic stays in ``headstart`` proper — ``harvest`` (the scrape engine),
``board_cost``, ``board_priority``, ``corpus`` — because ``python -m headstart``'s curated-feed
path reaches them too, and the pipeline must not become a dependency of that.
"""

from __future__ import annotations

from pathlib import Path

# src/headstart/ingest/__init__.py -> the repo root. Every stage reads and writes the repo's
# data/ tree, so they assume a source checkout — which `pip install -e .` (what all three
# workflows do) guarantees. Defined once here because the depth is easy to get wrong: each
# stage previously carried its own `Path(__file__).resolve().parents[2]`.
REPO_ROOT = Path(__file__).resolve().parents[3]
