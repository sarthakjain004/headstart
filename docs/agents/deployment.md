# Deployment runbook — HF dataset, Space, and the nightly pipeline

How an agent session inspects, operates, and debugs the ADR-0020 free-tier deployment. The
*decision* and its tradeoffs live in [ADR-0020](../adr/0020-free-tier-deployment.md); this file is
the operational how-to. Deployed and verified live on 2026-07-04, seeded with 42,615 vectors.

## The three parts

**State — private HF dataset `imPoseidon/headstart-index`** (~330 MB). Exact mirror of the local
paths, same layout on both sides:

```
data/embeddings/jobs/embeddings.f32   # id-keyed vector store, f32, dim 768
data/embeddings/jobs/meta.jsonl       # one row per vector: id + typed Job metadata
data/embeddings/jobs/manifest.json    # dtype/dim/count
data/lancedb/jobs.lance/…             # the production `jobs` table LanceDB reads
```

The dataset is private because the public GitHub repo deliberately ships only a ~2,000-job subset —
the full corpus must never land anywhere public (including git history).

**Serving — public Docker Space `imPoseidon/headstart-search`**, live at
`https://imposeidon-headstart-search.hf.space`. Built from `deploy/hf-space/` (Dockerfile, app.py,
requirements.txt, README.md with the Space frontmatter) — the repo is the single source of truth;
never edit the Space's files on the Hub directly. At container start the app `snapshot_download`s
**only `data/lancedb/*`** from the dataset (the embeddings store is pipeline-only), loads the nomic
encoder baked into the image, and serves Flask on port 7860. Free CPU tier: the Space sleeps when
idle, and the first visitor after a quiet stretch waits ~a minute.

**Ingest — two GitHub Actions workflows**, both inert (green no-op) until the repo's `HF_TOKEN`
secret is set:

`.github/workflows/pipeline.yml` (`nightly-pipeline`, cron 21:30 UTC + manual dispatch): download
state from the dataset → `nightly_harvest.py` (shuffled `--max-boards` slice of the live ledger,
default 8,000 — a rotating slice, whole live set covered ~weekly) → `filter/tech.py` →
`embed_jobs.py --resume` (only ids missing from the store; CPU) → `sync_index.py` (incremental
add/evict) → `compact_index.py` → upload both state dirs → `restart_space`. A `concurrency` group
serializes runs so two never race on the dataset.

`.github/workflows/deploy-space.yml` (`deploy-space`): pushes `deploy/hf-space/` to the Space on
any main push touching that dir (plus manual dispatch).

## Auth: who holds which token

Three `HF_TOKEN`s exist and they are **not interchangeable**:

1. **Local CLI login** (`hf auth login`, user `imPoseidon`) — what your session uses. Verify before
   doing anything:

   ```bash
   .venv/bin/hf auth whoami
   .venv/bin/python -c "from huggingface_hub import HfApi; print(HfApi().whoami()['auth']['accessToken']['role'])"
   ```

   `role` is `read`, `write`, or `fineGrained`. A fine-grained token must explicitly cover the
   dataset/Space repos — a mis-scoped one produces `401 RepositoryNotFoundError` ("Invalid username
   or password") on private repos, which looks like the repo doesn't exist.

2. **GitHub repo secret `HF_TOKEN`** — must be **write**-capable over both the dataset and the
   Space (the pipeline uploads state and restarts the Space).

3. **Space secret `HF_TOKEN`** — a fine-grained **read** token scoped to the dataset only (the
   Space just downloads it).

**Agents never set, read, or copy secrets** — the permission layer blocks it and that's correct.
If a secret is missing or mis-scoped, tell the user exactly where it goes: repo secret via
`gh secret set HF_TOKEN`, Space secret under Space → Settings → Variables and secrets. Adding a
Space secret auto-restarts the Space.

## Accessing the dataset from a session

```bash
# list what's in it
.venv/bin/python -c "
from huggingface_hub import HfApi
for f in HfApi().list_repo_files('imPoseidon/headstart-index', repo_type='dataset'): print(f)"

# commit history (nightly runs show up here — one commit per state dir per run)
.venv/bin/python -c "
from huggingface_hub import HfApi
for c in HfApi().list_repo_commits('imPoseidon/headstart-index', repo_type='dataset')[:10]:
    print(c.created_at, c.title)"

# pull the full state locally (same command the pipeline uses; ~330 MB)
.venv/bin/hf download imPoseidon/headstart-index --repo-type dataset --local-dir . \
    --include "data/embeddings/jobs/*" "data/lancedb/*"

# push local state up (ONLY after sync + compact — see invariants)
.venv/bin/hf upload imPoseidon/headstart-index data/embeddings/jobs data/embeddings/jobs \
    --repo-type dataset --commit-message "…"
.venv/bin/hf upload imPoseidon/headstart-index data/lancedb data/lancedb \
    --repo-type dataset --commit-message "…"
```

Uploads dedupe chunk-wise (Xet), so re-uploading a mostly-unchanged state is cheap.

## Operating the Space

```bash
# runtime stage: RUNNING | BUILDING | APP_STARTING | RUNTIME_ERROR | SLEEPING …
.venv/bin/python -c "
from huggingface_hub import HfApi
print(HfApi().get_space_runtime('imPoseidon/headstart-search').stage)"

# restart (e.g. after a manual dataset upload; needs write on the Space)
.venv/bin/python -c "
from huggingface_hub import HfApi; HfApi().restart_space('imPoseidon/headstart-search')"

# query it — /search returns JSON; k is capped at 100 server-side
curl "https://imposeidon-headstart-search.hf.space/search?q=backend+engineer&k=5"
curl "https://imposeidon-headstart-search.hf.space/search?q=devops+engineer&remote=true&max_years=3"
```

`/` serves the HTML search page. Container logs are on the Space page (huggingface.co/spaces/
imPoseidon/headstart-search → Logs) — not cleanly fetchable via `HfApi`; ask the user to paste them
if you need startup errors. A healthy boot logs `pulling index from …` → `loading encoder …` →
`ready: N jobs`.

## Operating the pipeline

```bash
gh workflow run nightly-pipeline -f max_boards=500   # manual run (small slice for testing)
gh run list --workflow nightly-pipeline --limit 5
gh run watch                                          # follow the latest run
```

The local equivalent of one pipeline cycle (see `docs/learnings.md` MPS entry before embedding on
this machine — the watermark env vars and bucketed batching are load-bearing):

```bash
.venv/bin/python scripts/embed/embed_jobs.py --resume
.venv/bin/python scripts/embed/sync_index.py
.venv/bin/python scripts/embed/compact_index.py
# then the two hf upload commands above, then restart the Space
```

## Invariants and known failure modes

**Compact before every upload.** Lance keeps every prior version's fragments after incremental
sync; skipping `compact_index.py` balloons the dataset and every Space cold start.

**401 on the dataset = token scope, not a missing repo.** Private-repo 401s are rendered as
`RepositoryNotFoundError`. Check which token the failing context holds before touching anything
else (local: `hf auth whoami`; Space: its secret; Actions: the repo secret).

**The Space only ever reads `data/lancedb/*`.** Uploading only the embeddings store changes nothing
user-visible until a sync produces a new table.

**Schedules fire from the default branch only** — the nightly pipeline exists on main as of
2026-07-04 and is live once the repo `HF_TOKEN` secret is set.

**Mixed vector provenance is fine.** Local embeds are MPS fp16, CI embeds are CPU fp32 — same
model, both L2-normalized; ranking effect is negligible (ADR-0020).

**A dead board's rows linger.** Eviction is scoped to boards present in the latest scrape, so a
board that drops off the live ledger keeps its stale rows until ledger-driven eviction is added to
`sync_index.py` (known v1 gap).
