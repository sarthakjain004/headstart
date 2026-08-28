# ADR-0095: A published witness for unfetched state

**Status:** accepted · **Date:** 2026-08-28 · **Closes:** the hole
[ADR-0030](0030-fail-closed-on-unfetched-state.md) §Consequences knowingly left open ·
**Relates to:** [ADR-0033](0033-state-fetch-retry-budget.md) (the retry budget this must not
spend), [ADR-0050](0050-persist-descriptions-across-runs.md) (the store most exposed to it)

## Context

ADR-0030's property is **"never publish state derived from a prior state you failed to load."**
`state_fetch` enforces it by listing the repo before downloading, because `snapshot_download` does
not raise when the Hub is unreachable — it warns and hands back an empty local dir, and every state
root is gitignored, so on CI that is indistinguishable from a first run. Run 30304173982 nearly
replaced a 244,173-row served table with 11,555 rows exactly that way.

That closes the case where the listing **fails**. ADR-0030 states, and leaves open, the case where
it **succeeds and matches nothing**:

> "When the listing succeeds but matches nothing, the fetch requires nothing and the run bootstraps
> — which is correct for a genuine first run, and wrong if `HF_DATASET` ever points at an emptied
> or mistyped repo. Closing it needs a witness that survives a failed fetch, and the obvious
> candidate (the committed liveness ledger) is present on a fresh fork too, so it would reject
> exactly the bootstrap this allows."

The nearest live edge is `cleanup-index.yml`: it fetches `data/descriptions/*` guarded by
`|| exit 0`, then runs `update_descriptions --compact`, then `hf upload … --delete "*"`. The guard
covers a *failed* fetch. It does not cover a fetch that succeeds having matched nothing. What stops
a wipe there today is incidental — `--compact` iterates `store.glob("*")` and never creates the
directory, so the CLI is handed a path that does not exist — and it is untested.

## Decision

**Publish `data/state/published_dirs.json`: the state roots this dataset holds.** A file written
only into the dataset is the witness ADR-0030 wanted, because every state root is gitignored
(`.gitignore:56-62`) — so it is absent from a fresh fork and present on any repo the pipeline has
written. That is the asymmetry the committed liveness ledger lacked.

`state_fetch` reads it in exactly one place: when the listing matched nothing. If the witness
claims a root the patterns draw from, the fetch aborts instead of bootstrapping.

**It can only under-claim.** `publish` records the roots that exist locally and hold at least one
file, from one writer, at the moment of upload. Omitting a root costs nothing — the fetch behaves
as it did before this ADR. Claiming one wrongly would fail every later fetch closed, which is an
outage rather than a degradation, so nothing ever unions in a root it did not just observe. The
`data/state` upload moves **last** in `merge` for the same reason: committed after the roots it
vouches for, a mid-sequence failure leaves it naming fewer, never more.

**It abstains on `data/state/role_centroids`.** `cluster-roles.yml` writes that on its own
schedule, so a pipeline run that never touches it must not be read as having lost it.

**`break`, not retry.** The contradiction is deterministic; re-listing four more times learns
nothing and spends the ADR-0033 budget a real rate-limit would need. A witness that cannot be
*read* is a different case: it raises, lands in the ordinary handler, and is retried.

## Why not a file manifest

The original proposal was per-directory manifests listing every file, so a sync could fetch each by
known path and cost zero Hub API-bucket requests. Measured live 2026-08-28 against the real dataset:

| call | API-bucket cost |
| --- | ---: |
| `repo_info(expand=["siblings"])` | 1 |
| `snapshot_download('data/state/*')` → 36 files | 3 |
| 8 × `hf_hub_download` of known paths | **0** |
| the witness read — file **absent** (first run) | **0** |
| the witness read — file **present** | **0** |

against a policy read off a live response: `ratelimit-policy: "fixed window";"api";q=1000;w=300`.
So `state_fetch` costs ~4–5 API calls per invocation, ~30 per run — **~3% of the allowance**. The
429 that broke run 33159268268 carried no `RateLimit` header at all, so it was likely not that
limiter either.

And the two goals fight. A content manifest must stay **exact** across all seven of the dataset's
write points — four `hf upload` commits in `merge`, two `--delete "*"` in `cleanup-index`, one in
`cluster-roles` — or it over-reports after a compaction and every later fetch fails closed. A
presence witness has one writer and cannot over-claim. Same zero-API property, a seventh of the
lockstep burden, and it buys the safety hole instead of 3% of a budget nothing is short of.

Revisit the manifest only if a 429 ever arrives *carrying* a `RateLimit` header — which ADR-0095's
sibling change to `limiter_note` now makes visible in the log.

## Consequences

The happy path is unchanged and costs nothing: a fetch that matches files never reads the witness.

`remote_files` stays exactly as it is. It is the fail-closed listing and it holds the
`siblings is None` guard; this adds a second, independent check rather than replacing the first.

**The first run after this ships carries no witness**, reads as a first run, and publishes one. No
flag day, and no bootstrap opt-out — the property ADR-0030 valued is preserved.

**A fresh fork still bootstraps**, which is the point. Its `HF_DATASET` names an empty repo with no
witness, so the fetch requires nothing.

**The witness is only as honest as `merge`.** If `merge` ever stops running `publish` while still
uploading `data/state`, the witness freezes at its last value — under-claiming as roots are added,
which is safe, but silently. There is no alarm for that, and adding one would need a second writer,
which is the lockstep this design exists to avoid.

**Not covered:** a dataset emptied *and* re-published by something other than this pipeline would
carry a witness matching its emptiness. Nothing here defends against a writer that is not `merge`.
