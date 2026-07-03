# HeadStart

Find software-engineering openings straight from companies' ATS (Applicant Tracking
System) career boards — earlier and more completely than relying on LinkedIn.

HeadStart discovers which companies host boards on which ATS, validates those boards, scrapes
them through **18 per-ATS scrapers**, normalizes everything into one `Job` shape, and serves it
two ways: a static dashboard over a curated feed, and an **AI semantic-search layer** (local
embeddings → vector search with structured filters) measured by a validated retrieval-eval
harness at **nDCG@10 = 0.90**.

- **Design decisions:** [`docs/adr/`](./docs/adr/) — 11 numbered ADRs (the option picked, the
  ones rejected, and why).
- **Domain glossary:** [`CONTEXT.md`](./CONTEXT.md) — the ubiquitous language (ATS, Board, Slug,
  Job, Discovery, Liveness, Feed…).
- **AI layer design + results:** [`docs/AI_Integration/`](./docs/AI_Integration/).
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

Two halves share the `Job` model. A discovery→scrape pipeline finds and reads boards; a search
layer makes the result queryable.

```
Pipeline (per CONTEXT.md):
  discover   find companies on ATSes (Common Crawl / Wayback feeders, careers-page fingerprint)
    → merge  union + dedupe the discovered lists per ATS
    → validate  liveness-probe each board (Live / Dead / Unknown), write the Active lists
    → resolve  map a known company → its (ATS, slug)
    → scrape  18 ATS scrapers read the Active boards, normalize to one Job, stream to JSONL
    → filter  keep only software/tech roles -> data/jobs/tech/ (recall-biased, ADR-0017)

Serving (all from the tech subset):
  curated feed  → docs/jobs.json  → GitHub Pages static dashboard (client-side filter)
  tech corpus   → embed → LanceDB → semantic search (filter-then-rank) + Telegram alert bot
```

Every job is scraped, but only tech roles are embedded, indexed, and shown. The scrape writes the
full set to `data/jobs/{ats}.jsonl`; a recall-biased regex filter (`headstart.tech_filter`) derives
the tech subset in `data/jobs/tech/{ats}.jsonl` — ~17% of the raw jobs, so the embedding model does
~83% less work. A non-tech job creeping in is fine; dropping a tech job is not, so a two-part
verification gate guards recall: a deterministic self-consistency check plus an independent LLM
reasoning gate (`scripts/filter/verify_tech.py`) that judges a sample of the *dropped* pile and flags
any real tech job the regex missed (ADR-0017).

No always-on server: scheduled GitHub Actions plus a static Pages site. The millions-scale
harvest produces only per-ATS JSONL; the dashboard serves a small curated subset, and true
scale (search over the full corpus) is the AI backend, not the static page (ADR-0010).

## ATS coverage

18 scrapers, selected from a registry by the `ats` key: `greenhouse`, `lever`, `ashby`, `zoho`,
`workday`, `workable`, `smartrecruiters`, `recruitee`, `oracle`, `sensehq`, `keka`, `trakstar`,
`ripplehire`, `darwinbox`, `teamtailor`, `personio`, `join`, `rippling`. Each reads a Board and
normalizes its raw postings into `Job` records; all HTTP routes through one pooled, thread-local
`curl_cffi` client that impersonates Chrome, so the same stack serves plain JSON APIs and the
TLS-fingerprinted (Cloudflare / DataDome) boards (ADR-0002). The liveness pipeline has validated
tens of thousands of live boards across these ATSes.

## AI semantic search

The search design is a **hybrid split made explicit at the UI**: the user applies structured
filters themselves (remote, employment type, max years of experience) *and separately* types a
natural-language query describing only the role. Filters drive a deterministic where-clause; the
query drives the embedding.

- **Embeddings:** `nomic-embed-text-v1.5`, run locally on the GPU. Its 8192-token context embeds
  each full job description without truncation; task prefixes (`search_document:` / `search_query:`)
  are load-bearing (ADR-0005). Only `title + cleaned description` is embedded — structured fields
  ride alongside as filterable metadata, never inside the vector (ADR-0006).
- **Store + retrieval:** LanceDB, embedded and local, does the filter-then-rank in one query —
  pre-filter on the typed metadata, rank the survivors by cosine (ADR-0007, ADR-0008). Required
  years-of-experience is extracted to a numeric range by a deterministic cascade so `min_years`
  is a real filter (ADR-0009).
- **Scope:** the search corpus is **English-only for now** — an explicit `langdetect` gate holds
  non-English descriptions out of the index before embedding (multilingual retrieval deferred).
  The LLM query-parser that would infer filters from free text is deliberately deferred; the
  constraints come from explicit controls.

### Retrieval eval

Ranking quality is measured, not asserted (ADR-0011). A five-stage harness in `scripts/eval/`:
pool the search's top hits per query, grade each `(query, job)` pair `0–3` with an LLM judge,
validate that judge against hand labels (quadratic-weighted Cohen's **κ ≈ 0.64**, "substantial"),
then score with `ranx` → **nDCG@10 = 0.90**. Honest limit, printed with the score: it is a
single-system pool, so nDCG measures how well the search orders its own picks, not corpus-wide
recall (pooling a second system, e.g. BM25, is the named next step).

## Layout

- `src/headstart/` — the package: `models.py` (Job + normalization), `scrapers/` (18 per-ATS +
  `base`/`registry`), `http.py` (the pooled reliable-fetch seam), `config.py`, `pipeline.py`,
  `search.py` (shared embed/search constants + filter builder), `experience.py`,
  `tech_filter.py` (the tech-role gate, ADR-0017); plus the v2 bot: `filters.py`, `bot.py`,
  `telegram.py`, `state.py`.
- `scripts/` — the pipeline stages (`discover/`, `merge/`, `validate/`, `resolve/`, `scrape/`,
  `filter/`) and the AI layer (`embed/`, `enrich/`, `eval/`, `ui/`).
- `docs/` — `index.html` dashboard + generated `jobs.json` (served by Pages), `adr/`,
  `AI_Integration/`.
- `.github/workflows/` — `ci.yml` (lint + format + tests), `bot.yml` (Telegram alerts).

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

Semantic-search demo (needs a corpus in `data/jobs/`; the embedding artifacts are regenerable
locally and gitignored for size):

```bash
pip install -e ".[embed,ui]"
python scripts/embed/embed_wellfound.py   # embed the English corpus → data/embeddings/
python scripts/enrich/extract_experience.py
python scripts/embed/build_index.py       # load LanceDB
python scripts/ui/serve.py                # search UI at http://localhost:8000
```
