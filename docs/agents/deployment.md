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
data/state/board_priority.csv         # sticky per-board tech-priority EWMA (ADR-0022)
```

The dataset is private because the public GitHub repo deliberately ships only a ~2,000-job subset —
the full corpus must never land anywhere public (including git history).

**Serving — public Docker Space `imPoseidon/headstart-search`**, live at
`https://imposeidon-headstart-search.hf.space`. Built from `deploy/hf-space/` (Dockerfile, app.py,
start.sh, requirements.txt, README.md with the Space frontmatter; `geo.py`, `llm_router.py` and
`resume_query.py` are copied in from `src/headstart/` by deploy-space.yml) — the repo is the single
source of truth; never edit the Space's files on the Hub directly. The container boots via
`start.sh`: a **best-effort** SSH tunnel to the llm-router first (serves only `/resume-to-query`;
failure costs that endpoint a 503, never the boot — ADR-0032), then the app, which
`snapshot_download`s **only `data/lancedb/*`** from the dataset (the embeddings store is
pipeline-only), loads the nomic encoder baked into the image, and serves Flask on port 7860. Free
CPU tier: the Space sleeps when idle, and the first visitor after a quiet stretch waits ~a minute.

**Ingest — two GitHub Actions workflows**, both inert (green no-op) until the repo's `HF_TOKEN`
secret is set:

`.github/workflows/pipeline.yml` (`nightly-pipeline`, **four crons/day** — 21:30 UTC compulsory
nightly + 3:30/9:30/15:30 day-runs that give the CPU embed budget more slots — plus manual dispatch).
The run is a **download → mutate → upload cycle** over the dataset, parallelized across runners as
**five stages** (ADR-0025 sharded the embed, ADR-0026 the scrape); every job/step is gated on the
`HF_TOKEN` secret so the whole run is a green no-op until it is set:

1. **`scrape-plan`** (1 job) — download the priority ledger (`data/state/*`), select this run's slice from
   the committed liveness ledger ordered by board priority (tech-history boards first + a randomly-rotated
   exploration tail, capped at `--max-boards` 20000 — 70% priority head / 30% exploration; ADR-0022), then **LPT-bin-pack the selected boards**
   into ≤15 cost-balanced shards (`ingest.scrape_plan`). Emits a per-shard board list + a matrix.
2. **`scrape`** (matrix, ≤15 shards) — each shard runs `ingest.scrape_run --assignment` (`timeout 60m`)
   over *only its boards*, streaming to a shard-scoped `data/jobs/shard-{k}/{ats}.jsonl` fragment. One
   runner per shard = one IP at the monolith's worker count, so per-host load is unchanged (ADR-0026).
   `fail-fast: false`; a timed-out shard banks its partial fragment.
3. **`join`** (1 job) — download all scrape fragments and **union them per ATS** into `data/jobs/`
   (`ingest.scrape_join`) so eviction sees the full scraped-Board set (ADR-0014); then **tech-filter**
   (`ingest.filter_tech` → `data/jobs/tech/`), **update the board-priority ledger** (`ingest.update_ledgers priority`
   — EWMA-blend each scraped board's tech count into `data/state/board_priority.csv`), and **plan the embed
   fan-out** (`ingest.embed_plan`: download the prior `meta.jsonl`, diff the new ids — this diff *is* the "only new
   jobs" step, no separate DB-diff — tokenize, LPT-bin-pack by measured per-bucket cost into ≤15 shards).
4. **`embed`** (matrix, ≤15 shards) — each shard runs `ingest.embed_run --assignment` (`timeout 180m`, CPU) over
   *only its assigned Docs* (the planner already English-gated, bucketed, and deduped them), encoding new
   vectors into a shard-scoped `embeddings.f32` + `meta.jsonl` fragment. Stateless — no prior store, no
   LanceDB. `fail-fast: false`; a timed-out shard banks its partial fragment.
5. **`merge`** (1 job — the single writer, `if: always()`) — **fetch** the *prior* store + served
   table (`ingest.state_fetch 'data/embeddings/jobs/*' 'data/lancedb/*'` — a bare `snapshot_download`
   silently returns the empty gitignored dir when the Hub errors, so the fetch asserts the state
   actually landed and aborts otherwise, ADR-0030), **concatenate** the embed fragments onto the store
   (`ingest.embed_merge`, reconciling any partial tail), then the unchanged tail: **sync** the LanceDB `jobs`
   table (`index sync`: add ids that now have a vector, evict postings gone from scraped boards —
   incremental, no rebuild), **prune** rows the board-scoped sync can't reach (`index prune --apply` —
   dead boards keyed on the live ledger + case-variant dups, ADR-0023; safety-aborts on a too-small
   keep-set), **compact** the table fresh to reclaim orphan fragments (`index compact` — keeps the
   served index small enough for the free-tier Space to cold-start), **upload** all three dirs back
   (`data/embeddings/jobs`, `data/lancedb` with `--delete`, `data/state`) with retry/backoff, and
   **restart the Space** to pick up the new table.

The two `scrape`/`embed` fan-outs run `max-parallel: 15` (leaving 5 of the free tier's 20 concurrent jobs
for `ci.yml`/`bot.yml`/`deploy-space.yml`); a workflow-level `concurrency: group: nightly-pipeline`
(`cancel-in-progress: false`) serializes whole runs so two never race on the dataset. The monolith
`ingest.scrape_run`/`embed_run --resume` paths are retained for local/single-job runs (see below).

`.github/workflows/deploy-space.yml` (`deploy-space`): pushes `deploy/hf-space/` (plus
`src/headstart/geo.py`, copied in — ADR-0024) to the Space on any main push touching those paths
(plus manual dispatch). **Never let tooling call `create_repo` on the Space** — HF now answers
Docker-Space create attempts with a `402 Payment Required` (new free Docker Spaces are PRO-only;
ours predates the policy and keeps running). The `hf upload` CLI pre-creates and so 402s; use
`HfApi().upload_folder(...)` against the existing repo, as the workflow does (2026-07-20, PR #44).

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

The résumé feature (ADR-0032; since ADR-0041 it fills the Profile tab) adds three more
**Space** secrets, all under Space → Settings → Variables and secrets: `OCI_SSH_KEY` (tunnel
private key), `LLM_ROUTER_SSH` (`user@host` of the router box — kept out of this public repo
on purpose), and `LITELLM_MASTER_KEY` (router auth). `RESUME_PASSWORD` is retired: the gate is
now the per-Account parse cap, so it needs no secret. Optional overrides: `LLM_ROUTER_MODEL`
(default `agent-default`), `LLM_ROUTER_BASE`. Until they are set the feature ships dark — the
parse endpoint answers 503 — and the rest of the Space is unaffected.

The sign-in wall (ADR-0042) adds one more **Space** secret: `SECRET_KEY`, a long random string
the session cookie is signed with (`python -c "import secrets; print(secrets.token_hex(32))"`).

**Once the wall is on, the app only works at its own URL** —
`https://imposeidon-headstart-search.hf.space` — not at the `huggingface.co/spaces/…` page,
which serves it inside a cross-site iframe. Two independent reasons, both verified against
the live embed: HF's iframe `allow=` list has no `identity-credentials-get`, so Google's
sign-in stalls part-way through; and the session cookie is `SameSite=Lax`, which browsers
never send from inside another site's frame, so even a successful sign-in would land back on
the door. The door detects the frame and offers a new tab rather than a button that hangs.
Share the direct link; treat the huggingface.co page as a listing, not the app.
The wall turns on only when both `SECRET_KEY` and `GOOGLE_CLIENT_ID` are set; until then the
page stays open and anonymous. Rotating `SECRET_KEY` signs everyone out (their cookies stop
verifying) and breaks nothing else.

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

# pull the full state locally (same call the pipeline uses; ~330 MB)
# NOT `hf download ... --include A B`: the CLI parses the second pattern as a positional
# filename and silently ignores --include (caused the 2026-07-05 state clobber)
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('imPoseidon/headstart-index', repo_type='dataset', local_dir='.',
                  allow_patterns=['data/embeddings/jobs/*', 'data/lancedb/*'])"

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
.venv/bin/python -m headstart.ingest.embed_run --resume
.venv/bin/python -m headstart.ingest.index sync
.venv/bin/python -m headstart.ingest.index prune --apply
.venv/bin/python -m headstart.ingest.index compact
# then the three hf upload commands above, then restart the Space
```

## Invariants and known failure modes

**Never upload state while a pipeline run is in flight.** A run downloads state at its start and
uploads at its end — a local upload in between gets silently overwritten by the run's (stale-based)
upload (this clobbered the 2026-07-05 surgery state). Check
`gh run list --workflow nightly-pipeline` and wait for / cancel in-flight runs before any local
`hf upload` of the state dirs; dispatch new runs only after the upload lands.

**Compact before every upload.** Lance keeps every prior version's fragments after incremental
sync; skipping `index compact` balloons the dataset and every Space cold start.

**401 on the dataset = token scope, not a missing repo.** Private-repo 401s are rendered as
`RepositoryNotFoundError`. Check which token the failing context holds before touching anything
else (local: `hf auth whoami`; Space: its secret; Actions: the repo secret).

**The Space only ever reads `data/lancedb/*`.** Uploading only the embeddings store changes nothing
user-visible until a sync produces a new table.

**Schedules fire from the default branch only** — the nightly pipeline exists on main as of
2026-07-04 and is live once the repo `HF_TOKEN` secret is set.

**Mixed vector provenance is fine.** Local embeds are MPS fp16, CI embeds are CPU fp32 — same
model, both L2-normalized; ranking effect is negligible (ADR-0020).

**A dead board's rows are pruned, not stranded.** The incremental sync only evicts within boards
scraped this run, so it can't reach a board that dropped off the live ledger — but `index prune`
(flow step 7, ADR-0023) sweeps exactly those rows every run, keyed on the live ledger. (An earlier
version of this note called it a known v1 gap; prune closes it.)
