# ADR-0157: A scraper's job URL is declared once, not authored three times

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** [ADR-0023](0023-canonical-board-identity-for-eviction-and-prune.md) (`board_key()`, which `job_id` composes with), [ADR-0139](0139-a-single-source-board-is-its-own-ats.md) (the `ats`/`slug` shape every scraper still shares)

## Context

A Job's apply URL, and the composite id built alongside it, were each authored independently in
three places that cannot see each other:

1. Every one of the 35 `src/headstart/scrapers/*.py` modules built its URL as a literal f-string
   inline in `parse()` (or a same-shaped module-level helper), and 31 of the 35 built the id as
   the literal `f"{self.ats}:{self.slug}:{native_id}"`. The other four (`workday`, `personio`,
   `taleo_be`, `taleo_enterprise`) already override `board_key()` for reasons specific to how
   that ATS names a Board, but still hand-wrote the id concatenation separately from it.
2. `src/headstart/search.py`'s `_rehost_recruitee`/`_canonical_url` repair two of those same
   URLs again, at serve time, because the scraper's own construction is sometimes wrong on
   already-stored rows (a pre-fix Darwinbox link, a Recruitee row on a dead vanity domain).
3. `scripts/eval/verify_filters.py`'s `URL_SHAPES` re-derived each ATS's shape a third time, by
   hand, from reading the scraper's source — with comments literally quoting the f-string it was
   copied from.

Three independent authors of the same fact drift. It already had: the `URL_SHAPES` file's own
comments record that its `recruitee` entry was host-agnostic and passed 458 dead rows before
being tightened, and its `oracle` entry once asserted `\d+` for ids that are not always numeric,
both "written from the scraper's source" and both wrong until someone noticed by hand. The
coverage gate `verify_filters.py` already has (`atses_without_shape`) only catches a *missing*
entry — a new ATS shipping with no shape at all — never a *wrong* one, which is exactly the shape
both past incidents took.

## Decision

`BaseScraper` (`src/headstart/scrapers/base.py`) gains three members every scraper now goes
through instead of hand-writing its own copy:

- **`job_id(self, native_id) -> str`** — concrete, not overridden by anyone: `f"{self.board_key()}:{native_id}"`.
  This alone collapses the 31-scraper literal *and* the four `board_key()`-deviating ones onto
  one formula, because the deviation already lives in `board_key()` — `job_id` only ever appends
  `:{native_id}` to whatever that returns. Workday's override composing `{ats}:{company}/{site}`
  and Taleo BE/Enterprise's canonicalized key all produce the exact same string as before, now
  through one call site instead of a hand-copied one per scraper.
- **`job_url(self, native_id) -> str`** — `@abstractmethod`, the same shape `_salary_field`
  already uses in this file: every concrete scraper must state, in one place, how it gets a
  Job's URL. The signature is deliberately not fixed to a bare id — a handful of ATSes need more
  (a title slug, a relative path the API already supplies, the whole raw record), and those
  override with whatever they actually need, the same informal default-here-override-there
  latitude `_detail_url` already had per scraper before this. There is no generic caller that
  invokes `job_url` across ATSes with one shared argument list — each scraper's own `parse()` is
  the only caller of its own `job_url` — so a fixed signature would buy nothing and cost real
  scrapers (Apple, Workday, Zoho, Tesla, …) an awkward indirection to force their real inputs
  through one shape.
- **`url_shape: str`** — a bare class-level declaration (no default, mirroring `ats: str`), the
  regex a scraper's own `job_url` output must always match.

Every one of the 35 scraper files was migrated: the literal id f-string is now `self.job_id(...)`,
the URL construction now lives behind `self.job_url(...)` (or, where an ATS's API supplies the
link directly — Ashby, Greenhouse, Lever, Workable, Teamtailor, Meta, TaleoBE — a `job_url` that
declares that pass-through rather than building anything), and each scraper's `url_shape` carries
the exact regex `verify_filters.py` used to hand-maintain, with its original measurement comment
moved onto the class attribute it now documents. Three scrapers whose fetch URL and served URL
happened to be byte-identical (`jazzhr`, `jobvite`, `trakstar`'s HTML-card path) had their
`_detail_url` renamed to `job_url` outright rather than gaining a second, parallel method: Zoho
and Workday's fetch and served URLs differ on purpose (a title slug and query param; a resolved
vs. slug-pinned host) and keep two separate methods, documented as deliberately different.

`scripts/eval/verify_filters.py`'s `URL_SHAPES` is now *generated*:

```python
URL_SHAPES: dict[str, str] = {ats: cls.url_shape for ats, cls in SCRAPERS.items()} | {
    "wellfound": r"https://wellfound\.com/jobs/\d+(-[\w-]+)?"
}
```

`wellfound` is the one entry that stays manual: it is built by the standalone `run_wellfound*.py`
scripts, never through `headstart.scrapers`, so there is no `BaseScraper` subclass for it to
declare a shape on. Every other entry now has exactly one possible source — a wrong shape can no
longer be typed independently of the scraper, because there is nowhere left to type it. A new
`tests/test_base.py` check (`test_every_scraper_declares_a_compilable_url_shape`) fails the suite
if any registered scraper's `url_shape` is missing or does not compile, closing the coverage
gate's blind spot one step earlier than a live-Space run would.

**Not done here: a live run of `scripts/eval/verify_filters.py` against the deployed Space**,
which CLAUDE.md's "verify against the live API" rule would otherwise ask for on a change whose
whole subject is per-ATS URL shapes. Skipped deliberately for time, not silently: every regex
migrated onto a `url_shape` attribute is byte-identical to what `URL_SHAPES` already asserted
(diffed programmatically against `git show HEAD^:scripts/eval/verify_filters.py`, zero
differences across all 36 entries), so this change carries no new *shape* claim to verify live —
the live-verification debt this leaves is the harness's existing one, not a new one this ADR
introduces.

`search.py`'s two serve-time repairs (`_rehost_recruitee`, `_canonical_url`) **stay in
`search.py`, unchanged in mechanism** — they still hardcode `"darwinbox"`/`"recruitee"` rather
than becoming a `canonical_url` hook on the two scraper classes. `search.py` is deployed to the
HF Space as a flat standalone file (`deploy-space.yml` copies only `search.py`, `geo.py`, `fx.py`
and a short named list of siblings; `headstart.scrapers` is never among them, and it pulls in
`curl_cffi` and the whole network-fetch stack the served app has no business importing). Routing
the repair through `registry.SCRAPERS[ats].canonical_url(...)` at request time would either break
the deployed Space's import graph or force shipping 35 scraper modules into an app that serves
LanceDB rows and calls no ATS. What the ADR *does* close is the silent-drift risk: a new
repo-side test, `tests/test_search.py::test_canonical_url_rewrites_match_the_scrapers_own_url_shape`,
runs a synthetic pre-fix URL for each ATS `_canonical_url` special-cases through the repair and
asserts the output matches that scraper's own `url_shape` — the drift the runtime hook would have
caught structurally is instead caught in CI, in the repo, where both modules are importable and
the deployed module's own constraints don't apply.

### Alternatives considered

- **A `canonical_url` hook on `BaseScraper`, called from `search.py` via the registry.** Rejected
  for the deployment reason above — it is not that the design is wrong, it is that this repo has
  exactly one module that must not import `headstart.scrapers`, and `search.py` is it.
  `_canonical_url` (and its two hardcoded ATS names) is the entire coupling; a hook would remove
  two `if ats == "..."` lines and add a whole scraper-package dependency to do it — not a good
  trade for what is, per both TEMPORARY comments already on that code, a stopgap fix scheduled
  for deletion anyway.
- **A fixed `job_url(self, native_id: str) -> str` signature everywhere**, matching the task's
  own example. Rejected once the survey of all 35 scrapers found the inputs genuinely vary: Apple
  needs `positionId` + a title, Zoho needs an id + a title, Workday needs a relative
  `externalPath`, Tesla needs an id + a title to slugify, Zwayam needs a per-Board `link_base`
  resolved by its own network fetch plus the vendor's raw slug, and several ATSes (Ashby,
  Greenhouse, Lever, …) hand back the URL directly with no id at all. Forcing one shape onto all
  of them would have meant smuggling extra state through `self` that `parse()` already has ready
  to hand, for no reader benefit — the override docstring says what it needs, the same as every
  other per-scraper method already does in this codebase (`_detail_url`, `slug_from`, `board_key`).

## Consequences

- **A wrong `url_shape` is now a one-place typo, not a hand-copied guess.** The oracle `\d+` and
  recruitee host-agnostic incidents this ADR opens with were both already fixed in
  `verify_filters.py` before this change (their comments record the fix and the measurement
  behind it); migrating them onto their scraper's `url_shape` carried the *already-correct*
  regex over unchanged — this change closes the mechanism that let them go wrong in the first
  place, not a shape that is wrong today.
- **35 files touched, each mechanically** — a `job_id`/`job_url` call swapped in for a literal,
  `url_shape` added, in every case preserving the exact string a scraper already produced.
  No scraper's actual served URL changes as a result of this ADR; where a genuine defect
  surfaced during the audit (none did — see the oracle note above) it would have been called out
  explicitly rather than folded into the mechanical pass.
- **`_detail_url` survives, on purpose, wherever a scraper's fetch route and served route
  genuinely differ** (Zoho's title-slug-free API path vs. its titled served link; Workday's
  resolved-instance fetch vs. its slug-pinned served link; SmartRecruiters' `api.` host vs. its
  `jobs.` one). `job_url` is not "the one URL method" — it is specifically the *served* one;
  collapsing a fetch URL into it too would have been the wrong kind of unification, conflating
  two things that are correctly different.
- **The three-copy trakstar case (`_detail_url`, one inline `parse()` copy, `_jobs_from_feed`'s
  own copy) is now one module-level `_job_url(slug, code)` plus a thin `TrakstarScraper.job_url`
  delegate** — `_jobs_from_feed` stays a free function (it is tested directly with no live
  scraper instance, per its own docstring), so it calls the module helper rather than `self`.
- **CI now structurally enforces `URL_SHAPES` coverage one step earlier than before.** The
  existing live-Space harness (`verify-search-filters` skill) still catches a shape that is
  *wrong against real served data*; `test_every_scraper_declares_a_compilable_url_shape` catches
  a shape that is *missing or malformed* without needing a deployment at all.

## Amendment (2026-09-23): Workday's served link follows the resolved pod

Workday's fetch and served URLs no longer differ. The slug-pinned link was preserved behaviour, not
a measured choice, and it is dead for a migrated tenant: `board_key` is pod-blind, so the ledger row
that survives dedupe can name a retired `wdN`. Measured live 2026-09-22/23 on netflix (wd1→wd108),
otis (wd5→wd504), verisure (wd3→wd502) and cmu (wd5→wd115): the stale pod's CXS 422s and its job
pages 500 with no JSON-LD, while the resolved pod serves them 200 with a `JobPosting` (8 of 8
postings across the four; netflix and otis re-checked through the real scraper, 4 of 4). A
non-migrated control (3m, wd1) is unchanged. `WorkdayScraper.job_url` now builds on
the instance `_resolve_instance` found, which makes it the same URL as the public-page detail
fallback, so `_page_url` is folded into it.
