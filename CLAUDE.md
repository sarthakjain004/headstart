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
- **SuccessFactors** — M. Scrape the legacy **RMK** surface (`{host}/sitemap.xml` RSS + per-job JSON-LD); modern **CSB is DWR-RPC → skip**. India GCCs: Wipro, HCLTech, LTTS, SAP, Volvo, Schaeffler.
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

### 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
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

### 4. Goal-Driven Execution
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

### 5. Weigh Design Choices on Big Work
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

## Repo Conventions
- `scripts/` is organized by pipeline stage: `discover/` (find ATS tenants), `merge/`
  (union/dedupe lists), `validate/` (liveness), `resolve/` (company → ats:slug), `scrape/`
  (pull jobs). Whenever you add a script, put it in the folder that fits its stage — and if
  none fits, create a new clearly-named stage subfolder rather than dropping it loose in
  `scripts/`. Keep `scripts/` itself free of stray top-level scripts.
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

### Verifying experience-extraction coverage
Whenever you change `experience.py`'s patterns, gauge the effect with
`scripts/enrich/experience_coverage.py`: it runs `extract(field, description, title)` over
`data/jobs/tech/{ats}.jsonl` and prints per-ATS coverage by tier (field / regex / seniority / none).
Then `--misses <ats>` dumps a sample of the still-missed descriptions to **read manually** and reason
about what phrasing to add (that read-then-widen loop is how ADR-0018's patterns were found — don't
just eyeball the number). Calibrate any seniority→years mapping against real numbers in the data.

## Agent skills

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
