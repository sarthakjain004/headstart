# ADR-0028: The scheduled ingest run lives in `src/headstart/ingest/`

- Status: Accepted
- Date: 2026-07-25
- Cadence note (2026-07-26, #63): this ADR was written when the run was 6-hourly; it is now
  2-hourly (`.github/workflows/pipeline.yml`, `cron: 30 1-23/2`). The decision — where the
  run's code lives — is cadence-independent, so only the wording changed. Text below that
  said "6-hourly" now says "scheduled".
- Moves the entry points of [ADR-0025](0025-parallelize-nightly-pipeline.md) /
  [ADR-0026](0026-parallelize-nightly-scrape.md) / [ADR-0027](0027-measured-scrape-cost-ledger.md)
  into the package, collapses five of them into two, moves the four pipeline-only library modules
  in with them, and renames `pipeline.py` → `harvest.py`. **Pure relocation: no stage semantics,
  eviction rule, partial-harvest guarantee, or cost model changes.** Paths in earlier ADRs were
  rewritten to match, so they stay usable as current reference.

## Context

The run that `.github/workflows/pipeline.yml` executes was **12 scripts, 1,899 lines, spread across
5 of `scripts/`'s 12 subdirs** — `pipeline/`, `scrape/`, `filter/`, `rank/`, `embed/` — mixed in with
roughly 8,500 lines of R&D and one-off tooling in those same directories, with nothing marking which
is which. `scripts/embed/` held `embed_jobs.py` (in the run) beside `search_wellfound.py`,
`build_index.py`, `evict_store.py` (not). `scripts/pipeline/` was itself a misnomer: it held 4 of
the 12.

Three concrete costs, not just untidiness:

1. **The tests could not import their subject.** `scripts/` is not a package, so all 7 test files
   loaded their target through `importlib.util.spec_from_file_location` against a hardcoded relative
   path. `test_embed_jobs.py`'s docstring stated the reason outright: *"embed_jobs.py is a script
   under scripts/embed, so we load it by path."* Any move silently broke collection.
2. **Repo root was recomputed 12 times.** Every script carried its own
   `Path(__file__).resolve().parents[2]` — a depth that is invisible until it is wrong.
3. **No way to answer "what does the pipeline run?"** without reading the workflow YAML top to
   bottom and cross-referencing five directories.

## Decision

**One package, `src/headstart/ingest/`, one module per stage step, invoked as
`python -m headstart.ingest.<module>`.**

| Stage | Module | Was |
| --- | --- | --- |
| 1 | `scrape_plan` | `scripts/pipeline/plan_scrape.py` |
| 2 | `scrape_run` | `scripts/scrape/nightly_harvest.py` |
| 3 | `scrape_join` | `scripts/pipeline/join_shards.py` |
| 3 | `filter_tech` | `scripts/filter/tech.py` |
| 3 | `update_ledgers priority` / `cost` | `scripts/rank/update_board_priority.py` + `update_board_cost.py` |
| 3 | `embed_plan` | `scripts/pipeline/plan_embed.py` |
| 4 | `embed_run` | `scripts/embed/embed_jobs.py` |
| 5 | `embed_merge` | `scripts/pipeline/merge_shards.py` |
| 5 | `index sync` / `prune` / `compact` | `scripts/embed/sync_index.py` + `prune_index.py` + `compact_index.py` |

Twelve modules become nine. Two collapses, both of steps that already run back-to-back on the same
data in the same job:

- **`index`** — `sync`, `prune`, and `compact` are three consecutive stage-5 steps against the same
  LanceDB table, and `prune_index` already imported `apply_sync` from the sync module. They now share
  one `--db` flag, one `_all_ids` read, and one connection idiom. The safety abort (`prune` exits 1
  when the keep-set is under 1,000 Boards) is preserved exactly — `cleanup-index.yml` and
  `pipeline.yml` both rely on that non-zero exit.

  `--db` is declared on **each subparser**, not the top-level parser. Hoisting it reads better but
  changes the CLI: argparse only accepts a parent-parser option *before* the subcommand, so
  `index prune --apply --db X` would fail where `prune_index.py --apply --db X` worked. `sync` and
  `compact` do gain the flag (both previously hardcoded `_DB`) — the one intentional surface
  addition in this change, and the price of the three sharing a parser.
- **`update_ledgers`** — both blend this run's measurements into a `data/state/` CSV keyed by
  `{ats}:{slug}`, both leave untouched Boards alone (ADR-0022's partial-harvest rule).

`REPO_ROOT` is defined once in `ingest/__init__.py`, replacing the 12 hand-counted `parents[2]`.

### The naming scheme: `{half}_{role}`

The run is two symmetric halves, each **plan → run → gather**, but the old names hid that behind
three different shapes: `plan_scrape` / `scrape` / `join_shards` against `plan_embed` /
`embed_jobs` / `merge_shards`. Worst of all, `join_shards` and `merge_shards` were near-synonyms
for different things — one gathered scrape fragments, the other embed fragments, and neither name
said which.

Naming them `{half}_{role}` makes the symmetry structural: `scrape_plan` / `scrape_run` /
`scrape_join` beside `embed_plan` / `embed_run` / `embed_merge`. The two triples group in a
directory listing, and `join` versus `merge` is unambiguous because the prefix carries the half.
`scrape` and `embed` are already the glossary's words for the two fan-outs (CONTEXT.md), so this
borrows the ubiquitous language rather than inventing terms.

One knock-on: `embed_prep` would sit one letter from `embed_plan` — the same near-homograph trap as
`index_sync`/`sync_index`. It becomes `doc_prep`; **Doc** is a defined term in CONTEXT.md and is
exactly what it builds. Test files are renamed to mirror their modules.

Rejected: `fan`/`gather` as the role verbs (perfectly parallel, but invents two terms the glossary
would then have to define) and verb-first `plan_scrape` / `run_scrape` / `join_scrape_shards`
(reads as a command, but scatters the two halves alphabetically and lengthens the gather names).

### Which library modules moved with them

The entry points were only half the boundary. Sorting `src/headstart/*.py` by who actually imports
it splits cleanly, so the split is drawn on that evidence rather than on taste:

| Module | Imported by | Verdict |
| --- | --- | --- |
| `binpack` | `embed_plan`, `scrape_plan` | ingest-only → moved |
| `doc_prep` (was `embed_prep`) | `embed_plan`, `embed_run` | ingest-only → moved |
| `index_sync` + `index_prune` | `index` | ingest-only → moved, merged as `index_plan` |
| `board_cost` | `harvest`, `scrape_plan`, `update_ledgers` | shared → stays |
| `board_priority` | `board_cost`, 5 ingest modules | shared → stays |
| `corpus` | `board_priority`, `index_plan`, 4 ingest modules | shared → stays |

`board_cost` / `board_priority` / `corpus` are reachable from `harvest.py`, which
`python -m headstart` (the curated `docs/jobs.json` feed) and `scripts/scrape/run_scrapers.py` both
use. Moving them under `ingest/` would make non-pipeline code import from the pipeline package —
the dependency arrow pointing the wrong way. They stay.

`index_sync` and `index_prune` merge into one `index_plan` because they are one concern — plan and
apply changes to the `jobs` table, with `apply_sync` already shared by both — and because keeping
them as separate neighbours of `index.py` would have made the `index_sync` / `index sync` homograph
worse. `index_plan` deliberately imports nothing heavy (no lancedb / numpy / pyarrow), so the
scoping invariants stay testable on CI's base-deps-only install.

### `pipeline.py` → `harvest.py`

"Pipeline" named two unrelated things: the 5-stage CI run (`pipeline.yml`, `headstart.ingest`,
ADR-0025/0026/0027) and this one module, whose actual content is "run all scrapers, build the feed".
It is one step *inside* the pipeline, not the pipeline. Renamed to `harvest.py`, which was already
the repo's word for a scrape run.

### Why `update_ledgers` keeps subcommands rather than doing both in one pass

The workflow marks the cost step `continue-on-error: true` and the priority step not — a missing
cost ledger costs one run of packing balance, so it must not sink a run that already scraped and
embedded successfully. Folding both into one command would put the priority update behind a cost
failure. Two subcommands, two workflow steps, failure semantics unchanged.

## Alternatives considered

- **Leave it in `scripts/` and just document which files are in the run.** Cheapest, but fixes
  none of the three costs — the tests still path-load, the root is still recomputed, and the
  documentation immediately drifts from the workflow.
- **A single `headstart.ingest` dispatcher** (`python -m headstart.ingest plan-scrape`) with a
  `console_scripts` entry point. More cohesive as a surface, but it adds an argparse dispatch layer
  on top of the nine the stages already have, for no gain — `python -m` already addresses a module
  in a package for free.
- **Move every library module under `ingest/`**, not just the ingest-only ones. Rejected on the
  import evidence above: `board_cost` / `board_priority` / `corpus` are reachable from `harvest.py`,
  so this would have the curated-feed path importing from the pipeline package.
- **Leave the library modules alone entirely** and move only the entry points. This was the
  original scope, and it left the boundary half-drawn: `binpack`, `embed_prep`, `index_sync`, and
  `index_prune` have no consumer outside the pipeline, so keeping them in the top-level namespace
  advertises pipeline internals as shared API.
- **Keep `index_sync` and `index_prune` as separate modules** after moving them. Rejected because
  `ingest/index_sync.py` sitting next to `ingest/index.py` (whose first subcommand is `sync`) is
  worse than the homograph it replaced.

## Consequences

- `.github/workflows/pipeline.yml` (12 call sites), `pipeline-smoke.yml` (5), and
  `cleanup-index.yml` (2) invoke `python -m headstart.ingest.*`. All three already
  `pip install -e .`, which is what makes `REPO_ROOT` resolve to the checkout.
- **`pipeline-smoke.yml`'s `paths:` trigger** was watching `scripts/embed/embed_jobs.py` and
  `scripts/pipeline/**`. Left alone it would have silently stopped firing; it now watches
  `src/headstart/ingest/**`.
- All 7 test files import normally; the `importlib` dance is gone.
  `test_nightly_harvest_assignment.py` → `test_scrape_assignment.py` tracks the module rename, and
  `test_index_sync.py` + `test_prune_index.py` merge into `test_index_plan.py` to match theirs.
  The two `embed_jobs` tests keep their `importorskip` gates *above* the module import — CI's
  quality job installs base deps only, so a top-level import would error instead of skipping.
- `scripts/pipeline/` and `scripts/rank/` no longer exist. `scripts/` now means "not in the
  scheduled run", which is the distinction that was missing.
- Anyone with a local `python scripts/embed/embed_jobs.py --resume` habit needs the new command;
  the README quickstart and `docs/agents/deployment.md` are updated.
