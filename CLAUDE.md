# Project Instructions

## Project Scope
HeadStart surfaces job openings read directly from company ATS boards.

- **Companies: global, not India-only.** India is a strong sub-segment and where coverage
  started, but the target is companies worldwide — don't scope discovery, scrapers, or data
  to India.
- **Roles: software-engineering / tech openings.** Only tech roles are embedded, indexed, and shown.
  Two layers optimise two different costs (ADR-0017). The **source query**, where an ATS cheaply
  supports it (Lever `?department=`, Workday `jobFamilyGroup` facet), trims *scraping* volume — a
  best-effort reducer, never authoritative (taxonomies are inconsistent and it would drop tech jobs
  mis-filed under odd departments). The **authoritative tech gate is a recall-biased post-hoc filter**
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

## TODO: ATS providers to add support for

**Evidence-ranked from host-mining** (2026-07-21; counts = deduped India tenant *hosts* from the
Common-Crawl + Wayback feeders in `data/scratch/india_ats_hosts.txt` — much stronger signal than
per-company web research). Full research, endpoint probes, and provenance:
`experiment/ats-provider-expansion/PLAN.md`.
- **Freshteam** ✅ DONE (2026-07-21, #45) — `scrapers/freshteam.py`, wired through liveness (818
  live / 579 hiring boards in `data/validate/liveness/freshteam.csv`). Widget caps at 1000/tenant.
- **SuccessFactors** ✅ DONE (2026-07-21) — `scrapers/successfactors.py` (RMK only), wired through
  liveness (26 live boards, `data/validate/liveness/successfactors.csv`). Slug = the vanity host.
  Three listing surfaces, cheapest-first: `/sitemap.xml` urlset of `/job/{id}/` (most tenants) →
  `/search/?startrow=N` HTML pages (RSS-sitemap tenants whose search works, e.g. SAP) → the patient
  full RSS stream (Voith/Tetra Pak, whose `/search/` is CSB-rendered). Fields come from a per-job
  detail pass — JSON-LD `JobPosting` on classic pages, schema.org `<meta itemprop>` microdata +
  `joblayouttoken` label spans on CSB-rendered ones (Wipro/LTIMindtree/Cipla). **CSB-only tenants
  (Ericsson-class, DWR-RPC) remain the known gap** — their sitemap isn't RMK-shaped, so liveness
  marks them `dead` and they're skipped, never mis-scraped.

**Highest ROI first — fingerprinter gaps, ZERO new scraper** (already-supported ATSes whose board
sits on a non-derivable tenant the fingerprinter can't guess; from `fp_all.txt` mining — 79% of the
316 were opaque to no-JS curl, so these are a lower bound):
- **Darwinbox — 11 curated companies** (CarDekho, Licious, Emeritus, Pixxel, FarEye, Happiest Minds
  `smileshrms`, Vymo `vymopeopleconnect`, LEAD `myleadschool`, …). **Top ROI** — the scraper exists;
  only a careers-page/redirect tenant scan is missing. Same fix lifts **Keka** (6: VWO, Open,
  Eka.care, AccioJob, Inito), **Workday** (3: BrowserStack, Fractal, Sprinklr — non-derivable pod),
  **Greenhouse** (2: Groww on the EU pod `job-boards.eu.greenhouse.io`, HighRadius embed-only).

**New providers — endpoints VERIFIED live 2026-07-21** (full protocols: PLAN.md §4b + `artifacts/research_*.md`):
- **PyjamaHR** — S, easiest. Open REST no auth: `GET api.pyjamahr.com/api/career/jobs/?company_uuid={UUID}` (+ `/jobs/{id}/?company_uuid=` for description). Native workplace_type + experience + salary.
- **Eightfold** — M, best discoverability (`{slug}.eightfold.ai` sweep → `/careers/sitemap.xml` → JSON-LD; the `/api/apply/v2/jobs` XHR is 403-hardened). Qualcomm/NVIDIA/Micron/Vodafone GCCs, ~75-89% tech.
- **TurboHire** — M, token flow: `/api/token/noauth` (needs Referer) → `POST /api/careerpagev2/filteredjobs?orgId={GUID}`. 72 hosts; unlocks Cleartrip/Flipkart, Ola.
- **Zwayam / Naukri Talent Cloud** — M. Two-call flow to `public.zwayam.com` (config→base64 companyId→ES `/jobs/search`); native experience years, description needs a detail pass. **The mined `.zwayam.com` hosts are DEAD** — real boards are on custom domains (`careers.persistent.com`, `careers.coforge.com`, `jobs.itcinfotech.com`); discovery is the cost.
- **Phenom** — M, but poor discoverability (no enumerable pattern, curated seed needed). Mastercard/Adobe India GCCs. After Eightfold.
- **PeopleStrong** (201 hosts, still no scraper — Angular SPA XHR), **Jobsoid** (`{slug}.jobsoid.com/api/v1/jobs`, S, low yield) — opportunistic.
- Verified **dead-ends** (do not build): **Oracle Taleo** (declining, ~1 live India tenant — GCCs migrated to Oracle Cloud HCM which we support), greythr/qandle/beehive (login-only HRMS), HirePro, iSmartRecruit, Recruit CRM/Ceipal. **iCIMS** = opportunistic-only (alive but HTML/JSP-only, non-enumerable, India tenants are GCC boards not IT majors).

Single-company unlocks (web research; a manual slug, not worth a scraper each):
- **Trakstar Hire** (`{slug}.hire.trakstar.com`) — ShareChat, MediBuddy, Exotel, Drip Capital (4).
- **Skillate** (`{slug}.skillate.com`) — Zetwerk, Ola, Pristyn Care (3).
- **SenseHQ** (`{slug}.sensehq.com/careers`) — Zetwerk, Capillary (2).
- **Param.ai** (`{slug}.app.param.ai/jobs/`) — Practo.
- **Kula** (`careers.kula.ai/{slug}`) — Rocketlane.
- **Oracle Cloud HCM** (`{tenant}.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/...`) — Icertis.
- **CareerSiteManager** (`{slug}.careersitemanager.com`) — Ecom Express.
- **ainterviews.com / recruiteecdn** (Recruitee white-label) — Lenskart (`hiring.lenskart.com`).

NB: **Workable** and **Recruitee** are now in the slug-probe (`ATS_PROBES`).

Also a known miss *class* (not an ATS gap): **non-derivable slugs** — the board is on a clean ATS
but the slug is a parent/legal/brand variant the name→slug derivation can't guess: Dream11 →
`lever:dreamsports`, Zomato → `smartrecruiters:Zomato1`, Razorpay →
`greenhouse:razorpaysoftwareprivatelimited`. These need the careers-page embed scan (or a manual
slug), not slug derivation.

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
- Do NOT add a `Co-Authored-By` trailer to commit messages.
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
  that as a deliberate, asked-for decision, not a default fallback: a full run rewrites ~1.86 GB of
  LFS data, and storage is this workflow's own documented binding cost constraint.

## Repo Conventions
- **The 2-hourly ingest run lives in `src/headstart/ingest/` — not in `scripts/`** (ADR-0028).
  One module per stage step, run as `python -m headstart.ingest.<module>`: `scrape_plan`,
  `scrape_run`, `scrape_join`, `filter_tech`, `update_descriptions` (the ADR-0050 description
  store, after the tech filter and before `embed_plan`), `update_ledgers` (`priority`/`cost`/`gap`/
  `failures` subcommands), `embed_plan`, `embed_run`, `embed_merge`, `update_meta` (the ADR-0061
  metadata refresh, after the merge and before `sync`), `index` (`sync` then `prune --apply`),
  `role_trends` (the ADR-0040 trends ledger, after prune). `index compact` is a subcommand of the
  same module but is **not** part of this run — it moved to the `cleanup-index` workflow, because
  rewriting the whole table every two hours is what the storage budget cannot afford.
  One more entry point is not a stage but opens three of them: `state_fetch` (ADR-0030) pulls each
  stage's slice of HF state in `scrape-plan`, `join` and `merge`, or aborts.
  If you change what the pipeline runs, change it there and update `.github/workflows/pipeline.yml`
  to match. Don't add a pipeline stage to `scripts/`. Helper modules used *only* by the pipeline
  live there too (`binpack`, `doc_prep`, `index_plan`, `observability`, `shard_speedup`); logic
  the curated-feed path (`python -m headstart` → `headstart.harvest`) also reaches stays in
  `headstart` proper
  (`harvest`, `board_cost`, `board_priority`, `corpus`) so the feed never imports from `ingest`.
- `scripts/` is for everything *outside* that run — R&D, discovery, and one-off ops tooling —
  organized by stage: `discover/` (find ATS tenants), `merge/` (union/dedupe lists), `validate/`
  (liveness), `resolve/` (company → ats:slug), `scrape/` (one-off/local pulls), `eval/`, `enrich/`,
  `filter/` (verification), `embed/` (local index tools), `bench/` (performance
  measurement), `ui/`. Whenever you add a script, put it
  in the folder that fits its stage — and if none fits, create a new clearly-named stage subfolder
  rather than dropping it loose in `scripts/`. Keep `scripts/` itself free of stray top-level scripts.
- **The README documents the served-table schema — keep it in lockstep with `_schema()`.** README
  §"The served table" carries every column of the LanceDB `jobs` table (type + what it means) plus
  two worked example rows. If you change `_schema()` in `src/headstart/ingest/index.py` — adding,
  removing, renaming, or retyping a column — update that section **in the same change**, examples
  included. A stale schema is worse than no schema, because it gets trusted.
  `tests/test_readme_schema.py` enforces it by parsing the README table and comparing it, in order,
  against `_schema()`. Note it `importorskip`s pyarrow, so it **skips in CI** — run the suite
  locally (with the `[embed]` extra installed) before opening any schema PR, and don't read a green
  CI as proof the docs are current. When you touch that section, re-check the example rows against
  real data rather than editing them from memory: `curl "https://imposeidon-headstart-search.hf.space/search?q=backend+engineer&k=2"`
  returns live rows, and `data/jobs/tech/*.jsonl` has the fields the API projection omits.
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
  **Known exception to migrate:** `scripts/eval/judge_pool.py:93` still constructs `Anthropic()`
  against the default base URL — pre-existing, predates this rule.
- Output must stream incrementally — never buffer until the program ends. Print per-item as
  work completes and flush (Python: `print(..., flush=True)` / `-u`; write results to a file
  progressively). A long batch that prints only at the end is forbidden: one slow item stalls
  all visibility, and a crash loses everything. Process loops with `as_completed`, not a
  blocking `map`, so a single slow item can't hold up the rest.
- Always write the results of any work to a proper, intuitive folder with an intuitive,
  self-describing name — never dump loose into a catch-all or a generic name. Match the kind
  of output to its home: pipeline data under `data/` (job output under `data/jobs/`),
  experiment/R&D captures (screenshots, HTML dumps, recon JSON) under `experiment/<topic>/`
  with a tracked `LOG.md` and the captures in an `artifacts/` subdir, prose analysis under
  `docs/`. Name files so the date/source/meaning is obvious at a glance (e.g.
  `2026-06-21_datadome-slider_warp.png`), not `out.json` or `test2.html`. If no existing
  folder fits, create a clearly-named one rather than misfiling.
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

  Cheap reads that answer most questions without pulling the ~1.5 GB of vectors: `HfApi()
  .repo_info(..., files_metadata=True)` for file sizes, `data/embeddings/jobs/manifest.json`
  for the store's `count`, `data/state/board_priority.csv` (~1 MB) for the board ledger.
  **Exception:** `data/validate/liveness/` is committed to git, so the repo is authoritative for
  it — do not look for it on HF. See `docs/agents/deployment.md`.
  **`data/jobs/` is gitignored but NOT on HF at all** (verified 2026-08-19: zero `data/jobs/*`
  entries in the dataset's file list, only `descriptions/`, `embeddings/`, `lancedb/`, `state/`
  exist there) — it's ephemeral scrape/filter-stage output, local to whichever machine or CI run
  produced it, with no durable source to refresh from. A `snapshot_download` against
  `data/jobs/*` is a silent no-op, not a stale-data warning — don't reach for it expecting fresh
  data; use `data/descriptions/` (the ADR-0050 store) or `data/state/` for anything durable.

### Adding or changing a scraper: run the filter harness first

**Before any new ATS scraper's jobs ship — in the same PR that adds the scraper — run the
`verify-search-filters` skill.** A new ATS is invisible to the harness until someone teaches it:
its job-URL shape must be added to `scripts/eval/verify_filters.py`'s `URL_SHAPES` (derived from
the scraper's `url=` construction and verified against the ATS's real routing, not assumed), and
the harness must run clean, including its coverage gate (`atses_without_shape` empty). This rule
exists because eightfold, freshteam and successfactors all shipped serving jobs no check ever
looked at, and the gap surfaced as a user-visible bad result rather than a red run.

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

## Agent skills

**Invoke a skill through the Skill tool whenever one applies — never reproduce its process from
memory.** A skill's value is its exact procedure (which sub-agents it spawns, what each one is
told, how findings are aggregated); improvising "roughly what the skill does" silently drops the
parts that matter. This was learned on `code-review`: its two axes exist as two *independent
parallel sub-agents* so neither pollutes the other, and a hand-rolled single-agent imitation
quietly merged them. If a skill fits the task, invoke it and follow it as written; if it doesn't
quite fit, say so rather than approximating it.

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
