# Wiring a new scraper into the repo

Every file a scraper PR touches, and what each needs. The generic tests enforce some of this
(`tests/test_base.py`, `tests/test_scrapers.py`, `tests/test_scraper_registry.py`,
`tests/test_detail_loss_status.py`, `tests/test_scraper_log_levels.py`); the rest is caught only
by review, so it is listed here. In the shared lists (the registry imports and tuple, `PROBES`,
`ATS_PATTERNS`, `ATS_HOSTS`, `_RESOLVE_ROWS`, the README list) insert each entry beside its
nearest alphabetical neighbour, leaving the rest of the list in place: sibling scraper PRs edit
the same lists, and entries all appended at the end conflict with each other.

## Scraper

`src/headstart/scrapers/{ats}.py`, subclassing `BaseScraper` (`base.py` — read its attribute
docstrings; each one records why it exists). Model it on `pyjamahr.py` (JSON listing + detail) or
`icims.py` (sitemap + per-job HTML/JSON-LD).

Class attributes:

- `ats` — the registry key, lowercase, equal to the module name.
- `url_shape` — regex every `job_url()` output matches (escape `?`, `&` and `.` — ADP and csod
  job URLs carry query strings); `verify_filters.py`'s `URL_SHAPES` is
  generated from it (ADR-0157). Derive it from the URL the scraper builds, then fetch real job
  URLs of that shape and confirm each lands on the posting — not a redirect to the board home
  (phenom's wrong country prefix 200s to the landing page; darwinbox's SPA routed bad links to
  the dashboard).
- `has_detail_pass = True` when a second per-Job request fills fields — read by the embed
  planner and `board_priority` (ADR-0050).
- `detail_workers` — below the measured knee: `harvest` scrapes Boards concurrently, so peak
  in-flight is Boards × workers. 16 is what icims, zwayam, oracle and pyjamahr run.
- `detail_streams`, `async_fanout`, `egress_fallback_on`, `alias_vendor_hosts` — only on a
  measured reason, each stated in `base.py`.
- `COMPANY` — single-company in-house systems only (ADR-0139); multi-tenant ATSes leave it None.

Methods (`url`, `parse`, `job_url` and `_salary_field` are abstract):

- `url()` — the listing request; `fetch_raw()` — network, returning a dict that `parse()` reads;
  `parse(raw, scraped_at)` — **pure**, everything the tests exercise; `job_url(native_id)`.
- Every request goes through `self._get` / `self._fetch` or `self._get_async` /
  `self._fetch_async` (base.py). They carry the injected Fetcher (ADR-0153) and the egress
  opt-in; a direct `http.fetch` call silently loses both. A token-bearing API (measurement.md
  Q11b) fetches its token once per Board in `fetch_raw`, sends it as a header through `_fetch` /
  `_fetch_async`, and refreshes it on the expiry response measured. A subclass that overrides
  `_get` overrides `_get_async` too.
- `slug_from(tenant, url)` — override when the slug is not the ledger's `tenant` column verbatim
  (a host read out of `url`, a lowercased label). `job_id()` composes `{ats}:{slug}:{native_id}`,
  so changing the slug later evicts every row of the Board as off-Board.
- Pagination ends on the terminator measured in step 2. A shortfall against the Board's own
  stated total goes to `mark_truncated_unless_negligible(read, expected, why)`; an unreachable
  remainder (a hard cap, a page ceiling) to `mark_truncated(why)` (ADR-0053/0121). A listing that
  cannot be parsed at all calls `note_unreadable_board(expected, got)` before returning `[]`.
- Detail fetches go through `fan_out_async` (default) and `fan_out`, and a failure records
  `note_detail_exception(exc)` beside the `None` it returns, so the loss keeps its HTTP status.
  Call `report_detail_gaps(results, what)` after the fan-out.
- The tech gate: `self.tech_detail_wanted(items, title_of, department_of)` before the fan-out,
  when step 2 measured it exact or a tolerable approximation; say which in the docstring and in
  CONTEXT.md's **Detail pass** entry (it lists every scraper's verdict).
- `needs_detail(native_id)` (ADR-0048) — skip a detail already in the description store, unless
  the detail also supplies other fields that would go blank (oracle and pyjamahr decline it).
- Per-Board log lines are INFO. A WARNING is an Actions annotation against a quota of 10 per step
  (ADR-0039); `test_scraper_log_levels.py` fails an unlisted one.
- `html_to_text()` for descriptions, `is_remote()` only as a fallback to a stated field,
  `host_of()` for any host arithmetic — all in `headstart.models`.

Field formatting — values stay as the provider phrases them, shaped for the shared extractors:

- `salary` via `_salary_field()`: a string `salary._field_generic` reads correctly
  (`"30000-40000 INR per-month"`), or a dedicated `_field_{ats}` parser registered in
  `salary._FIELD_PARSERS`, with tests in `tests/test_salary.py`. A new dispatch key touches no
  stored row, so it needs no `DERIVATIONS_VERSION` bump; a change to shared parsing does.
  An ATS that states no salary still overrides `_salary_field` to return None, with a comment
  citing the measurement (base.py's contract).
- `experience` as a string `experience.from_field` reads (`"3-5 years"`; coinciding bounds stay a
  range, `"3-3 years"`).
- `employment_type` and `posted_at` (ISO-8601) as measured.

Company name (ADR-0114), only if step 2 found a surface naming the employer:
`board_page()` returns the page, `company_name.PATTERNS["{ats}"]` extracts the name from its
`<title>`, `company_name._VENDOR_ALIASES["{ats}"]` lists the vendor's own brand, and
`tests/test_scrapers.py`'s `_RESOLVE_ROWS` gets a real row. The consistency test requires all
four together.

Register it: import and add the class in `src/headstart/scrapers/registry.py`; add it to
`DISABLED_ATS` there if step 6 decided so, with the arithmetic in the comment.

Tests: `tests/test_{ats}.py` over fixtures recorded from real responses (`tests/fixtures/`). Cover
at least: a full parse against the fixture, pagination to its terminator, the truncation paths,
a failed detail (the Job still ships, without a description), every field mapping measured as
non-obvious, and `url_shape` matching what `job_url()` builds. CI installs `.[dev]` (lancedb,
pyarrow and langdetect included) under a 10-minute timeout, so importorskip only what the
`embed`/`scrape`/`eval`/`alerts` extras bring (sentence-transformers, pydoll, anthropic,
google-auth).

## Discovery

Sources, cheapest first:

- A vendor-published roster: a cross-tenant sitemap (pyjamahr's `sitemap-jobs.xml` named 680
  tenants in one fetch), a tenant directory endpoint, a shared TLS certificate's SAN list. Write
  the miner as `scripts/discover/mine_{ats}.py` with a docstring saying what it covers and what
  it does not.
- Wayback: add the host(s) to `ATS_HOSTS` in `scripts/discover/wayback_feeder.py` with the
  style that matches the slug (`sub` for `{slug}.vendor.com`, `path` for `vendor.com/{slug}`,
  `host` when the slug is the whole host; see the `Style` definitions there), then run
  `PYTHONPATH=src python scripts/discover/wayback_pages.py {ats}` (the feeder itself is a
  library and exits silently) and `PYTHONPATH=src python
  scripts/merge/merge_wayback_into_tenants.py`. The sweep is long: run it in the background at
  the default 2 workers and build the scraper meanwhile. A key that lives in a query string (ADP's
  `cid`) fits no Wayback style — rely on Common Crawl, the seed list and a vendor roster, or add a
  style with a test in `tests/test_wayback_feeder.py`.
- Common Crawl: add a `{ats}` entry to `ATS_PATTERNS` in `scripts/discover/cc_miner.py`, then
  `CC_ONLY_ATS={ats} PYTHONPATH=src python -u scripts/discover/cc_miner.py [CC-MAIN-…]` and
  `PYTHONPATH=src python scripts/merge/merge_cc_into_tenants.py`. Sweep older indexes until the
  count goes flat. A pattern's capture group can read a query key (greenhouse's `for=`).
- The careers-page fingerprinter: registering the scraper makes the ATS `SUPPORTED` in
  `scripts/discover/fingerprint_careers.py`; check its `normalise_tenant` emits your slug shape,
  with a case in `tests/test_fingerprint_careers.py`.
- The upstream seed list, folded with its own `source` tag.

The pool is candidate-grade: leave its noise for the prober to kill. The ledger is what must be
right.

## Liveness

`scripts/validate/check_liveness.py`: a `p_{ats}(tenant, url)` returning `(LIVE, n)`,
`(DEAD, None)` or `(UNKNOWN, None)`, registered in `PROBES`. Use the module's `_get` (it carries
the gates, the backoff and the egress rotation) and `_note(reason)` for every non-settling
outcome. The cheapest request that yields a job count first; a second request only to settle what
the first cannot (pyjamahr's zero). A redirect-dead tenant needs `_fetch(..., allow_redirects=False)`
(measurement.md Q9). A rate limit that spans tenants gets its host in `_SPANNING` with a `_GATES`
entry, and a bare-403 quota its host in `_QUOTA_403`, each citing the measurement (Q19). Probes
that call `_fetch` directly are tested by monkeypatching `cl._fetch` (the `p_jobvite` tests). `DEAD` only on a response measured on real dead tenants;
everything unexplained is `UNKNOWN`, which is re-probed rather than lost. Tests go in
`tests/test_liveness_probes.py`, one per branch, by monkeypatching `cl._get` (see the pyjamahr
block there).

Run `PYTHONPATH=src python scripts/validate/check_liveness.py {ats}` in the background; it
reads the pool from the worktree's `data/ats-tenants-merged/{ats}.csv` and writes
`data/validate/liveness/{ats}.csv` (`ats,tenant,url,status,jobs,checked_at`), which is committed.
Then check it: job counts vary, no constant repeats across many Boards, the row count equals the
unique `board_key` count (or each duplicate is explained), and a spot check of 5 `live` and 5
`dead` rows against the live host agrees with the verdict. Pool vendor test tenants and
load-test instances go in `config.EXCLUDED_BOARDS` (oracle's 78,431-posting load-test tenant,
jobvite's `jvauto`).

## Docs

- `docs/adr/{NNNN}-{title}.md` in the house format (`**Status:** accepted · **Date:** … ·
  **Relates to:** …`, then Context / Decision / Alternatives considered / Consequences), plus its
  line in `docs/adr/README.md`.
- `docs/{ats}/{date}_{surface}-measurement.md` — the step-2 answers with sample sizes and the
  commands that produced them; `experiment/{ats}-{surface}/LOG.md` with the raw captures in
  `artifacts/`.
- `CLAUDE.md` TODO list: the ✅ DONE entry — date, PR, module, ledger figures (live / hiring
  Boards, postings), slug identity, and each measured trap wired into the code.
- `README.md` §"ATS coverage": the scraper count — `**N scrapers**`, the intro line's "N
  scrapers", and the "Eight of the N" sentence below the list, all the same N, the registry's
  size — and the alphabetical list.
- Board figures in `README.md` and `CONTEXT.md` §Counting Boards change with every ledger. Print
  the recomputed figures from the tree itself:

  ```bash
  PYTHONPATH=src python3 -c "
  import sys; sys.path.insert(0,'tests')
  from test_board_counts import counts
  for k,v in counts().items(): print(f'{k:24} {v:,}' if isinstance(v,int) else f'{k:24} {v}')"
  ```

  Replace each figure sentence by sentence: README carries two
  near-equal dedupe figures (the funnel's `dedupe_after_exclude` and the glossary's
  Live row − Unique Board) that read identically in prose. Then run `tests/test_board_counts.py`
  until green, and re-read the prose around every figure you changed.
- CONTEXT.md's **Detail pass** entry when the scraper has a detail pass: add it to the list of
  exact, approximated or ungated scrapers with its reason, and update that entry's own counts
  ("Twelve scrapers do it", "Seven are exact").
