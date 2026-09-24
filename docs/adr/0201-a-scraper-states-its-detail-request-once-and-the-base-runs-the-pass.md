# ADR-0201: A Scraper states its detail request once, and the base runs the Detail pass

**Status:** accepted · **Date:** 2026-09-24 · **Takes up:**
[ADR-0003](0003-fan-out-detail-fetch.md) (whose Consequences deferred "folding in the per-item GET
guard and removing the side-channel": the GET guard is folded in here; the `_detail` side-channel
goes Scraper by Scraper where its `parse` can read the returned mapping instead) · **Keeps:**
[ADR-0015](0015-async-multiplexed-fan-out.md),
[ADR-0016](0016-async-fan-out-default.md), [ADR-0048](0048-skip-details-we-already-hold.md),
[ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md),
[ADR-0167](0167-a-scraper-may-decline-the-multiplexed-path.md) exactly as decided

## Context

A **Detail pass** is composed in each Scraper by hand from seven `BaseScraper` hooks —
`tech_detail_wanted`, `needs_detail`, `async_fanout_enabled`, `fan_out` or `fan_out_async`, the
`note_detail_*` labels, `report_detail_gaps`, and one of five pairing idioms — over two
transports. Mapped across `src/headstart/scrapers/` on 2026-09-24 (26 Scrapers with
`has_detail_pass`, 27 call sites):

- **22 Scrapers write every detail request twice**, a sync function for the thread pool and an
  async twin for the multiplexed session, ~540 lines between them. One pair has drifted in
  behaviour: eightfold sends its `Referer` on the sync path only. Two more only look like it.
  adp's async pacer takes no `tries` or `**kwargs`, but no caller passes either to it (the one
  that passes them, `resolve_company`, is sync by design). workday's two paths clear different
  cookie jars on a 400, but each clears the jar its own transport rides — the thread's pooled
  session, or the pass's `AsyncSession`.
- **Pairing results back to items is done five ways** — a dict-zip (11 sites), `dict(zip)` keeping
  Nones (2), positional zips (7), in-place mutation (3) and `attach_details` (3).
- **8 Scrapers leave a parse failure unlabelled**: they catch only `http.RequestsError`, so a
  `json.loads` that raises falls through to `fan_out`'s catch-all and reaches the gap line as
  `unlabelled`, the exact blindness ADR-0088 exists to prevent.
- **Every Detail-pass policy change is a sweep.** The tech gate (#509, 10 files), the move onto
  `_fetch`/`_fetch_async` (#458, 20 files) and the description recovery (#480, 15 files) each
  touched every Scraper that composes the pass, because the pass has no single home.
- `fan_out` records no `fanout_stats`, so apple re-added them by hand (`_timed_details`) to keep
  the line ADR-0167's transport decision was read from.

## Decision

A Scraper states **what** to fetch and **how to read it**; `BaseScraper.run_detail_pass` owns
everything else.

- **`detail_request(item) -> DetailRequest`** — the request as data: `url`, `method`, `headers`
  (default: the headers `_get` sends, `DEFAULT_REQUEST_HEADERS`), `timeout` (30) and `options`
  for any further fetch keyword (`json=`, `data=`, `allow_redirects=`, `retry_on=`,
  `marks_wall=`). Raise
  `DetailLost(cause)` when no request can be formed; raised there it is counted unattempted.
- **`read_detail(item, response)`** — the detail out of a 200, or raise `DetailLost(cause)`.
  Only ever handed a 200: any other status is a loss labelled `HTTP {status}`, where a Scraper
  calling `raise_for_status` used to let a non-200 2xx through to its reader.
- **`run_detail_pass(items, *, key_of, what, title_of=None, department_of=None,
  skip_held=False, concurrency=None) -> FetchedDetails`** — arms the ADR-0166 gate when
  `title_of` is given, the ADR-0048 skip when `skip_held` is, sends each request on the
  multiplexed path unless `async_fanout_enabled()` says otherwise, labels every loss (a non-200
  as `HTTP {status}`, a transport exception or unexpected read error through `classify_exception`,
  a `DetailLost` by its cause), reports one gap line, and returns the details keyed by native id with
  `.missing` for a load-bearing pass to truncate on.
- **`fetch_detail(item)`** — the per-item step on the thread transport, public for samplers
  (`scripts/enrich/salary_sample.py`) that fetch a handful of details without a whole pass.
- The thread transport records `fanout_stats` itself (with its own lock), so every Board on it
  gains the `concurrency {ats} details @N` line only apple had; apple's hand-written copy goes when
  apple moves.
- `concurrency=` pins the multiplexed width over every other source, for the one host whose
  politeness bound must not be widened even by the operator (trakstar under DataDome, ADR-0016).

The primitives stay public — `fan_out`, `fan_out_async`, `tech_detail_wanted`, `needs_detail`,
`report_detail_gaps`, `attach_details` — for listing-page fan-outs (amazon, google, workday) and
for the Detail passes this does not fit. Those are named in each module rather than forced
through: **workday** (an in-pass circuit breaker, in-item fallback chains, recovered outcomes and
its own loss line, ADR-0088), **adp** (a process-wide pacer whose 429 rest spans attempts),
**cornerstone** (a shared auth token refreshed on 401 across items) and **gem** (batched
requests, so the fan-out unit is not the Job).

The Scrapers move in waves so each change stays reviewable: this change lands the seam with
pyjamahr, smartrecruiters and clearcompany (one per twin shape), and the rest follow in their
own changes. Four parts of the interface have no caller among those three and land now anyway,
each with a named first caller in the next wave, because the waves run in parallel and would
otherwise each re-add them to `base.py` at once: `skip_held` (apple, phenom, zwayam, eightfold),
`concurrency=` (trakstar), `DetailRequest.method`/`options` (phenom and zwayam POST a body) and
`FetchedDetails.missing` (icims, jobvite, meta and eightfold's sitemap pass truncate on it).

## Consequences

- A migrated Scraper has one request description, so transport drift is impossible by
  construction, and its tests inject one `tests/fake_fetcher.py` fake through the constructor
  instead of patching `_get`, `_fetch` or `fan_out_async` — the private seams its old tests had
  to reach past.
- A migrated Scraper's losses are all named. An unexpected read error is labelled by its type
  (`JSONDecodeError`) where it used to be `unlabelled`.
- `report_detail_gaps` now always runs, so a pass over zero items writes zeroed detail telemetry
  where a few Scrapers used to skip the call. The gap line's format is unchanged.
- A listing row with no native id is now a counted, labelled loss (`no posting id`, `no job id`)
  where pyjamahr used to drop it before the pass without a word.
- Measured live before merging, on the change's final code: the three migrated Scrapers on three
  real Boards each (235 Jobs) read the same Jobs from `origin/main` and from this change. Every
  differing value is a pyjamahr `posted_at` naming the same instant with a different UTC offset —
  11 on one pair of runs, 60 on the next, 0 of them a different instant — and the baseline code
  alone returns `-05:00` and then `+05:30` for one posting on consecutive calls, so the API
  varies the offset, not this change.

## Amendment, 2026-09-24: a third `read_detail` outcome

ADR-0201's second wave (#631) found details that arrive without their description yet carry fields `parse`
reads — a Taleo BE layout states a location and department on 128 of 128 pages with no body — and
three Scrapers each overrode `report_detail_gaps` to keep such a detail while counting it as a gap.
`read_detail` may now return `DetailWithoutDescription(fields, cause)`: `run_detail_pass` labels
`cause`, counts the Job in the gap line and `FetchedDetails.missing`, and keeps `fields` in the
mapping; `fetch_detail` returns `fields`. The three overrides are gone.
