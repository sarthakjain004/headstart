# ADR-0195: A Scraper states its detail request once, and the base runs the Detail pass

**Status:** accepted · **Date:** 2026-09-24 · **Completes:**
[ADR-0003](0003-fan-out-detail-fetch.md) (whose Consequences deferred "folding in the per-item GET
guard and removing the side-channel") · **Keeps:** [ADR-0015](0015-async-multiplexed-fan-out.md),
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
  async twin for the multiplexed session, ~540 lines between them. Three pairs had already
  drifted: eightfold sent its `Referer` on the sync path only, workday's sync path cleared the
  process-global cookie jar where its async path cleared the pass's own session, and adp's async
  pacer dropped `tries` and `**kwargs`.
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
  for any further fetch keyword (`json=`, `data=`, `retry_on=`, `marks_wall=`). Raise
  `DetailUnattempted(cause)` when no request can be formed.
- **`read_detail(item, response)`** — the detail out of a 200, or raise `DetailLost(cause)`.
  Only ever handed a 200.
- **`run_detail_pass(items, *, key_of, what, title_of=None, department_of=None,
  skip_held=False, concurrency=None) -> FetchedDetails`** — arms the ADR-0166 gate when
  `title_of` is given, the ADR-0048 skip when `skip_held` is, sends each request on the
  multiplexed path unless `async_fanout_enabled()` says otherwise, labels every loss (transport
  exception and non-200 through `classify_exception`, `DetailLost` by its cause, an unexpected
  read error by its type), reports one gap line, and returns the details keyed by native id with
  `.missing` for a load-bearing pass to truncate on.
- **`fetch_detail(item)`** — the per-item step on the thread transport, public for samplers
  (`scripts/enrich/salary_sample.py`) that fetch a handful of details without a whole pass.
- The thread transport records `fanout_stats` itself (with its own lock), so a Board on it keeps
  its `concurrency {ats} details @N` line and apple's hand-written copy can go.
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
own changes.

## Consequences

- A migrated Scraper has one request description, so transport drift is impossible by
  construction, and its tests inject one `tests/fake_fetcher.py` fake through the constructor
  instead of patching `_get`, `_fetch` or `fan_out_async` — the private seams its old tests had
  to reach past.
- A migrated Scraper's losses are all named. An unexpected read error is labelled by its type
  (`JSONDecodeError`) where it used to be `unlabelled`.
- `report_detail_gaps` now always runs, so a pass over zero items writes zeroed detail telemetry
  where a few Scrapers used to skip the call. The gap line itself is unchanged.
- Measured live before merging: the three migrated Scrapers on three real Boards each (235 Jobs)
  read byte-identical Jobs from `origin/main` and from this change, except 11 pyjamahr
  `posted_at` values. Each of those is the same instant with a different UTC offset, and the
  baseline code itself returns `-05:00` and then `+05:30` for one posting on consecutive calls,
  so the API varies the offset, not this change.
