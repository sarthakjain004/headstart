# Inspecting the HF dataset without downloading it

Read-only recipes for `imPoseidon/headstart-index`. Everything here is safe to paste — nothing
writes, deletes, or commits.

**Setup once per shell.** Every command below assumes these.

```bash
export HF_REPO=imPoseidon/headstart-index
export HF_TOKEN=$(python3 -c "from huggingface_hub import get_token; print(get_token() or '')")
[ -n "$HF_TOKEN" ] || echo "no token — run: hf auth login"
```

## What each command costs you

HF meters requests in three buckets over **fixed 5-minute windows**, and the one that runs out
first is the small one:

| bucket | what lands in it | free tier |
| --- | --- | ---: |
| **API** | `/api/…` — listing, metadata, commits | **1,000** |
| **Resolvers** | `…/resolve/…` — downloading file bytes | 5,000 |
| Pages | the website | 200 |

Measured 2026-08-28: `repo_info(expand=["siblings"])` costs **1** API call whatever the file count;
`snapshot_download` costs **3–4** regardless of how many files it pulls; and `hf_hub_download` of a
path you already know costs **0** — the bytes go to the Resolvers bucket. So *listing* is what
costs, not *fetching*. Each recipe below is labelled with what it spends.

## Watch your own budget · free

The single most useful command here. It tells you the policy and what is left in this window.

```bash
curl -sI -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/api/datasets/$HF_REPO" | grep -i ratelimit
```

```
ratelimit: "api";r=999;t=291
ratelimit-policy: "fixed window";"api";q=1000;w=300
```

`r` = requests remaining, `t` = seconds until the window resets, `q`/`w` = the policy. A **429 that
carries no `ratelimit` header did not come from this limiter** — look upstream (a CDN edge, a shared
runner IP) rather than at your own call volume.

## The whole layout, one directory per line · 1 API call

```bash
python3 - <<'PY'
import os, collections
from huggingface_hub import HfApi
info = HfApi().repo_info(os.environ["HF_REPO"], repo_type="dataset", files_metadata=True)
by = collections.defaultdict(lambda: [0, 0])
for f in info.siblings:
    d = f.rfilename.rsplit("/", 1)[0] if "/" in f.rfilename else "(root)"
    by[d][0] += 1
    by[d][1] += f.size or 0
print(f"{'files':>7} {'size':>10}  directory")
for d, (n, b) in sorted(by.items(), key=lambda kv: -kv[1][0]):
    print(f"{n:>7} {b/1e6:>9.1f}M  {d}")
print(f"{sum(n for n, _ in by.values()):>7} {sum(b for _, b in by.values())/1e6:>9.1f}M  TOTAL")
PY
```

## Why `data/lancedb/` is thousands of files · 1 API call

The table is four sub-trees and only one of them is data. This is the breakdown that explains the
storage bill and the 10,000-files-per-directory limit that took every upload down on 2026-08-27.

```bash
python3 - <<'PY'
import os, collections
from huggingface_hub import HfApi
info = HfApi().repo_info(os.environ["HF_REPO"], repo_type="dataset", files_metadata=True)
kinds = collections.defaultdict(lambda: [0, 0])
for f in info.siblings:
    if not f.rfilename.startswith("data/lancedb/"):
        continue
    parts = f.rfilename.split("/")
    kind = "/".join(parts[2:4]) if len(parts) > 4 else "/".join(parts[2:3])
    kinds[kind][0] += 1
    kinds[kind][1] += f.size or 0
for k, (n, b) in sorted(kinds.items(), key=lambda kv: -kv[1][0]):
    bar = "#" * min(40, n // 40)
    print(f"{n:>6} {b/1e6:>9.1f}M  {k:<34} {bar}")
PY
```

Reading it:

- **`_deletions/`** — one tombstone per `sync` that removed rows, and it grows as
  `ceil(deleted / chunk) x fragments`. Since the fragment count climbs ~10 per run, this is
  quadratic in runs, not linear. **This is the directory with the 10,000 ceiling.**
- **`_transactions/`** and **`_versions/`** — one file per table version, monotonic.
- **`data/`** — the actual `.lance` fragments. Usually the smallest count and nearly all the bytes.

The whole tree collapses to a few dozen files right after `cleanup-index` runs `index compact` and
re-uploads with `--delete "*"`, then climbs again. A count taken mid-sawtooth means little on its
own — compare it against `ceil(10000 / your_current_deletions_per_run)` runs of headroom.

## Storage: what you are billed for vs what is live · 2 API calls

```bash
python3 - <<'PY'
import os
from huggingface_hub import HfApi
api = HfApi()
repo = os.environ["HF_REPO"]
info = api.repo_info(repo, repo_type="dataset", files_metadata=True)
live = sum(f.size or 0 for f in info.siblings)
commits = len(api.list_repo_commits(repo, repo_type="dataset"))
print(f"usedStorage {(info.used_storage or 0)/1e9:.2f} GB   <- billed, includes every past revision")
print(f"live files  {live/1e9:.2f} GB   <- what a fresh clone would download")
print(f"commits     {commits}")
PY
```

`usedStorage` counts history; `live` does not. The gap is what `super_squash_history` reclaims, and
it falls on HF's own garbage-collection schedule rather than immediately — **a spike in `usedStorage`
alone proves nothing about what one run did.** Compare `live` across runs instead.

## Recent commits — who wrote what · 1 API call

```bash
python3 - <<'PY'
import os
from huggingface_hub import HfApi
for c in HfApi().list_repo_commits(os.environ["HF_REPO"], repo_type="dataset")[:15]:
    print(f"{str(c.created_at)[:19]}  {c.commit_id[:8]}  {c.title}")
PY
```

Four `nightly:` commits per pipeline run — embedding store, lancedb, descriptions, then
`board priority + published-dirs witness` last (ADR-0095);
`cleanup:` commits come from the daily compaction. A single `Super-squash branch 'main'` commit and
nothing else means the storage reclaim just ran — history is rewritten, not appended, so the log
starts over rather than growing.

## Read one file without cloning · 0 API calls

Known path in, bytes out, entirely on the Resolvers bucket.

```bash
python3 - <<'PY'
import os
from huggingface_hub import hf_hub_download
p = hf_hub_download(os.environ["HF_REPO"], "data/embeddings/jobs/manifest.json",
                    repo_type="dataset", local_dir="/tmp/hfpeek")
print(open(p).read())
PY
```

Useful known paths:

| path | what it tells you |
| --- | --- |
| `data/embeddings/jobs/manifest.json` | the vector store's `count`, `dim`, model |
| `data/state/published_dirs.json` | which state roots the pipeline last published (ADR-0095) |
| `data/state/board_priority.csv` | the board ledger, ~1 MB — `last_tech_jobs` per board |
| `data/state/derivations.json` | the `DERIVATIONS_VERSION` watermark |
| `data/lancedb/jobs.lance/_versions/latest_version_hint.json` | LanceDB's own pointer — **often stale**, do not trust it |

## Pulling data for real

- **Small slices** (`data/state/*`, ~1 MB): `snapshot_download` is fine.
- **`data/lancedb/`** (~1.9 GB): use `scripts/fetch/pull_lancedb.py`, **not** `snapshot_download` —
  ADR-0085 records four separate ways the latter failed on that pull. `--check` reports what is
  missing without fetching anything.
- Always `HF_HUB_DISABLE_XET=1` (ADR-0036): both paths get 429'd, but only the plain path survives it.

## See also

- `docs/agents/deployment.md` — the deployment's auth model and failure modes
- ADR-0030 / ADR-0095 — why a fetch that lists nothing is not automatically a first run
- ADR-0085 — why `pull_lancedb.py` exists
- ADR-0091 / ADR-0094 — the compaction that keeps `_deletions/` under its ceiling
