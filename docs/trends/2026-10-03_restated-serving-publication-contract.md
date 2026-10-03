# Restated Trends publication contract proposal

Status: proposed for coordination with the independent verifier and replay workers.
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

The app must disclose history selection and coverage in the response, including when it falls
back. Cache keys must include the selected History object. No response may claim older legacy
ticks were recomputed. The legacy archive is never inside a generation.

## Proposed JSON contract, version 1

Paths in the private index dataset:

```
data/trends/restated/current.json
data/trends/restated/generations/{generation}/manifest.json
data/trends/restated/generations/{generation}/validation.json
data/trends/restated/generations/{generation}/history/role_trend_board_deltas/*.parquet
data/trends/restated/generations/{generation}/history/company_directory.json
data/trends/restated/generations/{generation}/config/role_families.json
data/trends/restated/generations/{generation}/config/role_watchlist.json
```

Optional `history/dedup_evictions.csv` is included only if the replay/verifier establishes
its meaning for this generation. Never copy the live eviction ledger blindly into a restatement.
The Company directory must be built from the generation's Boards or explicitly checked against
them; it must not silently remove historical companies or invent counts.

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
All tick schemas and tick bounds must be validated before selecting a generation. Enforce a
documented small-artifact size cap during packaging and before downloading payloads.

Independent report fields, to agree with the parent verifier before implementing:

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

Repository rule changes during replay require checking the executable rule/config/model
fingerprint before promotion. Preserve the candidate's full rules/input fingerprint, including
its frozen Board inputs, for validation. Company-ledger growth alone is a coverage difference
and permits publication; the next scheduled run catches it. A substantive rule change requires
replay under the new rules. The fingerprint worker must expose these two distinctions without
weakening the full fingerprint bound by the independent report.

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

Parent coordination required: agree the verifier schema, current-rules fingerprint entry point,
pinned fetch/replay metadata, Company-directory construction, certified scope, and verifier CLI.
The serving worker does not edit replay, baseline, placement, reference or classifier modules.

Validation to implement after contract agreement: valid generation selection; corrupt/missing
and partial-gate fallback; explicit legacy selection; stale-pointer refusal; CAS retries with
new unrelated dataset commits; mutated chosen-input refusal; artifact allowlist and size limits;
Space download inventory excluding facts/vectors/descriptions; app History-aware cache keys;
workflow current-rules, serialization and release-gate wiring. These tests establish contract
enforcement, not the semantic truth of a replay.
