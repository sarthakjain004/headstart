# ADR-0172: A Single source scraper declares its company; the ledger cannot

**Status:** accepted · **Date:** 2026-09-21 · **Amends:**
[ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (whose
Context opens "`BaseScraper.__init__` has always done `self.company = company or slug`" — it now
does `self.COMPANY or company or slug`, so a declared name outranks a caller-supplied one) ·
**Relates to:** [ADR-0139](0139-a-single-source-board-is-its-own-ats.md) (the eight Boards this applies to),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the eviction this design exists to avoid)

## Context

A Single source scraper's Board displayed its careers **host** as its company: all 9,281 of
Amazon's served tech rows read `www.amazon.jobs`, and `apple`, `google`, `tiktok` and `bytedance`
the same — **17,587 served tech rows** between them (`filter_tech`'s per-ATS kept figures, run
35595828212).

The mechanism is in `load_active_companies`, which builds
`CompanyRef(slug=scraper.slug_from(tenant, url), name=tenant)`. **`name` is the raw `tenant`
column**, whatever it happens to be, while `slug` goes through `slug_from`. So a Board recorded by
hostname displays a hostname, and whether one read as a company name was decided by whoever typed
the ledger row.

**Renaming the tenant is not available.** `slug_from` defaults to returning the tenant, and those
five do not override it, so a tenant of `Amazon` makes the slug `Amazon`, changes every `job_id`
(`{ats}:{slug}:{native_id}`), and the next `index prune` evicts the Board's whole population as
off-Board (ADR-0023). `meta` and `tesla` could hold a name in that column only because they *do*
override `slug_from` to read the host out of `url` — which is why they looked correct and the
other six did not.

The repo had already reached for this three times, inconsistently: `uber` with
`super().__init__(slug, "Uber")`, and `apple`/`bytedance` with `super().__init__(slug, company or
"Apple")` — the latter dead on arrival, because the pipeline *always* passes `CompanyRef.name`, so
the hostname won every time.

## Decision

`BaseScraper.COMPANY`, a class attribute, consulted first: `self.company = self.COMPANY or company
or slug`. Declared on all eight Single source scrapers so the set no longer depends on a ledger
spelling. `None` everywhere else, so no multi-tenant ATS changes behaviour.

A declared name needs no request. That is the argument `meta` already made in declining a
`company_name` pattern: `resolve_company()` spends a page fetch per Board and buys nothing where
there is exactly one, known company.

**A caller-supplied name no longer wins.** That inverts ADR-0114's precedence for any scraper
setting `COMPANY`, and it is the point rather than a side effect: the only caller that passes one
is the pipeline, passing `CompanyRef.name`, which for these Boards *is* the hostname.

## Alternatives considered

- **Fix the ledger's `tenant` column.** Rejected: it is the slug for five of the eight, so it
  renames every `job_id` and evicts ~17.6k rows (ADR-0023).
- **Override `slug_from` on the five, then rename the tenant.** Rejected: it moves the same
  display concern into the identity derivation, and any future re-discovery writing a hostname
  reintroduces the bug. `COMPANY` cannot be reintroduced by a ledger edit.
- **Copy `uber`'s `__init__` idiom five more times.** Rejected on duplication: five near-identical
  overrides each carrying the same three-line comment, which is how `apple` and `bytedance` came to
  carry a *wrong* version of it.
- **Give the five a `company_name.PATTERNS` entry.** Rejected: a page fetch per run for a name
  known at authorship time, and ADR-0114's own machinery is for Boards whose name we must discover.

## Consequences

- 17,587 served tech rows change `company` from a hostname to a name on the next run. `company` is
  display and a substring search filter (`lower(company) LIKE …`); `board_key` is unchanged, so no
  id moves and nothing is evicted.
- `ingest/hot_boards.display_name()` exists partly to rescue exactly these rows and no longer has
  to for the eight; its `www.amazon.jobs` example is updated. `board_operator.classify` returns
  `employer` under both spellings — measured, unchanged.
- **This fixes eight Boards, not the problem.** 62,246 of 78,083 Scrapable Boards (79.7%) still
  have `name == slug`, and `looks_like_slug(name)` holds for 93.7%. Closing that needs a per-ATS
  derivation (extending `company_name.PATTERNS`) and is not this decision.
- A single-source scraper added later that forgets `COMPANY` serves its host again. The regression
  test asserts the *declaration* on the class, not just the resulting value, because asserting the
  value passes vacuously wherever the ledger tenant already spells the name — which is how the
  first version of this change shipped `COMPANY` onto `TeslaBrowserUnavailable` with every test
  green.
