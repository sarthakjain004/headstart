# HeadStart

Find software-engineering openings straight from companies' ATS (Applicant Tracking
System) career boards — earlier and more completely than relying on LinkedIn.

HeadStart discovers which companies host boards on which ATS, validates those boards, scrapes
them through **21 per-ATS scrapers**, normalizes everything into one `Job` shape, and serves it
two ways: a static dashboard over a curated feed, and an **AI semantic-search layer** (local
embeddings + vector search with structured filters) running live on a free-tier Hugging Face
Space over a **280,571-row** index of the tech corpus.

- **Design decisions:** [`docs/adr/`](./docs/adr/) — 45 numbered ADRs (the option picked, the
  ones rejected, and why).
- **Domain glossary:** [`CONTEXT.md`](./CONTEXT.md) — the ubiquitous language (ATS, Board, Slug,
  Job, Discovery, Liveness, Feed, Doc, Bucket, GitHub VM…).
- **AI layer design + results:** [`docs/AI_Integration/`](./docs/AI_Integration/).
- **Deployment runbook:** [`docs/agents/deployment.md`](./docs/agents/deployment.md).
- **Dashboard:** GitHub Pages serves [`docs/`](./docs/) → `https://sarthakjain004.github.io/headstart/`.

## Why

LinkedIn is not a comprehensive mirror of the job market. Employers have to *opt in* to push
roles there (via ATS integrations / "job wrapping"), and that path is gated and often skipped.
So two kinds of roles slip through: ones LinkedIn never gets because the employer never
syndicated them, and ones it gets late or buries below paid listings. Reading the ATS directly
catches both. The target is companies **worldwide**, focused on **software-engineering / tech
roles**; the long tail of smaller employers (India among them) is just where the LinkedIn gap is
widest.

## How it works

Two halves share the `Job` model. A discovery pipeline finds and validates boards; a scheduled
ingest pipeline reads them and keeps the search index fresh.

```mermaid
flowchart TB
    subgraph D["① Discovery &nbsp;·&nbsp; occasional, by hand"]
        direction LR
        D1["<b>discover</b><br/>Common Crawl · Wayback<br/>careers-page fingerprint"]
        D2["<b>merge</b><br/>union + dedupe per ATS"]
        D3["<b>validate</b><br/>liveness-probe each board"]
        D4[("<b>liveness ledger</b><br/>93,688 live of 148,553<br/>git-tracked, authoritative")]
        D1 --> D2 --> D3 --> D4
    end

    subgraph P["② Ingest &nbsp;·&nbsp; GitHub Actions, every 2h &nbsp;·&nbsp; ADR-0025 / ADR-0026"]
        direction LR
        P1["<b>scrape-plan</b><br/>1 VM · 10m<br/>pick 20k boards, LPT pack"]
        P2["<b>scrape</b><br/>≤15 VMs · 60m budget<br/>20 enabled scrapers → fragments"]
        P3["<b>join</b><br/>1 VM · 40m<br/>union · tech-filter<br/>priority · cost · plan embed"]
        P4["<b>embed</b><br/>≤15 VMs · 180m budget<br/>nomic on CPU → fragments"]
        P5["<b>merge</b><br/>1 VM · 48m · single writer<br/>concat · sync · prune · compact · trends"]
        P1 --> P2 --> P3 --> P4 --> P5
    end

    subgraph F["③ Curated feed &nbsp;·&nbsp; python -m headstart"]
        direction LR
        F1["<b>scrape + tech-filter</b>"]
        F2[("<b>docs/jobs.json</b>")]
        F1 --> F2
    end

    subgraph S["④ Serving"]
        direction LR
        S1[("<b>HF dataset</b><br/>headstart-index<br/>vectors · LanceDB · ledger")]
        S2["<b>HF Space</b><br/>headstart-search<br/>filter-then-rank"]
        S3["<b>GitHub Pages</b><br/>static dashboard"]
        S4["<b>Telegram bot</b><br/>every 15m · enrolment only"]
        S1 --> S2
    end

    D4 ==> P1
    S1 -. "state + prior meta" .-> P1
    S1 -. "prior store + lancedb" .-> P5
    D4 -.-> F1
    P5 ==>|"upload + restart"| S1
    F2 --> S3
    F2 --> S4
    P2 -. "partial fragments still flow" .-> P3
    P4 -. "partial fragments still flow" .-> P5

    classDef serial fill:#1b3a57,stroke:#5aa9e6,stroke-width:2px,color:#eaf4fc
    classDef fan fill:#14453a,stroke:#3fbf8f,stroke-width:3px,color:#e4f7f0
    classDef store fill:#42295e,stroke:#b184dd,stroke-width:2px,color:#f4ecfc
    classDef serve fill:#5a3418,stroke:#e09a4f,stroke-width:2px,color:#fbf1e6
    class D1,D2,D3,P1,P3,P5,F1 serial
    class P2,P4 fan
    class D4,F2,S1 store
    class S2,S3,S4 serve
```

Green stages are matrix fan-outs across many **GitHub VMs**; blue are single-VM serial stages;
purple are stored state. Thick `==>` edges are the main path. Dotted edges are the two things
that are easy to miss: state each stage *reads* back from the HF dataset, and the partial-work
guarantee — a shard that hits its budget still forwards what it finished.

**Discovery** runs occasionally and by hand; its output, the liveness ledger under
`data/validate/liveness/`, is committed to git and is what the ingest pipeline reads.

**Ingest** (`.github/workflows/pipeline.yml`) runs on a 2-hour cron as five stages, two of them
matrix fan-outs capped at 15 concurrent **GitHub VMs** (ADR-0025 sharded embed, ADR-0026 sharded
scrape). A run-level `concurrency` group serializes whole runs so two never race on the dataset.

**Serving** has two independent paths. The search index is the single-writer end: `merge` uploads
to the private HF dataset `imPoseidon/headstart-index` and restarts the Space
`imPoseidon/headstart-search`. Separately, `python -m headstart` scrapes the same ledger and writes
`docs/jobs.json`; the GitHub Pages dashboard reads *that* file, not the index. The two paths share the `Job` model and the tech filter but run on
their own schedules.

### Which boards a run picks

A run does not scrape every board it could, and the ledger's headline number is not the number
that matters. The 93,688 live boards reduce to 60,545 a run can even consider:

| | boards | |
| --- | ---: | --- |
| live rows in the ledger | 93,688 | |
| − `registry.DISABLED_ATS` | −24,900 | **all of it `join`** — German-SMB boards at ~1 tech job in ~10k |
| − case-variant dedupe | −8,231 | `company/External` and `company/external` are one board (ADR-0023) |
| − `config.EXCLUDED_BOARDS` | −12 | vendor test/sandbox boards, confirmed by reading their postings |
| = selectable | **60,545** | |

Of those, **45,686 are currently hiring** — `load_active_companies` defaults to `min_jobs=1`, so
the 14,859 live-but-empty boards are skipped as having nothing to read. `pick_boards` takes a
slice of
`--max-boards` (default **20,000**) and splits it **30/70**: the top 30% by board-priority score —
a sticky EWMA of each board's tech-job yield, kept in `data/state/board_priority.csv` (ADR-0022) —
and a random 70% exploration tail drawn from everything else, so newly-productive boards can never
starve. The tail is random over *everything* not in the head, not over unscraped boards alone, so
it re-samples known boards too; that is what keeps eviction working on boards outside the head.
Boards a run skips are simply left alone — eviction is scoped to boards actually present in the
scrape (ADR-0014), so a partial harvest never damages what it didn't look at.

### Nothing scraped is ever wasted

Both fan-out stages are time-budgeted, and both bank partial work by design. The inner
`timeout 60m` (scrape) and `timeout 180m` (embed) fire well before the step and job timeouts, and
`|| echo` absorbs the non-zero exit so the fragment still uploads. `JobWriter` flushes after every
board and `EmbeddingStore` flushes vectors then metadata after every batch, so a killed shard loses
at most the item in flight; `embed_merge` truncates any half-written tail. Whatever finished moves
to the next stage, and the unfinished boards and Docs simply reappear in the next run's plan.

### Tech-only, English-only

Every job is scraped, but only tech roles are embedded, indexed, and shown. The scrape writes the
full set to `data/jobs/{ats}.jsonl`; a recall-biased regex filter (`headstart.tech_filter`) derives
the tech subset in `data/jobs/tech/{ats}.jsonl` — **197,561 of 990,173 scraped rows, 20.0%**, in
run 31579689498, though the rate swings hard by ATS (Freshteam 44.0%, Ashby 42.0%, SuccessFactors
10.0%, Workday 8.0%). A non-tech job
creeping in is fine; dropping a tech job is not, so a two-part verification gate guards recall: a
deterministic self-consistency check plus an independent LLM reasoning gate
(`scripts/filter/verify_tech.py`) that judges a sample of the *dropped* pile and flags any real
tech job the regex missed (ADR-0017). A `langdetect` gate then holds non-English descriptions out
of the index before embedding — the scrape and the feed keep them, only retrieval is English-only.

No always-on server: scheduled GitHub Actions, a static Pages site, and a free-tier Space.

## ATS coverage

21 scrapers, selected from a registry by the `ats` key: `ashby`, `darwinbox`, `eightfold`,
`freshteam`, `greenhouse`, `join`, `keka`, `lever`, `oracle`, `personio`, `recruitee`,
`ripplehire`, `rippling`, `sensehq`, `smartrecruiters`, `successfactors`, `teamtailor`,
`trakstar`, `workable`, `workday`, `zoho`. `join` is in `registry.DISABLED_ATS` — German-SMB
boards running ~1 tech job in ~10k, pure noise for a tech-only index — so it is skipped rather
than scraped. Its scraper class and tests stay intact; re-enable by removing it from that set.

Each scraper reads a Board and normalizes its raw postings into `Job` records; all HTTP routes
through one pooled, thread-local `curl_cffi` client that impersonates Chrome, so the same stack
serves plain JSON APIs and the TLS-fingerprinted (Cloudflare / DataDome) boards (ADR-0002). The
liveness pipeline has probed **148,553 boards**: 93,688 live, 37,444 dead, 17,421 unknown. Of the
21 scrapers, 18 currently have rows in the served table — `join` is disabled, and `oracle` and
`sensehq` are single-company unlocks with nothing indexed yet.

## AI semantic search

The search design is a **hybrid split made explicit at the UI**: the user applies structured
filters themselves *and separately* types a natural-language query describing only the role.
Filters drive a deterministic where-clause; the query drives the embedding. `/search` takes
`remote`, `has_salary`, `max_years`, `ats`, `etype`, `india`, `location`, `company`,
`posted_within`, `seen_within`, the four custom bounds `posted_after` / `posted_before` /
`seen_after` / `seen_before`, and the alerts-only `first_seen_after` — all compiled by
`search.build_filter`, which rejects unparseable input with a 400 rather than ignoring it.

- **Embeddings:** `nomic-embed-text-v1.5`, 768-dim, L2-normalized. Task prefixes
  (`search_document:` / `search_query:`) are load-bearing (ADR-0005). Only `title + cleaned
  description` is embedded — structured fields ride alongside as filterable metadata, never inside
  the vector (ADR-0006). The model's context is 8192 tokens but Docs are **capped at 4096**: a
  full-context Doc transiently needs ~50 GB on the MPS stack, and only ~0.01% of the corpus is
  longer. Local runs use the Apple GPU (MPS, fp16); CI runs CPU/fp32, which is 10-40× slower and
  is why the pipeline shards embedding across 15 VMs.
- **Store + retrieval:** LanceDB, embedded and local, does filter-then-rank in one query —
  pre-filter on the typed metadata, rank the survivors by cosine (ADR-0007, ADR-0008). Required
  years-of-experience is extracted to a numeric range by a deterministic cascade so `min_years`
  is a real filter (ADR-0009, ADR-0018).
- **Freshness:** the index is reconciled incrementally, never rebuilt — `index sync` adds new
  vectors and evicts postings that vanished from a scraped board, `index prune` sweeps rows on
  dead boards and case-variant duplicates, `index compact` rewrites the table to reclaim orphan
  fragments (ADR-0014, ADR-0019, ADR-0023).
- **Signed in:** the whole UI sits behind Google sign-in once `SECRET_KEY` and `GOOGLE_CLIENT_ID`
  are set (ADR-0042) — a signed cookie, `SameSite=Lax` instead of CSRF tokens, which is also why
  the app only works at its own URL and not inside the huggingface.co Spaces iframe. Signing in
  unlocks three per-account tabs: **Matches** (Saved sets, one of which projects into the email
  Subscription, ADR-0043), **Saved** (starred jobs kept as display copies so a closed posting
  still renders, ADR-0044), and **Profile** (ADR-0041).
- **Profile:** paste a résumé and one LLM call extracts the stored career record and the single
  role-describing query it implies, editable before it runs — the query stays role-only
  (years/salary are scrubbed in code; those belong to filters). Capped at three parses per account
  for its lifetime, counted in its own single-writer file so a racing save cannot refill it. The
  pasted text is used for that one call and never stored. LLM calls go through the private
  llm-router; if it is unreachable that endpoint 503s on its own rather than taking the app down.

### The served table

One row per Job in the LanceDB `jobs` table — the only thing the Space reads. Defined by `_schema()`
in [`src/headstart/ingest/index.py`](src/headstart/ingest/index.py); `tests/test_readme_schema.py`
fails if this table drifts from it.

| column | type | notes |
| --- | --- | --- |
| `id` | string | `{ats}:{slug}:{native_id}` — the Board key is everything before the last `:` |
| `ats` | string | `greenhouse`, `workday`, `ashby`, `darwinbox`, … |
| `company` | string | the ATS slug, not a display name |
| `title` | string | embedded, with the description |
| `location` | string | raw ATS text; the India filter maps it via a gazetteer (ADR-0024) |
| `remote` | bool | |
| `employment_type` | string | raw per-ATS text (`FullTime`, `Full Time`, `Contract`, …), normalised at query time |
| `experience` | string | raw, for display (`"2 - 5 Years"`) |
| `min_years` | int32 | parsed from `experience`; **nullable** — null means unknown, not zero (ADR-0009) |
| `max_years` | int32 | nullable |
| `experience_source` | string | `field` \| `regex` \| `seniority` \| null — how the years were derived (ADR-0018) |
| `salary` | string | raw, for display (`"INR 3 - 5 (Annual)"`) |
| `department` | string | raw ATS text |
| `url` | string | the job-detail link |
| `posted_at` | string | **the company's** posting date, straight from the ATS — inconsistent (`2026-01-09T00:46:44.672+00:00`, `03-Jul-2026`) and **null on 29%** of a 1,000-row live sample (2026-08-12) |
| `first_seen` | string | **ours** — ISO-8601 UTC, stamped when `index sync` adds the row. Write-once, and null on rows added before the column existed (ADR-0031). Measured **null on 77%** of that same sample, so the "new since" filter currently reaches under a quarter of the table |
| `vector` | list\<float32\>[768] | `title + cleaned description`, L2-normalized |

Two example rows, fetched from the live index on 2026-08-12 (signed in — `/search` 401s an
anonymous caller) — exactly as `/search` projects them, which is why `experience`, `max_years`,
`experience_source`, `department` and the vector do not appear: the API omits those, though the
table stores them.

```jsonc
{
  "id": "ashby:level:538c0fe2-504d-45e9-8ae6-2b44de217418",
  "ats": "ashby", "company": "level",
  "title": "Backend Engineer (senior or above)",
  "location": "Austin", "remote": false, "employment_type": "FullTime",
  "min_years": 5, "salary": null,
  "url": "https://jobs.ashbyhq.com/level/538c0fe2-504d-45e9-8ae6-2b44de217418",
  "posted_at": "2026-01-09T00:46:44.672+00:00",     // ISO — this ATS is well-behaved
  "first_seen": null                                 // indexed before ADR-0031 added the column
}
{
  "id": "darwinbox:jslhrms:a65a11b3d9c70e",
  "ats": "darwinbox", "company": "jslhrms",
  "title": "Junior Engineer (Central QA)",
  "location": "Jajpur, Odisha , India",              // raw ATS text, stray spacing and all
  "remote": false, "employment_type": "Full Time",
  "min_years": 1, "salary": null,
  "url": "https://jslhrms.darwinbox.in/ms/candidatev2/main/careers/jobDetails/a65a11b3d9c70e",
  "posted_at": "03-Jul-2026",                        // NOT ISO — why the recency filter
  "first_seen": null                                 // needs a shape guard on posted_at
}
```

The second row is the reason `posted_at` and `first_seen` are separate columns rather than one
"date". `23-Jun-2026` sorts lexicographically *above* any ISO cutoff, so a naive
`posted_at >= '2026-07-01'` would let it into every window — hence the `LIKE '____-__-__%'` shape
guard on that filter, and none on `first_seen`, which we write ourselves.

Note the corpus files under `data/jobs/` carry a few fields the table does not, e.g. `scraped_at`
and the full `description`. The description is embedded, not stored — the vector is what survives
into the table.

### Retrieval eval

Ranking quality is measured, not asserted (ADR-0011). A five-stage harness in `scripts/eval/`:
pool the search's top hits per query, grade each `(query, job)` pair `0–3` with an LLM judge,
validate that judge against hand labels (quadratic-weighted Cohen's **κ ≈ 0.64**, "substantial"),
then score with `ranx` → **nDCG@10 = 0.90** on the Wellfound benchmark corpus. Two honest limits,
printed with the score: it is a single-system pool, so nDCG measures how well the search orders its
own picks, not corpus-wide recall (pooling a second system, e.g. BM25, is the named next step); and
the benchmark is kept deliberately distinct from the production tech corpus (ADR-0014, ADR-0019).
`scripts/eval/verify_filters.py` separately checks every filter's semantics and every ATS's job-link
correctness against the live Space — signed in, since the wall 401s an anonymous caller. It fails
the run on a dead link (404/410), a wrong-shaped or wrong-job link, or an ATS with no shape
registered; bot walls (403/429) stay advisory.

## Layout

- `src/headstart/` — shared library, used by both the pipeline and the curated feed: `models.py`
  (Job + normalization), `scrapers/` (21 per-ATS + `base`/`registry`), `http.py` (the pooled
  reliable-fetch seam), `config.py`, `harvest.py` (the scrape engine — `scrape_all`, `JobWriter`,
  feed builders), `liveness.py`, `corpus.py`, `tech_filter.py` (ADR-0017), `experience.py`,
  `geo.py`, `search.py` (shared embed/search constants + filter builder), `board_priority.py`
  (ADR-0022), `board_cost.py` (measured scrape seconds, ADR-0027); plus `telegram.py`, the
  polling client the enrolment bot uses.
- `src/headstart/alerts/` — job alerts (ADR-0035, ADR-0038) plus the signed-in per-account
  records: `store` (Subscriptions, Saved sets, Saved jobs and Profiles — the name now undersells
  it), `registry`, `access` (the invite allowlist), `identity` (Google token verification),
  `transports` (which channel delivers a Digest), `mail` and `telegram` (the senders), `bot`
  (Telegram enrolment), `digest`, `shortlist`, `space_query`, `run`.
- `src/headstart/ingest/` — **the 2-hourly pipeline run**, one module per stage step, invoked as
  `python -m headstart.ingest.<module>` (ADR-0028): `scrape_plan`, `scrape_run`, `scrape_join`,
  `filter_tech`, `update_ledgers` (`priority`/`cost`), `embed_plan`, `embed_run`, `embed_merge`,
  `index` (`sync`/`prune`/`compact`), `role_trends` (ADR-0040). `.github/workflows/pipeline.yml`
  runs exactly these. Its pipeline-only helpers live here too: `binpack.py` (LPT packing shared by
  both planners), `doc_prep.py` (doc prep shared by embedder and planner), `index_plan.py` (the
  pure add/evict and prune planners), `observability.py` (run context, step summaries and the
  per-shard reports the join aggregates), `state_fetch.py`.
- `scripts/` — tooling *outside* the run: `discover/`, `merge/`, `validate/`, `resolve/`,
  `scrape/` (one-off pulls), `filter/` (recall verification), plus the AI layer in `embed/`
  (local index tools), `enrich/`, `eval/`, `ui/`.
- `data/` — `validate/liveness/` is git-tracked and authoritative. **Everything else under `data/`
  is gitignored and lives in the HF dataset**, not in the repo: `state/`, `embeddings/`,
  `lancedb/`, `jobs/`. Pull them from HF before trusting any local copy.
- `deploy/hf-space/` — the Space app; `deploy-space.yml` pushes it on change, so the repo stays
  the single source of truth for what runs there.
- `docs/` — `index.html` dashboard + generated `jobs.json` (served by Pages), `adr/`,
  `AI_Integration/`, `agents/` (issue tracker, triage, domain, deployment runbooks).
- `.github/workflows/` — `pipeline.yml` (the 5-stage ingest), `pipeline-smoke.yml`, `ci.yml`
  (lint + format + tests), `alerts.yml` and `bot.yml` (email/Telegram alerts), `deploy-space.yml`,
  `cleanup-index.yml`, `cluster-roles.yml`, `squash-dataset-history.yml`, and the two embed
  benchmarks `embed-bench.yml` / `embed-threads.yml`.

## Development

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                               # network-free, fixture-based
ruff check . && ruff format --check .

python -m headstart                  # curated scrape → docs/jobs.json
python -m http.server -d docs        # preview the dashboard at http://localhost:8000
```

Semantic-search demo. The corpus and embedding artifacts are gitignored — pull them from the HF
dataset first (see [`docs/agents/deployment.md`](./docs/agents/deployment.md) for auth):

```bash
pip install -e ".[embed,ui]"
python -c "from huggingface_hub import snapshot_download; snapshot_download(
    'imPoseidon/headstart-index', repo_type='dataset', local_dir='.',
    allow_patterns=['data/state/*','data/embeddings/jobs/*','data/lancedb/*'])"
python scripts/ui/serve.py                # search UI at http://localhost:8000
```

To rebuild rather than download — note `embed_run.py` is CPU-bound and belongs on CI at any real
scale (ADR-0025):

```bash
python -m headstart.ingest.embed_run --resume   # embed the English tech corpus
python -m headstart.ingest.index sync            # incremental add/evict into the LanceDB `jobs` table
```
