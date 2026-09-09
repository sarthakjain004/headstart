# CI environment-install A/B — 2026-09-09

Every pipeline job rebuilds its Python environment from scratch. This measures whether that is
avoidable, and by what mechanism. The decision it led to is
[ADR-0122](../adr/0122-the-pipeline-installs-with-uv-and-caches-nothing.md); this document is the
evidence behind it.

Four `bench-env-install` dispatches: `34341080096` (cold), `34341460481`, `34342977545`,
`34344170457`. Production baselines come from pipeline runs `34332773221`, `34327339789`,
`34321068300`, `34316866965`.

**Dispatch `34341080096` is discarded from every headline number.** It was the cold dispatch: the
venv-cache variant necessarily missed, and it predates two variants added after it. Its other
figures agree with the warm dispatches and are shown only for reference.

The benchmark workflow that produced these numbers was deliberately not landed on `main` — the
question it answers is now answered, and a permanently-installed workflow for it would be
speculative. **This document is the authoritative record of the method**: §0 states the design, §2
the variants and their exact commands, §6 the end-to-end shape. The workflow itself survives on the
branch `bench/env-install-ab` as a convenience for re-running, but that branch is prunable and
nothing here depends on it.

## 0. The noise floor, before any ratio

`docs/pipeline/2026-09-08_five-run-log-review.md` measured a **2.6x end-to-end speed spread across
nominally identical `ubuntu-latest` runners**. A cross-job or cross-dispatch A/B is therefore
worthless at anything under ~2x. So every variant here ran **sequentially inside one job on one
runner**, and the headline is a **within-run ratio**, which cancels the lottery.

That leaves one confound the design cannot cancel: position in the sequence. `V1r` is a verbatim
repeat of `V1` at the end of the job, and measures exactly that.

| dispatch | V1 (s) | V1r (s) | V1r / V1 |
|---|---|---|---|
| `34341080096` (cold) | 77.34 | 82.66 | **1.069** |
| `34341460481` | 83.51 | 76.14 | **0.912** |
| `34342977545` | 104.74 | 100.20 | 0.957 |
| `34344170457` | 84.55 | 81.85 | 0.968 |

The drift changes sign — +6.9% once, negative three times — so position is **noise, not
advantage**. **The noise floor is ±9%.** Nothing below about 1.1x should be believed from this
harness; the winning ratio below is 4.3–5.9x.

**Calibration.** The bench reproduces production. `V0` runs join's exact command
(`pip install -e ".[embed]" huggingface_hub`) into the system interpreter and returned 72.8–86.3 s
against production's 75–80 s. The separate `baseline_prod` job, which replicates today's whole
configuration end to end, returned **128.73 s** against a production median of **~129 s**.

## 1. Where the time actually goes today

The original review attributed the ~2.3 min checkout-to-first-stage window loosely and named only
the `pip install` half. It is two costs, and they behave differently.

| job group | `setup-python` (= pip-cache restore) | `pip install` | combined |
|---|---|---|---|
| `join` / `embed` / `merge` (`.[embed]`) | **28–64 s**, median ~52 | **75–80 s**, median 76.5 | 103–144 s |
| `scrape` × 15 (`.[scrape]`) | **29–63 s**, median ~45 | **8–11 s**, median 9 | 37–74 s |
| `scrape-plan` (`.` + `huggingface_hub`) | **41–60 s** | **5–12 s** | 46–72 s |

n = 12 job-observations for each of the first two rows, 4 for `scrape-plan`, over the four runs
named above. Raw per-observation values, since Change A rests on them
(`setup-python` / `pip install`, seconds):

| run | `join` | `embed (0)` | `merge` | `scrape-plan` | `scrape (0)` | `scrape (7)` | `scrape (14)` |
|---|---|---|---|---|---|---|---|
| `34332773221` | 28 / 77 | 29 / 77 | 60 / 76 | 41 / 8 | 36 / 8 | 40 / 11 | 54 / 10 |
| `34327339789` | 56 / 80 | 59 / 76 | 49 / 76 | 53 / 10 | 31 / 10 | 61 / 8 | 62 / 9 |
| `34321068300` | 62 / 77 | 64 / 75 | 28 / 78 | 52 / 5 | 31 / 9 | 29 / 8 | 30 / 9 |
| `34316866965` | 60 / 76 | 45 / 76 | 30 / 76 | 60 / 12 | 44 / 9 | 56 / 9 | 63 / 9 |

Read down the restore column and then down the install column: the install never leaves a 5-point
band on either dependency set, while the restore ranges 28–64 s on the same job across four runs.

The install is the stable half; **the restore is the volatile half, swinging 2.3x on identical
inputs**. Any saving from removing it must be quoted as a range, not a point estimate.

`setup-python` without `cache: pip` costs **0–1 s** (n = 16 job-observations in `bot.yml` and
`deploy-space.yml`, which already omit it). So the 28–64 s *is* the restore, measured rather than
inferred.

One structural detail that matters: all five pipeline jobs declare `cache: pip` with no
`cache-dependency-path`, so they compute the **same cache key and share one entry**. That entry is
whatever the `.[embed]` jobs put in it — 4.4 GB including torch and the nvidia CUDA wheels. Every
`scrape` shard restores all of it to install `pydoll-python`.

## 2. The variants

All figures are seconds, from the three warm dispatches. Every variant was verified by importing
`headstart`, `sentence_transformers`, `torch`, `lancedb`, `langdetect`, `huggingface_hub`,
`sklearn`, `einops` and `curl_cffi`. **No variant was fast-but-broken**; all produced a working
environment.

| variant | `34341460481` | `34342977545` | `34344170457` | median | (cold ref) |
|---|---|---|---|---|---|
| V0 system pip — production command | 72.78 | 86.27 | 76.56 | 76.56 | 72.83 |
| V1 venv + pip, warm 4.4 GB pip cache | 83.51 | 104.74 | 84.55 | 84.55 | 77.34 |
| V5 venv + pip, **no** pip cache | 78.13 | 84.47 | 82.10 | 82.10 | — |
| **V3a uv, cold uv cache** | **14.12** | **24.18** | **15.99** | **15.99** | 17.48 |
| V3b uv, warm uv cache | 0.66 | 1.05 | 0.68 | 0.68 | 0.65 |
| V4 pip, CPU-only torch | 53.87 | 52.89 | 56.42 | 53.87 | 55.30 |
| V6 CPU torch + no pip cache | 56.08 | 63.47 | 61.55 | 61.55 | — |
| V2 cached venv — restore | 30.25 | 52.64 | 36.05 | 36.05 | miss |
| V2 cached venv — verify imports | 10.74 | 11.22 | 12.09 | 11.22 | — |

Small dependency sets, measured the same way on one runner (dispatch `34344170457`, n = 1 each):

| variant | seconds |
|---|---|
| S1 `.[scrape]` with pip cache | 12.33 |
| S2 `.[scrape]` **no** pip cache | 10.58 |
| S1r `.[scrape]` with pip cache, repeated | 9.20 |
| S3 `.` + `huggingface_hub`, with pip cache | 7.99 |
| S4 `.` + `huggingface_hub`, **no** pip cache | 9.40 |
| S5 `.[scrape]` uv, cold | **1.57** |
| S6 `.` + `huggingface_hub` uv, cold | **1.16** |

## 3. Change A — `cache: pip` returns nothing, and costs 28–64 s

**On `.[embed]`, this is a measurement.** V1 (cache restored) against V5 (cache off) gives
V1/V5 = **1.069, 1.240, 1.030** across the three warm dispatches. The cache never won. Two of the
three gaps sit inside the ±9% ordering noise and only `34342977545` clears it outright — so the
honest reading is *the cache's install benefit is at most marginal and is indistinguishable from
zero*, not *the cache is reliably harmful*. Either way it does not repay a 28–64 s restore.

The mechanism is that `.[embed]` install time is dominated by **unpacking 6.1 GB to disk**, not by
downloading. V3a is the proof: uv downloads every wheel cold, with no cache of any kind, and still
finishes in 14–24 s.

**On `scrape` and `scrape-plan`, this is arithmetic and needs no A/B.** The restore costs 29–63 s
to accelerate an install whose *entire* duration is 5–12 s. Even a perfect cache driving the
install to zero could not repay it: the maximum possible saving is bounded below the minimum
observed restore.

The small-install A/B does **not** add to that, and is reported here rather than leaned on. S1
(12.33 s, cache) against S2 (10.58 s, no cache) points the same way as `.[embed]`; but S3
(7.99 s, cache) against S4 (9.40 s, no cache) points the **other** way — the cache won that pair by
18%. Both are n=1, and the S1/S1r ordering drift on these short installs is **−25%**, larger than
either effect. So the honest statement is that this A/B **cannot resolve** a difference this small,
which is exactly why the bound, not the delta, is the argument for these two jobs.

**Evidence coverage.** Change A is supported by direct measurement on the `.[embed]` jobs
(`join`, `embed`, `merge`) and by a bound plus a confirming measurement on `scrape` and
`scrape-plan`. All five jobs are covered.

**The cache budget does not come back, and an earlier draft of this document claimed it would.**
That claim was wrong and is corrected here. `setup-python`'s cache key is
os / arch / python-version / pyproject-hash — **repo-wide, not per-workflow**. Measured before
this change landed, **ten** workflows declared `cache: pip`: `pipeline.yml` plus **nine** others
(`ci`, `cleanup-index`, `cluster-roles`, `diff-role-assignments`, `embed-bench`, `embed-threads`,
`pipeline-smoke`, `probe-successfactors-ua`, `probe-workday-400`). This change removes it from
`pipeline.yml`, leaving those nine. Only `ci.yml` sets a distinct `cache-dependency-path`, and
since the repo has a single root `pyproject.toml` that likely hashes to the same key anyway; the
rest certainly share one. `cleanup-index.yml` declares it at line 159 and
installs `.[embed]` at line 160 — the very install that fills the 4.4 GB entry — and it runs daily
on its own cron. So deleting `cache: pip`
from `pipeline.yml` alone frees **nothing**: the entry keeps being restored and stays alive.

Two consequences follow. The ~1.9 GB headroom in §5 stays as it is, so the adoptability constraint
on any future cache idea is unchanged. And `cleanup-index` — which runs the compaction ADR-0091
ranks *above* the pipeline — keeps paying the same 28–64 s restore for no return. **Extending
Change A to those nine workflows is the obvious follow-up** and is deliberately out of scope
here, to keep this change to one workflow.

## 4. Change B — pinned `uv` instead of `pip`, on the three `.[embed]` jobs

V1/V3a = **5.91, 4.33, 5.29** across the three warm dispatches (median 5.29x), against a ±9% noise
floor. Against the production command V0, V0/V3a = 5.15, 3.57, 4.79.

The decisive property is not the ratio but that **V3a needs no Actions cache at all**. uv with a
genuinely cold cache (`rm -rf ~/.cache/uv` immediately before) beats pip holding a warm 4.4 GB
cache by more than 4x. So Change B does not trade one cache for another; it removes the reason to
have one.

V3b (0.66–1.05 s) shows what a warm uv cache would give, and is reported only to explain the
mechanism — uv hardlinks from its cache, so a populated cache makes install nearly free. **It is
not proposed**: an `actions/cache` entry for `~/.cache/uv` would re-import the budget problem in
§5 for a saving of ~15 s.

uv is pinned to **0.12.11**, the version measured, for the reason `pyproject.toml` already pins
`ruff==0.16.6`: a resolver whose behaviour shifts between releases would change what the pipeline
installs with no commit to point at.

Change B is **not** applied to `scrape` or `scrape-plan`. uv would help there too (1.57 s against
10.58 s), but the saving is ~9 s per job against ~60 s on the `.[embed]` jobs, and `scrape` is the
run's critical path and its most failure-prone job (WARP, browser escalation, ADR-0056). Change A
already takes 29–63 s out of it. Swapping the installer there is a separate, smaller change that
should be justified on its own.

## 5. What was rejected, and why

**The originally proposed mechanism was wrong, though the cost it identified was real.** The
review that prompted this work suggested a prebuilt image or a cached built environment. Both are
rejected:

**V2, the cached venv — rejected on adoptability, not speed.** It works: the restored venv
imported everything, and `headstart` resolved to the real checkout at
`/home/runner/work/headstart/headstart/src/headstart/__init__.py`. But it is **6.1 GB on disk and
2,900 MB compressed**, and the repo's Actions cache stood at **8.12 GB of a 10 GB ceiling** before
this experiment — two `setup-python` pip caches (4,415 MB and 2,841 MB), the 482 MB
`hf-model-nomic-embed-text-v1.5` entry, and a 0 MB tokenizer entry. A 2,900 MB entry does not fit
in ~1.9 GB of headroom. Saving it during this experiment took the repo to 10.64 GB and started
eviction.

That is a correctness objection, not a budget quibble: the entry most likely to be evicted is what
the `embed` job's own `actions/cache` step exists to protect, and losing it means **15 embed shards
each re-downloading the 482 MB model in parallel**. A change that speeds up `join` by evicting the
model cache moves cost into `embed`. (The experiment's own entry was deleted immediately after the
last dispatch; `hf-model-nomic-embed-text-v1.5` survived.)

Even setting the budget aside, V2 loses on time: a 30–53 s restore against uv's 14–24 s install,
which needs no cache entry at all.

**A prebuilt image is unnecessary.** It exists to avoid install cost that, measured, is 14–24 s.

**V4/V6, CPU-only torch — not proposed, but worth recording.** The runners have no GPU, so every
nvidia CUDA wheel PyPI's default torch drags in is unpacked and never used. CPU-only torch cuts the
environment from **6.1 GB to 1.7 GB** and the install from 84.55 s to 53.87 s (median). That is a
real 1.6x, but it is strictly worse than uv's 5.3x, and it changes *what is installed* rather than
how — a correctness surface (`torch.version.cuda` becomes `None`) for a smaller win. Recorded
because it composes with Change B if install time is ever revisited: uv plus CPU-only torch was not
measured together.

## 6. End-to-end, on the command that will actually ship

The variants above measure uv installing into a venv. Production would use
`uv pip install --system`, which is a different command, so it was measured separately as two whole
jobs timing checkout → importable environment (dispatch `34344170457`):

| job | configuration | seconds |
|---|---|---|
| `baseline_prod` | `setup-python` + `cache: pip`, then `pip install -e ".[embed]" huggingface_hub` | **128.73** |
| `proposed` | `setup-python` no cache, `pip install uv==0.12.11`, `uv pip install --system -e ".[embed]" huggingface_hub` | **18.86** |

Both then imported `headstart.ingest.embed_plan` and `headstart.ingest.index` successfully — the
real pipeline modules, not a proxy. **6.8x, or 109.9 s saved.** A second sample on dispatch
`34363792555` put `proposed` at **18.58 s**, within 0.3 s of the first.

**Console scripts survive the swap, and this was measured rather than assumed.** `merge` does not
only import Python — it shells out to `hf upload` inside the `up()` retry helper of its "Upload
index state" step, which is the step that ships the run's data. An installer that skipped entry
points would break it, and §6's original job checked only imports. Run `34363792555` added the
check under the exact shipping install:

```text
which hf      -> /opt/hostedtoolcache/Python/3.12.14/x64/bin/hf
hf --version  -> 1.30.0
CONSOLE SCRIPT OK: hf is executable
```

`uv pip install --system` writes entry points into the `setup-python` interpreter's `bin`, which is
already on `PATH`. This was a real risk, not a theoretical one: the first version of this change
shipped a comment asserting "nothing in this workflow invokes a console script", which review found
to be false.

These are two jobs on two runners, so the runner lottery applies and this pair is **not** the
primary evidence — §2's within-run sequence is. It is a confirmation that the shipping command
behaves like the benchmarked one, and its baseline landing within 0.3 s of the production median is
the reason to trust it.

## 7. Projected saving

**This section is a projection, not a measurement.** It composes measured per-job costs; no run has
yet been observed with the change in place.

| job | today | after A (+B where applied) | saving |
|---|---|---|---|
| `join` | 103–144 s | 15–27 s | 76–129 s |
| `embed` (matrix, parallel — counts once) | 103–144 s | 15–27 s | 76–129 s |
| `merge` | 103–144 s | 15–27 s | 76–129 s |
| `scrape-plan` | 46–72 s | 5–13 s | 33–67 s |
| `scrape` (matrix, parallel — counts once) | 37–74 s | 8–12 s | 25–63 s |

`join`, `embed` and `merge` are strictly serial, so their savings add. The two matrix jobs pay the
cost on every shard in parallel, so each contributes its saving to the makespan once.

Total projected: **~4.8–8.6 min** off a run whose recent wall clock is 53.8–68.3 min — roughly
**8–14%**. The wide range is the volatile restore, and it is why this is quoted as a range.

Two things this projection assumes and does not demonstrate: that no stage becomes the new
critical path (`scrape` remains floor-bound on `successfactors:careers.hcltech.com`, unaffected by
any of this), and that uv resolves the same dependency set as pip — the import checks confirm the
packages are present and importable, not that every transitive version matches.

## 8. Artifact I/O — measured, no change proposed

The brief that prompted this work stated that `join` uploads a ~70 MB fragment in ~3.2 min, which
would be 0.36 MB/s and worth investigating. **That premise is wrong.** From run `34327339789`'s own
log:

```
Artifact corpus-state has been successfully uploaded! Final size is 2145258252 bytes.
```

The artifact is **2.145 GB**, not 70 MB. The upload window (08:39:27 → 08:42:36, 189 s) is
therefore **11.3 MB/s**, and `merge`'s 101 s download of the same artifact is **21.2 MB/s**. As a
cross-check on the same runner class, a 70 MB single-file fixture uploaded in 1.36–1.93 s
(36–51 MB/s) and downloaded in 0.98–1.46 s across the four dispatches; `compression-level: 1`
changed nothing outside noise (1.17–1.80 s).

There is no anomaly. Moving 2.1 GB between two jobs costs what it costs. **No change is proposed.**
The only lever would be not shipping 2.1 GB through an artifact at all, which is a pipeline design
question, out of scope here.

## 9. What this does not cover

- **uv's resolution is not proven equivalent to pip's.** The import checks prove the packages are
  present and importable. They do not prove every transitive pin matches what pip would choose.
- **`scrape`/`scrape-plan` keep pip.** Change B is measured but deliberately not applied there
  (§4).
- **A warm-uv-cache configuration was measured but not proposed** (§4).
- **CPU-only torch composed with uv was not measured** (§5).
- **No pipeline run has executed with these changes**, so every figure in §7 is projection. What
  *was* verified, on a real runner under the exact shipping install
  (`uv pip install --system -e ".[embed]" huggingface_hub`): the nine third-party imports listed in
  §2; `headstart.ingest.embed_plan` and `headstart.ingest.index`, the real pipeline modules; and
  `hf --version`, because `merge` shells out to the `hf` **console script** in its "Upload index
  state" step and an installer that dropped entry points would break the step that ships the run's
  data. The gap is end-to-end pipeline behaviour, not whether the environment is usable.
