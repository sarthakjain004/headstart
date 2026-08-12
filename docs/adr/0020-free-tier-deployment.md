# ADR-0020: Free-tier deployment — GitHub Actions ingest, private HF dataset state, HF Space serving

- Status: Accepted
- Date: 2026-07-04
- Builds on [ADR-0019](0019-tech-corpus-search-index.md) (the production `jobs` index and its
  incremental sync are what get deployed); ingestion semantics per [ADR-0014](0014-search-index-ingestion-and-freshness.md)

## Context

The search layer now serves the real tech corpus (ADR-0019), but only on the developer machine.
The product needs (a) an always-available search UI, (b) a recurring pipeline that scrapes new
jobs, embeds them, and refreshes the index, and (c) a total cost of ~$0 — expected load is ~10
users/day. The repo is now public, which unlocks unlimited GitHub-hosted Actions minutes, but the
public repo deliberately ships only a ~2,000-job subset of the corpus — the full index must not
land in git history.

Constraints that shape the design: the query path needs the 137M-parameter nomic encoder in memory
(rules out Vercel/Netlify-style serverless functions), the index is a directory of files that
LanceDB reads embedded (no server), and both the embedding store and the index are derived,
regenerable artifacts that change nightly.

## Decision

Three free parts, with all state in one place:

1. **State lives in a private Hugging Face dataset** (`imPoseidon/headstart-index`):
   `data/embeddings/jobs/` (the id-keyed vector cache) and `data/lancedb/` (the `jobs` table).
   Private keeps the full corpus out of public reach, honouring the public-subset policy; the
   Space reads it with a token. HF datasets are free at this scale (~150 MB today, ~4 GB at 1M
   rows) and uploads dedupe chunk-wise, so nightly pushes stay cheap.

2. **Ingest runs as a nightly GitHub Actions workflow** (`.github/workflows/pipeline.yml`), free
   on the public repo, inert until the `HF_TOKEN` secret is set (the `bot.yml` convention). Each
   run: download state → `ingest.scrape_run` (a **shuffled, `--max-boards`-capped slice** of the
   ledger's live boards, `min_jobs=0`) → `tech.py` filter → `embed_run.py --resume` (embeds only
   ids missing from the store; CPU) → `index sync` (incremental add/evict — eviction stays
   scoped to the Boards actually scraped, so a partial harvest is always safe, ADR-0014) →
   `index compact` (merge fragments + drop old Lance versions, or nightly re-uploads balloon)
   → upload state → restart the Space. The shuffle makes the capped run a *rotating* slice: at
   8,000 boards/night the whole live set refreshes on a roughly weekly cycle.

3. **Serving is a Docker HF Space** (`imPoseidon/headstart-search`, free CPU tier) defined in
   `deploy/hf-space/` and pushed from the repo. The encoder is **baked into the image** (cold
   start ≈ model load, no 550 MB re-download); the LanceDB table is **pulled at container start**
   from the dataset, so the nightly restart picks up a fresh index with no image rebuild. The app
   is a self-contained twin of `scripts/ui/serve.py` — it duplicates the few search constants
   rather than installing the package, decoupling the Space from repo internals.
   *(Superseded on this point by [ADR-0042](0042-signed-in-ui-saved-sets.md): the search path
   and constants now live once in `headstart/search.py`, synced into the Space beside the app
   the way `geo.py` is — still no package install.)*

## Rejected alternatives

- **An always-free VM (Oracle ARM) running everything** — no cold starts and one box, but the ops
  burden (signup lottery, patching, uptime) lands on a solo maintainer; revisit if the Space's
  sleep/CPU limits start to hurt.
- **Vercel/Netlify functions for serving** — the encoder doesn't fit serverless bundle/memory
  limits; Vercel remains the planned thin frontend/demo over this Space's `/search`.
- **Committing the state to git (repo or LFS)** — puts the full corpus in a public repo (policy
  violation), bloats history with nightly multi-MB churn, and LFS bandwidth quotas are the
  opposite of free.
- **A public HF dataset** — simpler auth, but publishes the full corpus wholesale; the product's
  public artifact is search results, not the dataset.
- **Full harvest every night** — unbounded runtime on a shared runner; the capped shuffled slice
  bounds the night at a few hours and still converges because eviction is board-scoped and the
  embed cache is id-keyed.
- **Refreshing the liveness ledger in the nightly job** — requires the workflow to commit to the
  repo and re-opens the shared-host throttling problem from CI's shared IPs; the ledger stays a
  locally-refreshed, committed input for now.

## Consequences

$0/month at the expected load. The Space sleeps when idle (first visitor after a quiet stretch
waits ~a minute); each board's index rows are at most ~a week stale under the rotating slice
(tune `--max-boards` to trade runtime for freshness). Vectors become a mix of MPS-fp16- and
CPU-fp32-computed embeddings — same model, both L2-normalized, negligible ranking effect.

Known gaps, accepted for v1: a board that **dies** (or drops off the live ledger) stops being
scraped, so its indexed rows linger until a ledger-driven eviction is added to `index sync`;
scheduled workflows only fire from the default branch, so the pipeline activates on merge; the
`HF_TOKEN` repo/Space secret is a coarse write token — rotate to a fine-grained token scoped to
the dataset + Space.
