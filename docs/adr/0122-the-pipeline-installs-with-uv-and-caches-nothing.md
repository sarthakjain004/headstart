# ADR-0122: The pipeline installs with uv, and caches nothing to do it

**Status:** accepted · **Date:** 2026-09-09 · **Relates to:**
[ADR-0020](0020-free-tier-deployment.md),
[ADR-0025](0025-parallelize-nightly-pipeline.md),
[ADR-0026](0026-parallelize-nightly-scrape.md) (the five jobs this changes),
[ADR-0091](0091-compaction-outranks-the-pipeline.md) (compaction runs from `cleanup-index.yml`,
the other consumer of the shared pip cache)
· **Measurement:** `docs/pipeline/2026-09-09_env-install-benchmark.md`

## Context

Every job in `pipeline.yml` rebuilt its Python environment from scratch, and the three serial
post-scrape jobs (`join`, `embed`, `merge`) each installed `.[embed]` — which pulls
`sentence-transformers`, therefore torch, therefore the whole nvidia CUDA wheel set. The observed
cost was ~2.3 min per job of checkout-to-first-stage, and the obvious reading was that the
`pip install` was the thing to attack.

That reading was half right, and the half it got wrong is the reason this ADR exists. Measured
across 12 job-observations on four runs, the cost is **two** costs that behave differently:
`setup-python` (which is almost entirely the restore of the pip download cache) at **28–64 s**,
and `pip install` at **75–80 s**. The install is stable; the restore swings 2.3x on identical
inputs.

The proposal on the table was a prebuilt image or an `actions/cache` over the built venv. Both
were measured rather than assumed, alongside `uv` and a CPU-only-torch build, all timed
sequentially inside one job on one runner — because
`docs/pipeline/2026-09-08_five-run-log-review.md` measured a 2.6x speed spread across nominally
identical `ubuntu-latest` runners, which makes any cross-job A/B worthless. An ordering control
(the baseline variant repeated verbatim at the end of the same job) put the noise floor at **±9%**.

## Decision

Two separable changes, both applied.

**A. Delete `cache: pip` from all five pipeline jobs.** It returns nothing. pip with its HTTP cache
switched off was never slower than pip with the 4.4 GB cache restored (n=3 on `.[embed]`), because
that install is dominated by unpacking 6.1 GB to disk rather than by downloading. On `scrape` and
`scrape-plan` the case is arithmetic and needs no A/B at all: a 29–63 s restore cannot repay a
5–12 s total install at any hit rate. `setup-python` without the cache costs 0–1 s (n=16), so the
whole restore is recovered.

**B. Install `.[embed]` with a pinned `uv` on `join`, `embed` and `merge`.** 5.3x faster than pip
(median 16.0 s against 84.6 s, n=3 within-run), and — the property that actually decides it — uv
with a *cold* cache beats pip holding a warm 4.4 GB one. So B removes the reason to hold a cache
rather than trading one cache for another. End to end, on the command that ships
(`uv pip install --system`), checkout-to-importable-environment went **128.7 s → 18.9 s**.

`uv` is version-pinned in a single workflow-level `env: UV_VERSION`, for the reason
`pyproject.toml` pins `ruff==0.16.6`: a resolver whose behaviour shifts between releases would
change what the pipeline installs with no commit to point at. Unlike ruff's pin, **Dependabot does
not see this one** — it parses manifests and `uses:` refs, not a version literal in a `run:` line
— so it is hand-maintained, which is why it is one named variable rather than three inline
literals that would drift apart.

B is deliberately **not** applied to `scrape`/`scrape-plan`. uv would help there too (1.6 s against
10.6 s), but the win is ~9 s against ~60 s on the `.[embed]` jobs, and `scrape` is the run's
critical path and its most failure-prone job (WARP, browser escalation, ADR-0056). Change A already
takes 29–63 s out of it. That swap is a separate decision on its own merits.

## Rejected

**The cached built venv — rejected on adoptability, not speed.** It works: the restored venv
imported every package and `headstart` resolved to the real checkout. But it is **2,900 MB
compressed**, and the repo's Actions cache stood at **8.12 GB of a 10 GB ceiling**, leaving ~1.9 GB
of headroom. Saving it during the experiment took the repo to 10.64 GB and started eviction. The
likeliest victim is `hf-model-nomic-embed-text-v1.5` (482 MB) — precisely what `embed`'s own
`actions/cache` step exists to protect — and losing it means 15 shards re-downloading the model in
parallel. A change that speeds up `join` by moving cost into `embed` is not a speed-up. It also
loses on time: a 30–53 s restore against uv's 14–24 s install.

**A prebuilt image** — unnecessary once the install it exists to avoid is 14–24 s.

**CPU-only torch** — real but smaller (6.1 GB → 1.7 GB, install 84.6 s → 53.9 s) and it changes
*what* is installed rather than how. Recorded because it composes with B if this is ever revisited;
the combination was not measured.

**An `actions/cache` for `~/.cache/uv`** — would make the install nearly free (0.7 s) but
re-imports the cache-budget problem above for a ~15 s saving.

## Consequences

Projected ~4.8–8.6 min off a 53.8–68.3 min run when this was written. **Confirmed 2026-09-10 at
≈6.0 min**, from four runs on the merged code (`34433479155`, `34429796522`, `34426795362`,
`34423283174`). Per-job pre-work cost fell from ~108 s / ~110 s / ~139 s on `join` / `embed` /
`merge` to medians of 22.5 s / 21 s / 27 s, and from ~52 s / ~47 s to ~12 s on `scrape-plan` /
`scrape`. `setup-python` measured 0–1 s in **20 of 20** job-observations, confirming the restore
was its entire cost.

One correction the real runs force: uv's install is **less consistent** in production than on the
bench. It ran 16–38 s (median ~18.5, n=12) against the bench's 14.1–24.2 s, so the honest
production figure is **4.1x** faster than pip, not the bench's 5.3x. Size any timeout off the
16–38 s range. Details: `docs/pipeline/2026-09-09_env-install-benchmark.md` §7b.

The cache-budget headroom does **not** come back. `setup-python`'s key is
os/arch/python-version/pyproject-hash — repo-wide, not per-workflow — and **nine** other workflows
still declare `cache: pip` (`ci`, `cleanup-index`, `cluster-roles`, `diff-role-assignments`,
`embed-bench`, `embed-threads`, `pipeline-smoke`, `probe-successfactors-ua`, `probe-workday-400`),
including `cleanup-index.yml`, which installs the same `.[embed]` and
so keeps that entry alive. Deleting it from `pipeline.yml` alone frees nothing. Extending Change A
to those nine is a follow-up, and it would also spare `cleanup-index` — which runs the
compaction ADR-0091 ranks *above* the pipeline — the same 28–64 s restore.

`uv pip install --system` must keep console scripts on PATH, because `merge` shells out to
`hf upload` in the `up()` retry helper of its "Upload index state" step — the step that ships the
run's data. That is verified on a real runner, not assumed; the benchmark's `proposed` job runs
`hf --version` under the exact shipping install.
