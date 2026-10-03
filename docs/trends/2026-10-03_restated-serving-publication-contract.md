# Restated Trends serving and publication contract

Status: implementation direction approved by the parent; serving/publication code built.
Production validation and release remain pending the parent's independent verifier.
No publication, workflow dispatch, deployment or release is authorized by this document.
Implements ADR-0330 step 4 with the owner's newer instruction: retain older history explicitly
as unrecomputed legacy history; never splice it into a restated generation.

## Observed interfaces

Space boot (`deploy/hf-space/app.py`) downloads the legacy tick directory, the index archive,
Company directory and dedup eviction ledger alongside the Search index. It loads
`TrendHistory.load(state_dir, config_dir)` once, and answers and ranks Hiring now from that
history. The reader accepts the same tick-file format the replay writes. Its taxonomy and
watchlist come from `config_dir`. The Space must continue downloading no Job facts,
description archive or pipeline vector store.

The inspected `codex/restate-memory-fix` engine writes `role_trend_board_deltas/*.parquet`
and `placements/*.parquet` under `--out`. It does not yet provide a publication manifest.
Its output directory is replaced during replay: packaging must run only after replay finishes.
Per-id placements are verifier inputs, never part of the Space artifact.

ADR-0330's existing validator certifies observed tech stock placements under preserved rules.
It explicitly does not certify watched roles, `new`, turnover or raw-scrape admission replay.
The production gate must come from the independent verifier worker; a report assembled from
the candidate's own counts cannot satisfy it. Historical production equality under different
rules or different Board coverage is not a publication gate.

## Proposed module interfaces

`ingest.restate_publish` packages an allowlisted small history, validates an independently
written gate report, and publishes the immutable generation and current pointer in one commit.
An initial CLI dry run validates and produces the package locally; a separate explicit publish
operation requires the release gate. Neither mode generates a verifier report.

`trends.restated_history` selects a verified generation and loads it through the existing
`TrendHistory` reader. It downloads the pointer first and pins the pointer's dataset revision
for every subsequent download. Its result exposes the selected history, its provenance and
the separately loaded legacy history. Default requests use the verified generation; explicit
`history=legacy` selects the old history. An absent or invalid generation falls back to legacy
with a recorded reason. Search's live taxonomy hand-off remains separate from the generation's
pinned chart taxonomy.

Hiring now, Search, default company lookup/suggestions and live posting-date splits retain the
fresh pipeline History. Only Trends and its explicitly scoped picker prefer a verified replay.
New companies remain visible while the replay runs.

The app discloses history selection and coverage in the response, including when it falls
back. Cache keys must include the selected History object. No response may claim older legacy
ticks were recomputed. The legacy archive is never inside a generation.

## Proposed JSON contract, version 1

Paths in the private index dataset:

```
data/trends/restated/current.json
data/trends/restated/generations/{generation}/manifest.json
data/trends/restated/generations/{generation}/validation.json
data/trends/restated/generations/{generation}/role_trend_board_deltas/*.parquet
data/trends/restated/generations/{generation}/company_directory.json
data/trends/restated/generations/{generation}/config/role_families.json
data/trends/restated/generations/{generation}/config/role_watchlist.json
```

Paths preserve candidate-relative names so the report's file inventory is identical after
packaging. Optional `dedup_evictions.csv` is included only if the replay/verifier establishes
its meaning for this generation. Never copy the live eviction ledger blindly into a restatement.
The Company directory is built from the generation's tech Boards using the established naming
policy. It contains named groups only; legitimately unnamed Boards remain in aggregate history
with explicit naming-coverage counts. It must not invent employers or counts.

The pointer contains `schema_version`, `generation`, `manifest_sha256`, `last_covered_tick`,
`rules_fingerprint`, and `rules_code_sha`. It references no mutable generation path.

Manifest fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer 1; unknown versions are refused. |
| `generation` | Content-derived generation identity binding manifest payload and report. |
| `rules_fingerprint` | Computed hash of current rule code, config, head weights and Board inputs. |
| `rules_code_sha` | Main-branch source revision used for replay; ordering is by ancestry. |
| `input_revision` | Exact HF revision pinned before downloading replay inputs. |
| `first_covered_tick`, `last_covered_tick` | Actual replay boundaries, not publication time. |
| `files` | Exact sorted artifact inventory: relative `path`, SHA-256 and byte `size`. |
| `inputs` | Exact selected replay-input inventory with paths and content hashes. |
| `validation` | Report path, SHA-256 and byte size; report is not included in its own inventory. |
| `quality` | Verifier's certified semantics and coverage, retained without interpretation. |
| `limitations` | Explicit limitations, including approximate vector precision and missing provenance. |
| `legacy_history` | `{ "available": true, "recomputed": false, "spliced": false }`. |

The manifest's inventory excludes itself and the verifier report to avoid recursive hashes.
The pointer hashes the manifest; the manifest hashes the independent report. The report binds
exactly the same artifact inventory. Paths must be relative, traversal-free and allowlisted;
duplicate paths, extra files, wrong size/hash, absent ticks and unordered/duplicate stamps fail.
All tick schemas and tick bounds are validated before selecting a generation. The complete
package is capped at 128 MiB and individual JSON documents at 16 MiB. Advertised total size
is checked before payload downloads; actual hashes and sizes are checked after download.

Independent report fields agreed with the parent verifier:

```
schema_version: 1
complete: true
pass: true
rules_fingerprint: <same as manifest>
input_revision: <same pinned HF revision>
first_covered_tick: <same as manifest>
last_covered_tick: <same as manifest>
files: <same exact path / sha256 / size inventory>
inputs: <same selected replay-input inventory>
verifier: {name: <independent verifier>, code_sha: <source revision>}
quality: <structured certified scope and coverage>
limitations: <explicit list>
checks: <independent evidence and per-check verdicts>
```

Both booleans must be literally true. A partially completed report, stock-only pass described
as full validation, or a report naming another output is refused. The verifier must define the
required certified scope for enabling the existing stock/new/watched-role/turnover interfaces.
An unsupported interface should remain explicitly unavailable rather than gain invented proof.
New ATSes, Boards and companies are coverage differences, not failures. No exact equality with
an older liveness ledger is required. Schema agreement alone is not independent verification.

## Generation Company labels and coverage

Preparation rebuilds named groups with `company_directory.companies(generation_boards,
previous_names(pinned_directory))`. This reuses curated aliases, Tenant identity, stated-name
checks, humanized tenants and the canonical Operator policy. It does not merge companies merely
because their names match. Existing labels carry forward where the policy preserves them;
new humanizable Boards can become named groups. Opaque Boards with no defensible name remain
unnamed under ADR-0212. No LanceDB download or latest-index naming lookup is needed.

The pinned source directory must have nonblank named labels, nonempty Board lists and unique
Board membership. An explicit duplicate source remains a hard failure, even if outside the
generation; rebuilding must not conceal corrupt input. Prepared and served named membership
must be unique and a subset of generation tech Boards. Non-tech diagnostic rows do not require
Company labels. An empty `companies` list is valid when no generation company can be named.

Preparation writes measured coverage into `replay.json.quality.company_labels`, preserving the
other quality fields, replay identity and input inventory:

```
history_boards: number of generation tech Boards
named_boards: number of Boards in named generation groups
unnamed_boards: history_boards - named_boards
```

The independent verifier already computes the same counts as
`validation.json.quality.company_labels`. Packaging requires prepared and verified coverage
to match the actual generation. The manifest retains verifier quality, and the serving loader
checks its coverage counts against the downloaded tick files and labels. This measures naming
coverage; it is not certification of employer names or grouping accuracy.

Tick Parquets and their hashes remain untouched, so unnamed Boards are not dropped from counts.
Placements remain verifier-only and outside the serving artifact. Publication still requires
an independently complete/passing report and explicit release approval; preparation regressions
do not constitute a production validation gate.

The saved 37125686815 candidate illustrated the mismatch: 35,778 generation tech Boards versus
33,820 named by its hash-verified pinned directory. Rebuilding under unchanged naming rules
names 35,732 Boards in 34,944 groups, leaving 46 unnamed. These are measured saved-run results,
not live coverage figures or a waiver of independent validation.

## Publication while pipeline runs continue

Pin the input revision and exact consumed file inventory before replay. At publication, read
the latest HF head, check the current pointer, and compare the selected facts/reference files'
content hashes at that head. Newly appended fragments after `last_covered_tick` are allowed;
changes or deletion of selected inputs invalidate the candidate. Mutable state inputs are
either frozen into the selected input revision or reconciled by the engine's contract.

Use an HF commit with `parent_commit` equal to the head just checked. On conflict, refresh head,
recheck selected content and pointer, then retry with a bounded retry budget. Reject a pointer
already covering a later tick. For equal ticks, require identical generation or demonstrably
newer rules by Git ancestry; reject incomparable revisions. Never replace a newer rules
generation with older rules merely because the older replay completed later. Write every
generation file, manifest, report and pointer in one additive commit. Do not delete generations.
Keep this root outside `data/state` so a pipeline state-folder upload cannot restore a pointer.

Preserve the candidate's full rules/input fingerprint and code SHA, including its frozen Board
inputs, for validation. Company-ledger growth alone is a coverage difference and permits
publication; the next scheduled run catches it. Serving explicitly identifies the verified
code SHA rather than claiming current-rule equality. Source ancestry and covered ticks prevent
an older replay replacing a newer verified generation. A subsequent rule change queues another
current-main replay; the already verified prefix remains explicitly versioned until it lands.

## Workflow and integration dependencies

Add only `publish-restated-trends.yml`; leave the original benchmark workflow unchanged.
Schedule input-tip checks, trigger on relevant rule/config/Board-input pushes, and allow manual
dispatch. Use a separate serialized concurrency group with `cancel-in-progress: false` so
hourly ingest never cancels a two-hour replay. Checkout current main at execution time; do not
run preserved benchmark rules for production. Skip a replay only when both computed rules
fingerprint and covered input tip already match the published generation.

The workflow remains publication-disabled until parent approval, and cannot dispatch before
the current-rules engine and independent verifier are integrated on main. A Space upload or
restart also requires that approval. Missing engine/verifier must fail before expensive fetch.
Replay inputs are runner-only; only the allowlisted small package reaches Space.

## Parent integration instructions

The pinned runner-only fetch is `python -m headstart.ingest.restate_publish fetch`. It writes
`data/restated-inputs.json` with `input_revision` and path/size/SHA-256 entries (`git_blob` also
included for non-LFS content). In Actions it exports `HEADSTART_RESTATE_INPUT_REVISION` and
`HEADSTART_RESTATE_INPUT_INVENTORY`. Checkout uses `ref: main`, then sets
`HEADSTART_RESTATE_CODE_SHA=$(git rev-parse HEAD)` via `GITHUB_ENV`; this is the actual fresh
checkout's SHA, not the triggering event's `GITHUB_SHA`. The parent
engine must consume these and write `data/restated/replay.json` with the identity fields,
`rules_code_sha` and selected `inputs`. Mutable inputs remain pinned to that revision; only
chosen immutable facts/reference files must retain their content at publication.

Replay runs with `--out data/restated --encode-budget-seconds 1800`. Then run
`restate_publish prepare`. It rebuilds generation Company labels from the pinned naming input,
copies config, and measures named/unnamed coverage while leaving all tech Boards counted.
After the copy and checks, prepare replaces
`metadata.files` with the exact serving path/size/SHA-256 inventory, preserving identity,
inputs, bounds and other quality fields. It atomically replaces `replay.json` before verification; placements
stay excluded. Packaging also checks that this prepared inventory still matches actual files.
Then run the independent verifier:

```
python scripts/eval/verify_restatement.py --facts data/facts --state data/state \
  --candidate data/restated --metadata data/restated/replay.json \
  --report data/restated/validation.json
```

Its `files` inventory must include exactly the candidate's serving files: tick files,
`company_directory.json`, both `config/` JSON files and, only when independently checked,
`dedup_evictions.csv`. `restated_history.allowed` selects those paths if useful; per-id
placements, replay metadata and the report itself are not serving files. Report `inputs`
must agree with replay metadata, including non-LFS `git_blob` used for content verification.

`quality.supported_metrics` declares `stock`, `new`, `watched_roles`, `turnover`; only `stock`
is mandatory for selection. Unsupported new/watched-role requests return 503; unsupported
turnover is withheld from the reading inputs. No live posting-date split is attached to replay.
Serving reports `rules_status=verified_at_code_sha`, never that a generation uses today's rules.
Company-ledger growth permits the verified prefix; scheduled current-main replay catches up.

`restate_publish package` creates `data/restated-publication/data/trends/restated/` locally.
Only explicit `--publish` writes HF. The workflow adds it only when the repository variable
`RESTATED_TRENDS_PUBLICATION_ENABLED` is exactly `true`; default publication is disabled.
Engine/verifier presence is checked before expensive fetch; no Space upload/restart is run.

The serving worker does not edit replay, baseline, placement, reference or classifier modules.

Implemented fixture coverage: valid generation selection; corrupt/missing
and partial-gate fallback; explicit legacy selection; stale-pointer refusal; CAS retries with
new unrelated dataset commits; mutated chosen-input refusal; artifact allowlist and size limits;
Space download inventory excluding facts/vectors/descriptions; app History-aware cache keys;
workflow current-rules, serialization and release-gate wiring. These tests establish contract
enforcement, not the semantic truth of a replay. Browser checks use synthetic fixtures at
1440px light and 375px dark, with reduced motion and toggle round trips. Captures are local
under `experiment/trends-restated-serving/artifacts/`, never committed.
