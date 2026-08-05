# ADR-0037: Wellfound is scraped through three surfaces, behind a real browser

- Status: Accepted
- Date: 2026-08-05
- Supersedes nothing, but corrects the implicit assumption behind the original role-page scrape:
  that paginating a listing page yields that page's jobs. It does not.
- Leaves [ADR-0014](0014-search-index-ingestion-and-freshness.md)'s and
  [ADR-0019](0019-tech-corpus-search-index.md)'s split in place **for now** — Wellfound is still
  the frozen eval benchmark (`EVAL_TABLE`), not the served corpus. That is a staging point, not
  the end state: **Wellfound is intended to become a pipeline source**, and this ADR exists partly
  to make that possible. The remaining work is the pending section below.
- Consumes [ADR-0017](0017-tech-role-filter.md)'s `is_tech` as a *cost reducer* only; the
  authoritative post-hoc tech gate is unchanged.
- Follows [ADR-0004](0004-memory-safe-parallel-resumable-scrape.md)'s resumability contract, and
  sits deliberately **outside** [ADR-0028](0028-ingest-package.md)'s ingest package (see
  Consequences).

Terminology note: CONTEXT.md reserves **Board** for an ATS-hosted listing. Wellfound is an
aggregator, not an ATS, so this ADR says *role page* and *company page* for its surfaces and
never calls them Boards.

## Context

Wellfound (ex-AngelList Talent) matters to this project for one measurable reason: **70% of the
jobs we take from it carry no `atsSource`** — 4,530 of 6,462 rows in a full sweep have that field
null, meaning no upstream Greenhouse/Ashby/Lever board to scrape instead. For those companies the
existing "prefer the native ATS" shortcut can never apply, so if we want them at all, we take them
here.

It is also the hardest target we have. The site runs **DataDome**
(`geo.captcha-delivery.com`) and **Cloudflare Bot Management** together. A plain HTTP client gets
a 403 JS challenge; a distrusted IP gets an interactive slider that will not auto-clear.

The original scrape read the SEO role pages — `/role/l/{role}/{location}` and `/role/r/{role}` —
and paginated them. On 2026-08-05 that was measured against the site's own reported numbers and
found **structurally incomplete**:

| page / company | collected | site's own count |
| --- | --- | --- |
| `devops-engineer/india`, full 8-page run | 200 | 206 (`totalJobCount`) |
| `artificial-intelligence-engineer/remote` | ≈4,100 (extrapolated from 4 sampled pages) | 6,784 |
| Deepgram | 19 | 78 |
| Netskope | 18 | 139 |

The cause is two properties of the role page, neither a bug in our code. It groups jobs by company
and caps `highlightedJobListings` at **3 per company**; and its `perPage: 20` counts *startups*,
not jobs, so `pageCount = ceil(totalStartupCount / 20)`. Paginating perfectly still only ever
yields `min(3, n)` jobs per company. Verified on a captured page: all 48 listings in the Apollo
cache were reachable from a `StartupResult`, zero orphans — the parser was extracting 100% of what
the page contained. Deep pagination was fine too (pages 1→114 return 20 distinct startups with
zero overlap; past the end it wraps to page 1).

**The missing jobs were never in the HTML.** No parser or paging fix could reach them, and a
role-page-shaped scrape had no way to notice: nothing on the page says a company was truncated.

A second, independent limit: the sweep covers 11 roles × {india, remote} = 22 pages. Jobs outside
that matrix — Deepgram's San Francisco and New York listings — are invisible whatever the cap does.

## Decision

**Scrape Wellfound through three surfaces, each doing the one thing it is good at, all driven by
a real Chrome over CDP (pydoll) through Cloudflare WARP.**

1. **Role pages → company discovery.** `run_wellfound_sweep.py` still walks the 22 pages, but its
   job is now to enumerate company slugs (3,316 of them). The ≤3 jobs per company it returns are
   kept, because they arrive with **full descriptions at no extra cost** — the role page's
   `description` field is the complete text (median 3,523 chars).
2. **Company page → the complete roster.** `run_wellfound_company_jobs.py` walks
   `/company/{slug}/jobs?page=N`: server-rendered, 20 `JobListing` nodes per page, no per-company
   cap, and a plain `?page=` advances the base64 offset cursor (`after: "MA=="` → `"MjA="`). This
   also reaches the off-matrix locations and roles the role pages cannot see.
3. **Job page → the full description.** `run_wellfound_job_details.py` fetches
   `/jobs/{id}-{slug}`, because the company page carries only a `descriptionSnippet` of ~300 chars
   (measured 198–424), and full descriptions are what the embedding corpus needs
   ([ADR-0006](0006-what-we-embed.md)).

### Getting past the anti-bot layer

A real browser is the decision, not an implementation detail. pydoll drives Chrome over CDP, which
is why Cloudflare passes at all — its JS Detections is VM-obfuscated and not worth forging. On top
of that: stealth launch flags, one warm-up hop onto `/jobs` so the first deep link reads as in-site
navigation, jittered pacing, and a per-page mouse-move plus humanized scroll emitted as *trusted*
CDP input (a JS `scrollBy` fires untrusted and is itself a tell). When DataDome does challenge,
`datadome_slider.solve_slider` attempts a humanized drag — dispatching raw CDP with `force=0.5`
held across press and moves, because pydoll's default pressure of 0 with `buttons=1` is a state
real hardware cannot emit — with a Whisper-transcribed audio challenge as the `--audio-first`
alternative. A *hard* block ("Access is temporarily restricted") is distinguished from a solvable
challenge and aborts the whole run.

### Four rules that make the walk trustworthy

These are the real content of this decision; the three surfaces are just where the data lives.

**Never paginate on a reported count.** `totalCount` reads 0 on companies whose page serves 20
listings, and disagreed with the live page on Deepgram (77 vs 78). It is display-only. The walk
ends on what the pages show: a page shorter than `PER_PAGE`, or one whose ids repeat the previous
page's. Trusting a count is exactly how the role page truncated silently, and repeating that
mistake one layer down would have been invisible the same way.

**Distinguish "no jobs" from "not read".** `parse_company_page` returns `(jobs, total, found)`. A
company page also caches ~20 *recommended* startups, so `found` keys on the requested slug — and a
company that renders with zero openings (`staple-3`) is a finished walk, whereas a redirect,
rename, or garbled render is not. Collapsing the two retires a company permanently having never
read it.

**Checkpoint at the granularity you iterate.** Stage 2 records a slug in its done-file only when
that company's walk ended cleanly; stage 3 uses its output CSV as the checkpoint. On a hard block
the run aborts and `--append` resumes from the last fetched item. Every further request while the
restriction is live re-signals it, and re-fetching banked work spends the IP's trust for nothing.

**Spend a page load only when nothing cheaper has the text.** Per job: reuse the role page's
description if the sweep already saw it; else fetch only if `is_tech(title, department)` passes;
else keep the snippet. Every row records provenance in `desc_source`
(`board` / `detail` / `snippet` / `gone`), so weak descriptions are visible rather than assumed.

### The predicate that made stage 3 affordable

`_is_blocked()` treats a missing `__NEXT_DATA__` as blocked. **Job detail pages have none** — they
carry a JSON-LD `JobPosting` instead — so every one looked blocked and burned the full retry
budget before returning HTML that was correct from the first read: **41s/page, versus 0.2s** with
a shape-aware predicate. `_load_page` therefore takes an optional `blocked=` predicate. Only a
*challenge* is worth waiting out; a resolved page without a posting is a dead listing, and some
**live** listings serve no JSON-LD at all (Checkfront's `4476061` renders, says "Actively Hiring",
and has none), for which the rendered `#job-description` block is the fallback — verified
byte-identical (8,711 chars) to the JSON-LD text on a page carrying both.

## Alternatives considered

- **Keep the role pages only, and accept the loss.** Rejected on the numbers: ~39% of the
  AI-engineer/remote page is unreachable, worst exactly where it matters most — companies hiring
  heavily. The loss is also *silent*, which is the disqualifying property.
- **Call the GraphQL API from the page context** (one navigation per company, then `fetch()` with
  the cursor, which is plain base64 of the offset). Probably faster, but unmeasured, and the
  persisted-query contract is unverified; full navigations reuse anti-bot handling that already
  works. Deferred, not rejected — revisit if wall-clock becomes binding.
- **A pure-API path, forging the DataDome payload.** The DataDome tag is plain-minified and
  reversible in principle, but Cloudflare's VM-obfuscated JSD runs alongside, and CDP-direct
  browsing is *why* we already pass it. Beating both to save browser overhead is a large project
  defending a small prize.
- **Resolve every company to its native ATS instead** (no DataDome at all). Already done where
  possible, and it structurally cannot cover the ~70% with no `atsSource`.
- **Fetch a detail page for every job, not just tech-titled ones.** Roughly doubles the first pass
  and the anti-bot exposure for jobs ADR-0017's filter drops anyway. `--all-titles` exists as the
  escape hatch and re-attempts anything left at `snippet` or `gone`.

## Consequences

- **Completeness is bounded by what the company page reports, not by a listing page's display
  budget.** Off-matrix locations and roles come along for free.
- **The first pass is long** — stage 3 is one page load per job, and pacing (not load time, which
  is 0.2s) dominates: roughly a day for ~17k tech-titled jobs. At the pre-fix 41s/page it would
  have been an order of magnitude worse, which is what made the predicate fix load-bearing rather
  than an optimisation. Both figures are estimates; only the per-page times were measured.
- **One deliberate exception to the resume rule:** hitting `PAGE_CEILING` (200 pages ≈ 4,000 jobs
  for a single company) ends the walk *and* records the company done, on the grounds that a
  company that deep is a pathology rather than a roster and retrying would hit the same wall every
  run. It prints a loud `!!` line. Every other incomplete outcome is left for `--append`.
- **These stages live in `scripts/scrape/`, not `src/headstart/ingest/`.** ADR-0028 reserves the
  ingest package for the scheduled pipeline run; this is a WARP-gated, human-supervised pull that
  cannot run on a GitHub Actions runner today. It stays R&D tooling until the pending work below
  lands.
- **`wellfound` now has a `URL_SHAPES` entry** in `scripts/eval/verify_filters.py`, verified
  against all 6,462 rows of the sweep (zero non-matching). This is defensive rather than
  corrective: the harness gates ATSes that are actually served, and Wellfound is the eval table,
  so the entry is there for when it *is* served.
- **`wellfound_full.csv` may contain a duplicated id after an `--all-titles` re-fetch**, because
  `--append` only appends. The contract is *last row wins* — it carries the better description. A
  plain `--append` run never duplicates. This is documented, not enforced.
- **Three surfaces mean three ways the site can change**, each independently fragile. Fixtures for
  the company page and both job-detail shapes live in `tests/fixtures/wellfound_*.html`; the role
  page's 3-per-company cap has **no fixture and no test** — it is pinned only by this ADR and the
  measurements above.
- Evidence for the measurements sits in `experiment/wellfound-datadome/LOG.md` and
  `device-check-map.md`. Both are **untracked** (`.gitignore` excludes `experiment/`), so the
  numbers are reproduced inline above rather than left as a reference a clone cannot follow.

## Pending: integrate Wellfound into the 2-hourly pipeline

**This is the intended destination for Wellfound, not an open question.** Today it is neither in
the served corpus nor on the scheduled run — all three stages are invoked by hand — and the
three-surface design above was built so that it *can* run unattended: every stage is resumable,
checkpointed per item, and safe to re-run.

What remains is to make it a production source on the 2-hourly pipeline
(`.github/workflows/pipeline.yml`, `cron: 30 1-23/2`). Four things block it, roughly in order of
difficulty:

1. **Egress.** The standing rule is that Wellfound is only requested through Cloudflare WARP, and
   a hosted runner has neither WARP nor a residential IP — a datacentre IP is what DataDome blocks
   hardest. Either a residential/mobile proxy, or running this stage on the Oracle box that
   already hosts the llm-router. Note `--proxy` is currently wired only in `run_wellfound.py`; the
   sweep and both new stages hardcode no proxy, so that flag has to be threaded through first.
2. **A browser with a display.** The scrape defaults to headful because it keeps the UA and client
   hints genuine; `--headless` exists but raises the challenge rate. The chosen host needs a
   display, or a measured decision to accept headless plus more solves.
3. **Runtime shape.** Stage 3 is per-job and paced, so a full pass is hours — it cannot sit inside
   a 2-hourly job. It wants its own schedule with a per-run work budget, walking the roster
   incrementally; the checkpointing above already supports that.
4. **Then the ADR-0028 move and the promotion.** Once it runs unattended it stops being R&D
   tooling and belongs in `src/headstart/ingest/`, with its output joining `data/jobs/` through
   the normal `filter_tech` → embed → index path.

That last step also forces a decision this ADR does not make: the eval table's whole value is that
it stays frozen, so promoting Wellfound to a live source means ADR-0014's benchmark/production
split needs a separate resolution — most likely pinning the benchmark to a snapshot rather than to
the source. Worth its own ADR when the work starts.
