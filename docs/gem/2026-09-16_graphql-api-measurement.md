# Gem (jobs.gem.com) GraphQL API measurement

Everything below was measured live against `https://jobs.gem.com/api/public/graphql/batch` on
2026-09-16, using the 496-company seed CSV published alongside kalil0321/ats-scrapers'
`gem.py` (`https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/gem.csv`)
and a real, already-scraped sample of 3,542 Gem jobs
(`experiment/ats-scraper-candidates/artifacts/parquet/gem.parquet`). Every claim the upstream
scraper makes was re-verified against the live API rather than trusted — this repo's own
precedent (phenom.py, icims.py) is that an adapted scraper's assumptions are checked, not ported.

## Endpoint and operations

One host, one endpoint: `POST https://jobs.gem.com/api/public/graphql/batch`, taking a JSON array
of `{operationName, variables, query}` objects (a GraphQL batch request). Two operations are used:

- `JobBoardList(boardId)` — lists a board's open postings (id, extId, title, locations,
  department, locationType, employmentType).
- `ExternalJobPostingQuery(boardId, extId)` — one posting's detail (descriptionHtml,
  firstPublishedTsSec, locations, job, compensationHtml).

**No CSRF, session cookie, or Referer needed.** Every probe in this measurement pass — over 2,500
requests across listing, detail, batch-size, and concurrency tests — used a bare POST with no
prior GET, no cookie jar, and no `Origin`/`Referer` header, and got a full 200 response including
the very first request of a fresh process.

## Pagination: none exists, and none was needed

The `JobBoardList` GraphQL query takes no offset/limit/cursor argument — there is nothing to page
through. Swept all 496 seed slugs (43.2 req/s, conc 16, zero non-200s) and found 377 boards with at
least one open job; the largest, `coupa-software-inc-ats-1`, listed exactly 300 postings — matching
the third-party pre-scraped sample's own count for that tenant exactly, with no truncation.

Unlike Phenom's `totalHits`, there is no separate "total" field on this endpoint to compare a read
against, so a cap above what has been observed here (300) would be **undetectable** from the API
alone. Nothing measured up to 300 justifies building speculative windowing/pagination handling for
an unobserved failure mode.

## Live board vs. nonexistent board: the listing alone cannot tell them apart

A deliberately fabricated slug (`this-slug-definitely-does-not-exist-xyz123`) returns:

```json
{"data": {"oatsExternalJobPostings": {"jobPostings": []}}}
```

HTTP 200 — the *identical* shape a real board with zero currently-open roles returns. Of the 119
seed slugs the API reported zero jobs for, the board page (`jobs.gem.com/{slug}`) distinguishes
them cleanly: **104 (87%) answer 200** (a real, live company simply hiring nothing right now — the
same shape this repo already accepts for freshteam/keka boards at `jobs=0`) and **15 (13%) answer
404** (a dead or mistyped tenant — several are well-known company names, e.g. `genspark`, `dydx`,
`ondo-finance`, suggesting a migrated ATS rather than a typo). The liveness probe (`p_gem` in
`scripts/validate/check_liveness.py`) therefore checks the board page first and only trusts the
API's job count once the page has confirmed the tenant is real.

## Detail batch size: upstream's 20 is arbitrary, not measured

kalil0321/ats-scrapers' own comment calls `DETAIL_BATCH_SIZE = 20` "conservative" without citing a
measured limit. Packing N `ExternalJobPostingQuery` operations into one POST against a real
300-posting board:

| N ops | result |
|---|---|
| 20 / 25 / 50 / 100 / 200 / 300 / 500 / 1,000 | all succeed, 200, full data |
| 1,000 | 8.0s, zero errors |
| 2,000 | **fails — HTTP 500** |

`_DETAIL_BATCH_SIZE = 100` ships — five times upstream's batch size, comfortably under the measured
wall, and enough that even the largest observed board (300 postings) needs only three detail
requests total rather than one per job.

## Rate limit: none found up to concurrency 128

500+ listing requests and 2,500+ detail requests were sent across this pass at concurrency
8/16/32/64/128 against a mix of live tenants sharing the one host:

| concurrency | throughput | p50 | p95 | non-200s |
|---|---|---|---|---|
| 8 | 18-19 req/s | 0.35s | 0.74-0.76s | 0 |
| 16 | 42-44 req/s | 0.33-0.34s | 0.57s | 0 |
| 32 | 70-72 req/s | 0.35s | 0.65-0.67s | 0 |
| 64 | 99-118 req/s | 0.38-0.43s | 0.76-0.77s | 0 |
| 128 | 159 req/s | 0.48s | 0.85s | 0 |

No `Retry-After` header was ever seen. `detail_workers = 16` ships anyway — batching already cuts
request volume roughly 100x versus a per-job-fetch scraper, so there is nothing to gain by chasing
a wall that was never found.

## Field mapping decisions

### `posted_at`: `firstPublishedTsSec`, not `startDateTs`

Upstream prefers `firstPublishedTsSec` and falls back to `startDateTs`. Sampled 159 detail records
across a spread of boards: `firstPublishedTsSec` was populated on **159/159**; `startDateTs` on
**0/159**. `startDateTs` is documented upstream as a *future* go-live timestamp — a job scheduled
to go live later is, by definition, a job the public `JobBoardList` query cannot see yet, so the
field is structurally unreachable through this scraper, not merely rare. `startDateTs` is not
requested by the trimmed detail query gem.py ships.

### `remote`: `job.locationType`, not the per-location `isRemote` flag

Gem exposes two remote signals. Measured across all 3,533 postings on every live seed board:
`job.locationType` (`REMOTE`/`HYBRID`/`IN_OFFICE`) was populated on 100% of postings.
`locations[].isRemote` **disagreed with it on 318 (9.0%)** — almost always a `locationType: REMOTE`
posting whose location still states `isRemote: false` (e.g. a real posting titled "Agora - Full
Stack Engineer (Remote)" whose location carries `isRemote: false`). Titles confirm `locationType`
is the field telling the truth. `_remote()` reads `locationType` first (`REMOTE`→True,
`HYBRID`→None, `IN_OFFICE`→False, matching `phenom._remote`'s hybrid-is-None convention) and falls
back to `isRemote` only if `locationType` is itself absent — never observed in this sample, but not
guaranteed by the schema.

### `location`: first location's `name`, falling back to `city`/`isoCountry`

Locations is a list; `name` is Gem's own rendered string ("San Francisco (On-Site)", "Remote -
USA", "Toronto (Remote)") and is usually richer than the raw `city`/`isoCountry` pair, so it is
tried first. Only the first location is used for multi-location postings, matching upstream's own
choice — real multi-location postings were a small minority of the sample.

### `employment_type`: passed through raw, no normalization

`Job.employment_type`'s own docstring says values are "kept as the provider phrases them" — so
Gem's raw `FULL_TIME`/`PART_TIME`/`CONTRACT`/`TEMPORARY`/`INTERN` strings are emitted unchanged.
Upstream's `_EMPLOYMENT_TYPE_MAP` normalization table is not ported: it exists to homogenize across
ATSes, which this repo's model deliberately does not do.

### Every job's detail is fetched — no ADR-0048 skip

Phenom can skip a Job whose description the store already holds, because every *other* field it
emits also comes from its listing. Gem cannot make the same claim: `posted_at` and
`compensationHtml` are **detail-only** (absent from `JobBoardList` entirely, confirmed by the query
definitions above) — skipping the detail fetch for an already-seen Job would silently null those
two fields on every run after the first. `fetch_raw()` therefore does not call `needs_detail()` at
all. The cost is small: batching means a 300-posting board (the largest seen) still costs only
three detail requests, skip or no skip.

## Salary: `compensationHtml`

Sampled 899 postings spread across all 377 live seed boards with any open jobs. 136 (15.1%) carry a
non-empty `compensationHtml`, spanning 72 distinct companies.

**132 of 136 (97%) fit one machine-generated template**, even when it sits inside a longer prose
paragraph the employer wrote around it:

> "The base pay range for this role is $X – $Y per YEAR|HOUR|MONTH."

Real observed currency notations: `$`, `CA$`/`C$` (Canadian), `A$` (Australian), `€`, `£`, `₹`, plus
a trailing explicit ISO code variant ("$200,000 – $350,000 USD"). `_field_generic` (the existing
Tier-1 fallback) **cannot** read this shape: its `_RANGE` regex requires the second number to start
immediately after the separator, and Gem states a currency symbol in front of *each* side
("$80,000 – $120,000"), so `_RANGE.search` never matches and `_field_generic` silently falls
through to `_SINGLE_NUM` — keeping the floor ($80,000) and discarding the ceiling entirely. This
was the concrete, measured reason a dedicated Tier-1 parser (`salary._field_gem`) was built rather
than leaving Gem on the generic fallback.

`_field_gem` also had to widen past a dash: "$180,000 to $210,000" (Thunder) uses the word "to" as
its separator, not a dash — caught during validation when the first draft silently dropped
$210,000 the same way `_field_generic` does.

Currency symbols are mapped explicitly rather than guessed: the shared Tier-2 `_guess_currency`
reads any multi-character `$`-ending symbol as CAD, which is right for that function's own,
narrower observed evidence but would be **wrong** here — `A$125,000 – A$175,000` is a real,
repeated AUD figure in this corpus.

The 4/136 declines are not a parser gap: each states a figure that reads as implausible if
annualized (e.g. "$100 – $200 per year") — almost certainly a tenant data-entry error (meant
hourly) — and the shared plausibility bounds correctly reject rather than silently mis-annualize
them, matching this module's no-fabrication policy.

Full validation (132/136 correctness, not just coverage) is in the PR's own test additions to
`tests/test_salary.py`; the false-positive check (an unrelated dollar figure earlier in a prose
paragraph — "Series A+ ($10M - $20M raised)" — must not be picked up as the salary) is pinned there
too.

## Company name: board page renders server-side

Sampled 60 live board pages (`jobs.gem.com/{slug}`) with a bare GET — real server-rendered HTML, no
JS wall. ~95% of titles follow `"{Name} Careers"` (case varies: `a16z speedrun careers`, `Accel
Careers`), the exact wrapper `company_name.py` already uses for eightfold/jobvite/keka, so
`PATTERNS["gem"]` reuses `_CAREERS_WRAPPER` rather than a new pattern. Running the real
`company_name.from_title()` against the full 57-title sample (3 boards refused the request
entirely) yields a name for **36/57 (63%)** — the gap between "matches the wrapper" (~95%) and
"yields a name" is almost entirely the existing hostname guard correctly declining tenants whose
brand *is* their domain (`agenta.ai Careers`, `11x.ai Careers`, `basalt.health Careers` — common
among early-stage Gem customers).

## Discovery and liveness — final numbers

- Seed: 496 companies (`kalil0321/ats-scrapers`' curated CSV).
- Wayback (`wayback_pages.py gem`, one host, 4 CDX pages): **+1,572 tenants** (before dedup against
  the seed).
- Common Crawl (`CC_ONLY_ATS=gem cc_miner.py`, crawl `CC-MAIN-2026-34`): **+271 tenants**.
- Merged pool (`data/ats-tenants-merged/gem.csv`, union, deduped by slug): **1,580 tenants**.
- Liveness (`scripts/validate/check_liveness.py gem`, `data/validate/liveness/gem.csv`):
  **1,019 live**, 561 dead, 0 unknown. **601 Hiring Boards** (`jobs >= 1`), **4,450 jobs** summed
  across every live board (a point-in-time count, not the corpus this run would actually scrape,
  since many of the 1,019 live boards report 0 jobs right now — see the live-vs-empty finding
  above).

No second/alias host was found for Gem: every one of the 3,542 sampled real postings and all 496
seed rows resolve to `jobs.gem.com`; `wayback_feeder.ATS_HOSTS["gem"]` and
`cc_miner.ATS_PATTERNS["gem"]` both sweep that one host only.
