# Project Instructions

## Project Scope
HeadStart surfaces job openings read directly from company ATS boards.

- **Companies: global, not India-only.** India is a strong sub-segment and where coverage
  started, but the target is companies worldwide — don't scope discovery, scrapers, or data
  to India.
- **Roles: software-engineering / tech openings.** Only tech roles are embedded, indexed, and shown.
  Two layers were designed to optimise two different costs (ADR-0017). The **source query**, where
  an ATS cheaply supports it, would trim *scraping* volume — but it is **not currently wired on any
  scraper**, and it would only ever be a best-effort reducer, never authoritative (taxonomies are
  inconsistent and it would drop tech jobs mis-filed under odd departments). Don't read the existing
  ATS query params as that layer: Lever is fetched as a plain `?mode=json` board, and Workday's
  `jobFamilyGroup` facet is the 2,000-cap subdivision whose *union covers the full board*, so it
  reduces nothing. The **authoritative tech gate is a recall-biased post-hoc filter**
  (`headstart.tech_filter`): the scrape writes the full set to `data/jobs/{ats}.jsonl`, the filter
  keeps the tech subset in `data/jobs/tech/{ats}.jsonl`, and everything downstream (feed, embedding,
  index, UI) reads that. Post-hoc saves no scraping, but it is the only layer that is uniform across
  ATSes and recall-safe — no tech job dropped, some non-tech creep tolerated — which is exactly what
  the embedding-cost/recall goal needs. Company selection barely helps: boards are mixed.
- **Search corpus: English-only for now.** The AI semantic-search layer pre-filters non-English
  descriptions out *before* embedding — an explicit language-detection gate at ingestion
  (e.g. `langdetect` / fastText LID over `title + description`), not something the embedding
  model does on its own. An English embedding model still embeds foreign text, just badly, so
  the gate is a separate step. This scopes only the embedding/search index, not the scrape or
  the job feed — non-English boards are still scraped; they're held out of the index until
  multilingual retrieval is added. See `docs/AI_Integration/`.
- **Search interface: explicit filters + a pure semantic query (no LLM query-parser for now).**
  The user applies **structured filters** themselves (experience / `min_years`, salary, remote,
  employment_type, …) and *separately* types a **natural-language query describing only the role
  they want** — e.g. "backend engineer at a climate startup", never "3+ years" or other structured
  constraints. So the hybrid split is made explicit at the UI: filters drive the structured
  where-clause (deterministic), the query drives the embedding (semantic). The LLM query-parser
  that would *infer* filters from one free-text paragraph is **deferred** — don't build query
  understanding while the constraints come from explicit controls.

## ATS coverage: what to build next

Built providers are listed in README §"ATS coverage". What each one's scraper had to learn lives in
its module docstring, its measurement doc under `docs/{ats}/` and its ADR; its Boards live in
`data/validate/liveness/{ats}.csv`; discovery playbooks live in `docs/discovery/`
(`shared-cert-tenant-rosters.md` is the general one). **Don't keep a built provider's Board counts
in this file.** Nothing checks them here, so every ledger change can move them. This section used
to carry ✅ DONE entries with live/hiring figures for twelve providers: on 2026-09-23 three were
already wrong (the SuccessFactors entry quoted 26; its ledger held 2,214 Live rows), and one
discovery landing (#576) moved five more. Board totals belong in README and CONTEXT.md, where
`tests/test_board_counts.py` checks them.

### Landing rules the ledgers' code does not enforce

- **Decide "new" by `board_key`, and land in the ledger's own spelling.** `check_liveness.py` keys
  a ledger on the raw `tenant` string, so a Board already held under another spelling lands as a
  second row. Match candidates through each scraper's `slug_from(tenant, url)` and `board_key` (the
  identity `load_active_companies` uses), and write new rows in that ledger's majority form:
  Workday keys a Board as `{co}.wdN.myworkdayjobs.com/{site}`, Personio and Zoho as a bare label,
  Taleo BE as `ORG:CWS@host/path`. `url` is the Board's public URL, never the probe's endpoint.
- **Phenom carries only skins whose backing Board we do not already hold.** Phenom is a career-site
  skin over Workday, SuccessFactors, Taleo and others, and `index_plan.evict_duplicate` groups
  within a Board, so a skin over a Board we already scrape would serve every posting twice under
  two ATS labels. Resolve the backing Board on the tenant's **registrable domain**, not only its
  `applyUrl` — SuccessFactors rarely states one, and an `applyUrl`-only pass let four collisions
  through. Then **measure** each collision: a held row that reads 0 today is stale, and the Phenom
  Board is the live one (`careers.ucb.com`). Widen past this gate only if cross-ATS dedup is built.
- **iCIMS holds tenant hosts only.** Every live row has a hyphen in its tenant label; single-word
  `{customer}.icims.com` hosts are vendor infrastructure (`docs/icims/`) or recruiter logins
  (#576). A vanity career site on Jibe, iCIMS's own career-site layer, is not a tenant: resolve it
  to its `*.icims.com` host through the site's `/api/jobs` `apply_url`, and land that.
- **ClearCompany: re-run `scripts/validate/clearcompany_shared_accounts.py` after landing rows.**
  Every label an HRM Direct account owns serves that whole account's feed, so a new label is often
  a second name for a Board already held (131 accounts spanned 453 labels on 2026-09-23). The
  script rewrites `data/validate/aliases/clearcompany.csv`; `dedupe_boards.py` finds none of these
  and refuses `--apply` for this ATS (ADR-0182).
- **SuccessFactors holds RMK sites only.** `p_successfactors` accepts any `<urlset>`, so a corporate
  site or a Radancy career front probes `live`, and the scraper reads it as 0 jobs or as page titles
  ("Working at TUI"). Before landing a host, confirm a `/job/` page from its sitemap (urlset, RSS or
  index) carries RMK's own assets, `rmkcdn` or `j2w` — a bare "successfactors" string also appears
  on Radancy's apply links. `scripts/validate/confirm_successfactors_boards.py`'s URL-shape test is
  not enough on its own: it confirms Radancy fronts as `rmk` (4 of 4 tried). Method and
  measurements: `docs/discovery/2026-09-23_indeed-sweep-landing.md`. CSB-only tenants
  (Ericsson-class, DWR-RPC) remain the known gap.

### To build, by evidence

Evidence for the first three is in `docs/discovery/2026-09-23_indeed-sweep-landing.md`.

- **Jibe.** 271 employers found by the Indeed sweep list 145,555 open jobs on Jibe sites, and 99.7%
  of them sit on iCIMS tenants that serve `Disallow: /`, which the sitemap-only iCIMS scraper cannot
  read. Each Jibe site serves `/api/jobs` JSON and allows crawling at `crawl-delay: 5`. Needs a
  decision on reading a front whose backing tenant opts out.
- **The unsupported ATSes the Indeed sweep resolved most companies to**, most first:
  Hireology, Recruiterflow, Avature. (Breezy led that count; it, ClearCompany, Pinpoint and
  Cornerstone are now built, #579, #582, #580 and #584, and the sweep's companies on all four are
  landed. ADP Workforce Now is built too, #585, ADR-0180; the sweep's ADP companies are a landing
  still to do.)
- **ADP Recruiting Management** (`myjobs.adp.com/{slug}`, `recruiting.adp.com`) — a different
  platform from Workforce Now: its listing
  (`my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters`)
  wants an `orgoid` header, which `/public/staffing/v1/career-site/{slug}` supplies, and a
  posting-channel id not yet found (`docs/adp/2026-09-23_careercenter-measurement.md`).
- **SenseHQ** — the scraper is registered but has no ledger and no liveness probe, so none of its
  Boards can land.
- **TurboHire** — token flow: `/api/token/noauth` (needs Referer), then `POST
  /api/careerpagev2/filteredjobs?orgId={GUID}` (verified live 2026-07-21; Cleartrip, Flipkart, Ola).
- **PeopleStrong** (Angular SPA XHR) and **Jobsoid** (`{slug}.jobsoid.com/api/v1/jobs`, low yield)
  — opportunistic.
- Single-company unlocks, a manual slug each rather than a scraper: Skillate
  (`{slug}.skillate.com` — Zetwerk, Ola, Pristyn Care), Kula (`careers.kula.ai/{slug}` —
  Rocketlane), CareerSiteManager (`{slug}.careersitemanager.com` — Ecom Express), and Recruitee's
  white label `ainterviews.com`/`recruiteecdn` (Lenskart, `hiring.lenskart.com`).
- Verified **dead ends** (do not build): greythr, qandle and beehive (login-only HRMS), HirePro,
  iSmartRecruit, Recruit CRM/Ceipal. A dead-end verdict carries the scope it was measured in:
  Oracle and Taleo were dead ends only for India, and both are built
  ([ADR-0144](docs/adr/0144-oracle-taleo-was-a-dead-end-only-for-india.md)).

## Tactical Rules

### 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly.
- If uncertain, ask. If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

**Always ask clarifying questions, and keep asking.** This is not a one-time gate you pass before
implementing — it applies at every stage, however far into the work you are. Each time the work
reveals a fork you can't settle from the request or the code, ask then and there; don't bank
questions for the end, and don't go quiet just because you already asked once. A question late is
far cheaper than a wrong deliverable. Where a genuine choice exists, put the options in front of me
rather than picking silently — even when one looks obvious to you.

### 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Name Modules Deliberately
A module's name is the first thing anyone reads. Get it right when you create the file, and
re-check it whenever you change what the file does.

When adding a module:
- The name must say what the module *is*, in the repo's own vocabulary (`CONTEXT.md`). If no honest
  name comes, the design is murky — fix that first.
- Check it against its neighbours before committing. Two failure modes to avoid: **near-synonyms
  for different things** (`join_shards` vs `merge_shards` — one was scrape, one was embed, and
  neither name said so) and **near-homographs** (`index_sync` vs `sync_index`, `embed_plan` vs
  `embed_prep`) that are hard to grep and easy to misread in a traceback.
- Where a set of modules share a shape, name them so the shape shows. `src/headstart/ingest/` is
  `{half}_{role}` — `scrape_plan`/`scrape_run`/`scrape_join`, `embed_plan`/`embed_run`/`embed_merge`
  — so the two symmetric halves group in a directory listing (ADR-0028).
- Name the test file after the module it tests.

When modifying a module, ask whether the name still fits what it now does. If the content has
drifted from the name, rename it in that change — a stale name is a defect, not cosmetics. Say so
in the PR, and update every reference (imports, workflows, docs, ADRs).

### 4. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 5. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
- [Step] → verify: [check]
- [Step] → verify: [check]
- [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### 6. Weigh Design Choices on Big Work
For substantial or architectural work — a new abstraction, a schema change, a cross-cutting
pattern, anything where the right structure isn't obvious — don't silently pick one approach and
build it.

- Lay out the realistic options (usually 2–4), each with its concrete tradeoffs.
- Recommend the best one and say why — give a real opinion, not a neutral survey.
- Present the options to the user and let them choose before you build.
- Skip this for small or obvious changes; weighing options on trivial work is its own overkill.
- Once a non-obvious call is made, record it as a new numbered ADR in `docs/adr/` so the reasoning lasts.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Git Conventions
- **Do add a `Co-Authored-By` trailer to agent-authored commit messages.** A session-level
  instruction mandates it. Until 2026-09-09 this file forbade it, and the contradiction split
  identical PRs (#390 carried it, #391 did not). Don't re-tighten it from memory.
- Do NOT add "Generated with Claude Code" (or any similar attribution line) to PR descriptions.
- Keep commit messages to a maximum of 50 words.
- **Run the `code-review` skill on every code-changing PR before it merges** (the two-axis
  Standards/Spec review, fixed point = the merge-base). Apply or explicitly defer its findings,
  and re-verify (tests + lint) before merging. Docs-only PRs are exempt. This is the review that
  caught a guard justified by a false premise on #77 and a glossary collision on #78 — it earns
  its run time.
- **Verify against the live API whenever a review claim rests on how a real endpoint behaves.**
  Reading the code cannot tell you what a host actually returns, and a plausible-sounding guard
  built on an assumed response is worse than none. Hit the endpoint — a handful of real hosts,
  drawn from both sides of whatever the change keys on (live *and* dead, empty *and* full) — and
  let the measurement decide. Report the sample size with the finding; a 12-host probe is
  evidence, not proof. This rule exists because a "probe the host root to tell dead from empty"
  guard on #160 looked obviously correct and died on contact: 9 of 12 boards the ledger already
  calls dead answer `GET /` with 200. The same discipline applies to any claim about rate limits,
  pagination, or response shape — measure it, don't reason about it.
- **An agent working this repo can reach the open internet directly — use that, don't assume it
  away.** Verified 2026-08-19: direct fetches from a Claude Code session against arbitrary ATS
  hosts (Workday, Eightfold, SuccessFactors, Lever) all reached real content, not a network-egress
  block. Default to hitting the real endpoint yourself rather than reasoning from logs, docs, or a
  prior session's evidence. If a specific host genuinely looks blocked, rate-limited, or gives a
  response that doesn't smell like the real thing, don't guess past it — the pipeline's
  `workflow_dispatch` (manual trigger) can get a real answer from inside Actions instead. But treat
  that as a deliberate, asked-for decision, not a default fallback: a full run commits new LanceDB
  and state data to HF, and HF storage is the binding cost (the 100 GB quota filled on 2026-09-18,
  ADR-0168).

## Repo Conventions
- **The back-to-back ingest run lives in `src/headstart/ingest/` — not in `scripts/`** (ADR-0028).
  One module per stage step, run as `python -m headstart.ingest.<module>`: `scrape_plan`,
  `scrape_run`, `scrape_join`, `filter_tech`, `update_descriptions` (the ADR-0050 description
  store, after the tech filter and before `embed_plan`), `update_ledgers` (four subcommands, invoked in
  this order: `priority`, `cost`, `failures`, `gap`), `embed_plan`, `embed_run`, `embed_merge`, `update_meta` (the ADR-0061
  metadata refresh, after the merge and before `sync`), `index` (`sync` then `prune --apply`),
  `role_trends` (the ADR-0040 trends ledger, after prune), `hot_boards` (the actively-hiring
  ranking the "Hiring now" tab serves, strictly after `role_trends` because it reads that
  stage's Board-count snapshot and delta ledger). `index compact` is a subcommand of the
  same module but is **not** part of this run — it moved to the `cleanup-index` workflow, because
  rewriting the whole table once per run is what the storage budget cannot afford.
  Five more entry points are not stages. `state_fetch` (ADR-0030) pulls each stage's slice of HF
  state in `scrape-plan`, `join` and `merge`, or aborts. `state_witness` (ADR-0095) publishes which
  state directories exist, so an empty fetch can be told apart from a first run. `state_guard`
  (ADR-0129) refuses a write to HF state that another workflow changed since it was read — `merge`
  and `cleanup-index` both record and verify through it. Another publishes inside a stage:
  `index_publish` commits the LanceDB table and its ADR-0083 grace set in a single HF commit in
  `merge`'s upload step, so the two can never disagree. And one runs at the
  end of `merge` without being a stage either: `reclaim_storage` (ADR-0168) deletes the orphaned
  LFS blobs and verifies the quota actually fell — squashing history only makes them eligible for
  HF's collection, which is how the 100 GB quota filled on 2026-09-18.
  If you change what the pipeline runs, change it there and update `.github/workflows/pipeline.yml`
  to match. Don't add a pipeline stage to `scripts/`. Helper modules used *only* by the pipeline
  live there too (`binpack`, `board_failures`, `board_freshness`, `board_operator`,
  `derived_meta`, `doc_prep`, `index_plan`, `observability`, `role_assignments`, `shard_plan`,
  `shard_speedup`, `trends_epochs`). Logic the curated-feed path (`python -m headstart` →
  `headstart.harvest`) also reaches stays in `headstart` proper (`harvest`, `board_cost`,
  `board_priority`, `corpus`), so the feed never imports from `ingest`.
- `scripts/` is for everything *outside* that run — R&D, discovery, and one-off ops tooling —
  organized by stage: `discover/` (find ATS tenants), `merge/` (union/dedupe lists), `validate/`
  (liveness), `resolve/` (company → ats:slug), `scrape/` (one-off/local pulls), `fetch/` (pull HF
  data down — distinct from `scrape/`, which pulls from ATS hosts), `eval/`, `enrich/`,
  `filter/` (verification), `embed/` (local index tools), `bench/` (performance
  measurement), `runlog/` (pipeline run-log analysers), `state/` (one-off HF state migrations),
  `alerts/` (alert dry runs), `ui/`. Whenever you add a script, put it
  in the folder that fits its stage — and if none fits, create a new clearly-named stage subfolder
  rather than dropping it loose in `scripts/`. Keep `scripts/` itself free of stray top-level scripts.
- **The README documents the served-table schema — keep it in lockstep with `_schema()`.** README
  §"The served table" carries every column of the LanceDB `jobs` table (type + what it means) plus
  two worked example rows. If you change `_schema()` in `src/headstart/ingest/index.py` — adding,
  removing, renaming, or retyping a column — update that section **in the same change**, examples
  included. A stale schema is worse than no schema, because it gets trusted.
  `tests/test_readme_schema.py` enforces it by parsing the README table and comparing it, in order,
  against `_schema()`. The `[dev]` extra includes the index runtime and CI checks those imports
  before pytest, so the schema checks run in quality CI. Run them locally with `[dev]` before
  opening a schema PR. When you touch that section, re-check the example rows against
  real data rather than editing them from memory. The Space's `/search` needs a signed-in session
  (it answers `{"error":"sign in first"}` without one, 2026-09-23), so read the served table itself
  (`scripts/fetch/pull_lancedb.py`); `data/jobs/tech/*.jsonl` has the fields the API projection
  omits.
- **"How many Boards do we have" has five defensible answers — use the names, not a number.**
  CONTEXT.md §Counting Boards binds each to exactly one figure: **Ledger row** (a CSV line),
  **Live row** (still a row — 6,632 are duplicate spellings), **Unique Board** (deduped),
  **Scrapable Board** (what a run may pick — the right default), **Hiring Board** (`min_jobs=1`),
  plus **Slice**/**Head**/**Tail** for one run and **Scraped**/**Scored Board** for history. The
  phrase "live boards" names no single number and should not be written. Quoting the wrong one has
  already misled three separate discussions in one session; the widest pair differs by 3.3x.
  `tests/test_board_counts.py` keeps the README funnel and the glossary honest by recomputing from
  the committed ledger — it needs no heavy deps, so unlike `test_readme_schema.py` it really runs
  in CI. It deliberately cannot check **Scraped Board** or **Scored Board**: those live only on HF
  and move every run, so they carry a measured-on date instead.
- **Every LLM API call in this project goes through the llm-router — never a provider SDK pointed
  at a provider.** The router is a LiteLLM deployment on the Oracle box; callers use an
  OpenAI-compatible client against its `/v1` endpoint with `LITELLM_MASTER_KEY` as the `api_key`,
  and ask for a router-exposed model name (e.g. `agent-default`) rather than a vendor model id. One
  place chooses the model, holds the provider keys, and carries the cost — so swapping providers is
  a router config change, not a code change across callers. **The endpoint is deliberately not
  public** (binds `127.0.0.1:4000`; only port 22 is open), so anything off-box reaches it through
  the SSH tunnel or Tailscale — full recipes, including the HF Spaces one, in `docs/LLM_API.md`
  (deliberately untracked: it names private infrastructure and this repo is public).
  A remote caller that gates its own startup on the tunnel must degrade rather than die: bring the
  app up regardless and fail that one endpoint, so a router outage never takes down the product.
- Output must stream incrementally — never buffer until the program ends. Print per-item as
  work completes and flush (Python: `print(..., flush=True)` / `-u`; write results to a file
  progressively). A long batch that prints only at the end is forbidden: one slow item stalls
  all visibility, and a crash loses everything. Process loops with `as_completed`, not a
  blocking `map`, so a single slow item can't hold up the rest.
- Always write the results of any work to a proper, intuitive folder with an intuitive,
  self-describing name — never dump loose into a catch-all or a generic name. Match the kind
  of output to its home: pipeline data under `data/` (job output under `data/jobs/`),
  experiment/R&D captures (screenshots, HTML dumps, recon JSON) under `experiment/<topic>/`
  with a `LOG.md` and the captures in an `artifacts/` subdir — kept local and **not committed**
  (gitignored; decided 2026-09-23, since committed experiments are noise in the repo) — and prose
  analysis under `docs/`, which must stand alone without them. Name files so the date/source/meaning is obvious at a glance (e.g.
  `2026-06-21_datadome-slider_warp.png`), not `out.json` or `test2.html`. If no existing
  folder fits, create a clearly-named one rather than misfiling.
- **When a task needs data and the freshness isn't specified, use the freshest data available.**
  Never reason from whatever snapshot happens to sit in the working tree because it is already
  there — check how old it is, and refresh it first. A local `data/` directory carries no
  guarantee of currency: it is frozen at whenever *this machine* last pulled or last ran the
  stage that wrote it, which for ephemeral stage output (`data/jobs/`) may be months ago and for
  HF-backed data (`data/lancedb/`, `data/state/`, `data/descriptions/`, `data/embeddings/`) is
  whenever someone last ran a `snapshot_download`. This rule exists because an experience-coverage
  analysis was run against a `data/lancedb/` snapshot holding 32,179 rows when the live served
  table had 287,144 — a 9x understatement that silently changed every conclusion drawn from it.
  If the freshest copy is genuinely too expensive to fetch, say so and label the number stale in
  the same sentence you report it; don't quietly present a stale figure as current.
- **The HF dataset is the source of truth for pipeline data — never trust the local copy.**
  `data/state/`, `data/embeddings/`, and `data/lancedb/` are all gitignored: they live in the
  private HF dataset `imPoseidon/headstart-index`, and whatever sits in the working tree is a
  stale snapshot from whenever it was last pulled. Before reading, reasoning about, or quoting a
  number from any of them, refresh from HF first:

  ```bash
  python -c "from huggingface_hub import snapshot_download; snapshot_download(
      'imPoseidon/headstart-index', repo_type='dataset', local_dir='.',
      allow_patterns=['data/state/*'])"          # widen the patterns as needed
  ```

  Cheap reads that answer most questions without pulling the ~3.7 GB of vectors (2026-09-23):
  `HfApi().repo_info(..., files_metadata=True)` for file sizes, `data/embeddings/jobs/manifest.json`
  for the store's `count`, `data/state/board_priority.csv` (~2 MB) for the board ledger.
  **Exception:** `data/validate/liveness/` is committed to git, so the repo is authoritative for
  it — do not look for it on HF. See `docs/agents/deployment.md`.
  **`data/jobs/` is gitignored but NOT on HF at all** (verified 2026-08-19: zero `data/jobs/*`
  entries in the dataset's file list, only `descriptions/`, `embeddings/`, `lancedb/`, `state/`
  exist there) — it's ephemeral scrape/filter-stage output, local to whichever machine or CI run
  produced it, with no durable source to refresh from. A `snapshot_download` against
  `data/jobs/*` is a silent no-op, not a stale-data warning — don't reach for it expecting fresh
  data; use `data/descriptions/` (the ADR-0050 store) or `data/state/` for anything durable.

- **Pulling `data/lancedb/` (or any multi-GB slice): use `scripts/fetch/pull_lancedb.py`, not
  `snapshot_download`** (ADR-0085). Measured 2026-08-25 on a 1,888 MB / 4,222-file pull,
  `huggingface_hub` failed four separate ways and cost most of a session — silent Xet death, an
  internal read-timeout loop that never raised, a 1 GB file that restarted from byte zero on
  every drop instead of resuming, and finally a wedge with zero sockets open while a plain
  `requests.get` of the same file returned 200 in 0.28s. ADR-0085 has the detail; three things
  matter at the call site:

  - It is **cancellable** — Ctrl-C and re-run costs only the bytes not yet landed.
  - **When it is slow, add flows (`--workers`), never timeout.** One long-lived stream measured
    0.16 MB/s against 2.17 MB/s aggregated across four concurrent ranged GETs; raising
    `HF_HUB_DOWNLOAD_TIMEOUT` to 300s made the hangs *longer*, not rarer.
  - `--check` reports what is missing without fetching, and `snapshot_download` is still right
    for the small slices above (`data/state/*` is ~40 MB).

### Adding or changing a scraper: run the filter harness first

Build a new ATS with the `add-ats-scraper` skill; it carries this step and the rest of the
procedure. **Before any new ATS scraper's jobs ship — in the same PR that adds the scraper — run
the `verify-search-filters` skill.** A new ATS is invisible to the harness until someone teaches it:
its job-URL shape must be declared as the scraper class's `url_shape` (derived from the scraper's
`url=` construction and verified against the ATS's real routing, not assumed —
`verify_filters.py`'s `URL_SHAPES` is built from it, ADR-0157), and
the harness must run clean, including its coverage gate (`atses_without_shape` empty). This rule
exists because eightfold, freshteam and successfactors all shipped serving jobs no check ever
looked at, and the gap surfaced as a user-visible bad result rather than a red run.

### Checking a liveness ledger for duplicate boards

**Before trusting a liveness ledger's row/board count, or shipping a change that reads it as
one row per board, check for duplicates — a `live` ledger routinely holds more than one row for
the same underlying board.** This has shown up as three genuinely different mechanisms, each
found independently, each costing real critical-path time or serving stale data before it was
caught:

- **Cross-hostname redirects** (#212, #218) — a vanity/legacy hostname 301s to the canonical
  one; both marked `live` as if independent tenants. Diagnostic: fetch `/` and `/sitemap.xml`,
  follow redirects manually, check whether the target is another `live` row in the same ledger.
- **Tenant-key format proliferation** (#220) — the same board recorded under more than one slug
  spelling (bare tenant vs full URL vs partial path), from discovery/resolve emitting more than
  one variant over time. Diagnostic: group by `board_key()` (ADR-0023's canonical identity), not
  by the raw ledger row.
- **Stale casing duplicates** (found fixing #202/PR #226) — a prober-side casing-normalization
  change left the old-cased row behind instead of replacing it; 1,843 pairs in one ledger, one
  root cause. `_dedupe_boards`'s lexicographic tie-break (`config.py`) usually papers over this
  silently, but picks the **older** row whenever old and new disagree in ASCII order — which
  matters when the two rows also disagree on *verdict*, not just casing: two boards stayed in
  the active scrape list after the newer probe had already found them `dead`, because the stale
  `live` row kept winning the tie-break. Diagnostic: for a ledger with real duplicate rows, check
  whether the tie-break's survivor is the newest-verified data, not just count how many boards
  survive dedup.

None of these are caught by `_dedupe_boards()`/`_drop_parked` alone — that mechanism assumes
duplicate rows differ only cosmetically (casing, URL form) and always agree on which board they
name and whether it's live. A raw `wc -l` or per-row count on a liveness CSV overstates board
count by however many duplicates exist; go through `load_active_companies()` (or an equivalent
`board_key()`-grouped count) for anything that needs to be accurate, not the CSV directly.

### Reading eviction, flapping, or "we deleted a live job" data

**An id absent from one scrape is NOT evicted by `sync`.** Since ADR-0083 (live 2026-08-23), a
missing id is recorded as **Unconfirmed** (`data/state/unconfirmed_ids.txt`) and evicted only if
the *next* scrape of that Board misses it too. Measured across the 10 runs
`32671773723`→`32719831948` (2026-08-24, from their merge logs' own `grace period:` lines):
425–1,176 ids unconfirmed per run against 129–605 evicted, so this is doing real work, not a
no-op. **One live exception:** `prune` is a different path with no grace period at all — it
evicts off-Board and duplicate rows outright (ADR-0023), so "one absence never deletes anything"
is true of `sync` specifically, not of the merge stage as a whole.

This changes what the numbers *mean*, not just their size. A "flapped" row was missed by **two
consecutive scrapes** and then seen again — a far stronger signal than one unlucky crawl — and
any claim of the form "a single transient miss deletes a live job" has been false since
2026-08-23. Before writing anything about evictions, confirm the runs you read post-date it
(`git merge-base --is-ancestor ee97ebc <run-sha>`).

Two mechanisms withhold evictions, are reported separately on purpose, and must not
be conflated — CONTEXT.md's **Eviction** and **Unconfirmed** glossary entries are authoritative:
- **Unconfirmed** (ADR-0083) — per-*Job*; one absence isn't enough. The unit is *scrapes of that
  Board*, never runs: only ~20k are in any run's slice, under a quarter of the Scrapable Boards
  (CONTEXT.md §Counting Boards — "live Boards" names no single number), and a Board the run
  did not read is no evidence, so its ids keep the state they had.
- **scope-excluded** (ADR-0053, narrowed by ADR-0121) — the Board's scrape was not authoritative,
  so it leaves the eviction scope entirely that run. Since ADR-0121 a *measured* shortfall at or
  above 99% of the Board's own stated total no longer scope-excludes it — those ids go to the
  per-Job grace period instead — so this now covers hard caps, unmeasurable shortfalls and losses
  past the tolerance. For everything it still covers it has **no bound and no drain**: a Board
  that is short on every run never re-enters scope, and its closed postings are served indefinitely
  (measured: 105 dead rows on `careers.qualcomm.com`, oldest 22 days —
  `docs/eightfold/no-client-side-fix-for-replica-instability.md`). It reports only a Board count,
  never a row count, so the accretion is invisible unless you go looking for it.

### Verifying experience-extraction coverage
Whenever you change `experience.py`'s patterns, gauge the effect with
`scripts/enrich/experience_coverage.py`: it runs `extract(field, description, title)` over
`data/jobs/tech/{ats}.jsonl` and prints per-ATS coverage by tier (field / regex / seniority / none).
Then `--misses <ats>` dumps a sample of the still-missed descriptions to **read manually** and reason
about what phrasing to add (that read-then-widen loop is how ADR-0018's patterns were found — don't
just eyeball the number). Calibrate any seniority→years mapping against real numbers in the data.

**That script's data source is stale by construction** — `data/jobs/tech/` is ephemeral stage output
that never reaches HF (see the freshest-data rule above), so it reports on whatever this machine last
scraped and is missing whole ATSes. It remains the right quick gauge, but measure a coverage *claim*
against the served LanceDB table joined to `data/descriptions/`, both of which have a durable source.

**And measure changed values, not only coverage.** A pattern change can silently move an answer
Tier 2 already produced, and a coverage total hides that completely: a review caught `_RANGE_TAIL`
turning "GET THE JOB DONE - 5+ years" into 1-5 (off the "one" in "DONE") while coverage went *up*.
Bucket every record by old-tier → new-tier **and** report same-tier value changes (ADR-0066).

### `DERIVATIONS_VERSION`: when a fix to a derived field needs a version bump
`headstart.ingest.doc_prep.DERIVATIONS_VERSION` (ADR-0061) is a single counter shared by every
field `experience.extract()` or `salary.extract()` derives. `to_meta()` runs the cascade once, at
scrape time, on whatever code is live then — so a fix to either module reaches a **new** Job
immediately, for free. It does **not** reach an **already-indexed** Job: `update_meta`'s sweep only
re-derives a stored row when `DERIVATIONS_VERSION > stored_version` (or the row's own raw inputs
changed), so a fix that isn't accompanied by a version bump silently never reaches production data
that already existed before the fix shipped — the code is correct, but everything indexed earlier
keeps serving the old, wrong answer forever.

**Bump it, in the same change, whenever a fix to `experience.py` or `salary.py` changes what
`extract()` returns for input that's already been scraped** — not just a new pattern that only
matters for new data going forward. A currency/pattern *addition* usually needs one too, since it
can recover a value on already-indexed jobs that previously fell through to `None`. Skip it only
when the change is provably inert on anything already stored (e.g. a new ATS's own field parser,
scoped to a dispatch key no existing row uses yet).

Cite the exact commit range in the version comment (`git log <prior-bump-sha>..<this-sha> -- path`
— a fixed range, never `..HEAD`, which drifts as later commits land), and verify every fact you put
in that comment against the real diff/data before writing it down, not from memory of what a past
session found — this repo's own history has caught an unbumped fix reaching production twice
(`docs/salary-extraction/keka.md`'s "DERIVATIONS_VERSION can silently stop being bumped" finding,
then the identical gap recurring across darwinbox's and trakstar's own salary.py passes).

## Agent skills

**Invoke a skill through the Skill tool whenever one applies — never reproduce its process from
memory.** A skill's value is its exact procedure (which sub-agents it spawns, what each one is
told, how findings are aggregated); improvising "roughly what the skill does" silently drops the
parts that matter. This was learned on `code-review`: its two axes exist as two *independent
parallel sub-agents* so neither pollutes the other, and a hand-rolled single-agent imitation
quietly merged them. If a skill fits the task, invoke it and follow it as written; if it doesn't
quite fit, say so rather than approximating it.

This repo's own skills: `add-ats-scraper` (measure, build, probe, discover and ship a new ATS),
`ats-gap-search` (one agent per ATS to close its Board-discovery gap) and `verify-search-filters`
(the search-filter harness a new ATS must pass).

### Issue tracker

Issues are tracked in this repo's GitHub Issues via the `gh` CLI; external PRs are not a triage
surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use their default label names (`needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at the root + ADRs in `docs/adr/`. See `docs/agents/domain.md`.

### Deployment

The live free-tier deployment (private HF dataset `imPoseidon/headstart-index`, Space
`imPoseidon/headstart-search`, nightly Actions pipeline) — access commands, auth/token model,
and failure modes: see `docs/agents/deployment.md` before touching any of it.
