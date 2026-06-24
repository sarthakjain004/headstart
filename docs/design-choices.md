# Design choices

A log of non-obvious design decisions — the option picked, the ones rejected, and why — so the
reasoning survives past the commit that made it. Append new entries at the top.

---

## Full-harvest scrape: stream + dedup-by-id, parallelism via the company pool (2026-06-24)

**Context.** Scraping the liveness-validated Active lists is ~63k boards / ~3.3M Jobs. The old
`scrape_all` retained every Job in a `dict[str, Job]` (for dedup + the combined `docs/jobs.json`)
and `build_feed` copied them again — at harvest scale that OOMs near the end, after hours, losing
everything. The user also wants it "parallel a shit ton."

**Memory.** Decision: when streaming to `data/jobs/`, keep only a **`set[str]` of seen job ids**,
write each board's fresh Jobs to `{ats}.jsonl`, and **discard the Job objects**. The combined feed
becomes opt-in (`collect_feed`, default True for small runs; `__main__` forces it off for the
Active-list harvest — a 3.3M-Job `docs/jobs.json` would be gigabytes and is the OOM). The id set
(~hundreds of MB of strings, not GB of objects) also collapses the **duplicate slug forms** the
liveness data carries (e.g. `dollartree` appears under three slugs → same ids → written once).

**Parallelism — the knob is the company pool, not processes.** The work is network-bound and
`curl_cffi` releases the GIL per request, so threads already use every core; multiprocessing
wouldn't help and can't share the writer handles. Company-pool size defaults to `cores*4` capped
at 64, overridable with `HEADSTART_WORKERS=N`.

**Why per-board detail pools stay (rejected: one shared detail pool).** The 5 detail-fetching
scrapers each fan out their *own* bounded pool (`fan_out`, 8 workers; trakstar 4) per board. A
single shared global pool would bound total threads more tightly, but it would let many requests
land on **one** host at once (e.g. one `{slug}.hire.trakstar.com`) and trip its DataDome / rate
limit — per-board pools keep per-host concurrency polite. The accepted cost: peak in-flight
requests ≈ `workers + workers*8`, so raising `HEADSTART_WORKERS` multiplies by the detail width.

**Resume.** A multi-hour harvest that crashes shouldn't restart from scratch. `JobWriter` keeps a
`.done` journal of completed board keys (`ats:slug`), flushed per board; `HEADSTART_RESUME=1`
opens the `.jsonl` files for append, loads the journal, and `scrape_all` skips boards already in
it. Boards are marked done on completion whether they succeeded or errored, so resume moves
forward (re-run fresh to retry failures). The marker is written only after a board's jobs are
flushed, so a crash in that small window just re-scrapes that one board — and a board split across
a resume (the duplicate-slug case) can re-emit lines too, since the in-memory id set doesn't
persist; both are dedupable by `id` downstream. Rejected the alternatives: deriving done-ness from
the `.jsonl` (can't tell a 0-job board from an unstarted one; a partial line corrupts it) and
one-marker-file-per-board (63k tiny files).

---

## Concurrent detail fetch: `fan_out` as a `BaseScraper` staticmethod (2026-06-23)

**Context.** Five scrapers (workday, smartrecruiters, rippling, trakstar, join) each hand-rolled
the *same* bounded `ThreadPoolExecutor` block for the per-posting detail pass — fan one detail GET
out per Job, isolate per-item failures so a single 404/timeout never drops the rest, stash the
result back on the raw item. The concurrency-plus-isolation invariant lived in no module: copied
five times, and exercised by zero tests (the scraper fixtures are post-`fetch_raw` snapshots, so
the fan-out path never ran under test).

**Options.** (A) leave it duplicated; (B) a free function in a new `headstart/concurrency.py`;
(C) a method on `BaseScraper`.

**Decision: C — `BaseScraper.fan_out`, a `@staticmethod`.** Only scrapers fan out detail fetches,
and that stays true as more scrapers are added, so the primitive belongs with the scrapers, not in
a general utility module. A staticmethod (matching the existing `slug_from` precedent on the base)
keeps it ergonomic as `self.fan_out(...)` inside each scraper while taking no instance state — so
it is still tested directly as `BaseScraper.fan_out([...], fn)` with no instance to construct.
Module option (B) was rejected: it implies non-scraper callers that don't exist, and the cohesion
win is small when scrapers are the only users.

**Scope.** Only the fan-out/isolation was lifted. Each scraper keeps its own per-item fetcher (URL
build + the GET-or-default guard) and the `_description`/`_detail` side-channel on the raw item, so
every `parse` is untouched. Folding in the per-item GET guard and removing the side-channel are
separate, later cleanups.

---

## HTTP layer: one pooled, thread-local curl_cffi session (2026-06-22)

**Context.** Scrapers used the stdlib `urllib` (a fresh TCP+TLS connection per request) for plain
boards and a separate `curl_cffi` path for the TLS-fingerprinted ones (darwinbox, trakstar). A
quick benchmark on this machine showed `urllib` at ~827 ms/request vs ~110 ms for a pooled client
on same-host bursts — a 5–7x penalty, paid hardest exactly where we burst: every board on
`boards-api.greenhouse.io`, every detail fetch on one tenant's host.

**Options.** (A) keep stdlib `urllib`; (B) add connection pooling to the sync layer, keep the
thread model; (C) rewrite the framework to async (httpx/aiohttp). The pooling — not raw library
speed — is the lever, so B captures the win with a small change; C only pays off at much larger
fan-out and is a big rewrite.

**Decision: B, unified on `curl_cffi.Session`.** All scraper HTTP now goes through
`headstart.http`, which hands out **one `curl_cffi` Session per thread** (keep-alive pools
connections; thread-local because a libcurl session isn't safe to share across threads — and the
scrapers already run under thread pools, per-company and per-detail-pass). It impersonates Chrome,
so the *same* client serves both plain JSON APIs and the Cloudflare/DataDome boards — collapsing
the two HTTP stacks into one. Async (C) is deferred until threads-plus-pooling stops being enough.

**Contract preserved.** `BaseScraper._get` still raises `urllib.error.HTTPError` on a definitive
HTTP error, so lever's EU-instance 404 fallback (which branches on `exc.code`) is untouched — only
the transport changed underneath. The liveness script still uses `urllib`; converting it is the
remaining high-value follow-up (it's the biggest same-host burst of all).

---

## Per-ATS slug derivation: `slug_from` on the scraper (2026-06-22)

**Context.** The discovery/liveness pipeline stores each board as a `(tenant, url)` row (e.g.
`data/ats-tenants-merged/active/{ats}.csv`). To scrape it, we need a `CompanyRef.slug` in the
exact shape that ATS's scraper expects. Most scrapers are keyed by the bare tenant label, but a
few aren't: zoho wants the careers host (`acme.zohorecruit.in`), workday wants the full careers
URL, and oracle wants the host plus an optional site number. Something has to map row → slug per
ATS. The question was where that per-ATS knowledge should live.

**Options weighed.**

- **A — an `if ats == …` mapping in `config.py`.** Simplest to read, but it puts ATS-specific
  knowledge *outside* the scraper. The scraper's `url()` already interprets the slug; the inverse
  mapping would sit in a different file. Since new scrapers get added often, every author would
  have to remember to also edit `config.py` — shotgun surgery, and a silent footgun (forget it →
  wrong slug → the scraper quietly fetches the wrong board or nothing).
- **B — an overridable `slug_from(tenant, url)` method on the scraper.** The slug knowledge lives
  with the scraper that owns it; adding an ATS stays a one-file change and `config.py` stays
  generic. This is the cohesive option.
- **C — bake the resolved slug into the active CSV.** Moves the same per-ATS logic upstream into
  `check_liveness.py` — the wrong layer (a liveness validator shouldn't know scraper slug
  internals) — and couples the data file to slug semantics, so the data goes stale if a scraper's
  interpretation ever changes.

**Decision: B.** The scraper is the single authority on what its slug means, so the inverse
constructor belongs next to `url()`. `config.load_active_companies` resolves the slug through the
registry (`SCRAPERS[ats].slug_from(tenant, url)`) and never names a specific ATS.

**A default method, not an abstract one.** Twelve of fourteen scrapers want the same
`return tenant`. Making `slug_from` abstract would force a dozen boilerplate overrides and provide
no safe default if one were forgotten. So `BaseScraper.slug_from` ships the `tenant` default
(template-method pattern) and only the exceptions opt in:

- `BaseScraper.slug_from` → `tenant`
- `ZohoScraper.slug_from` → the careers host from the url
- `WorkdayScraper.slug_from` → the full careers url

**Trade-off accepted.** `config.py` now reaches the scraper registry (lazily, inside
`load_active_companies`, so the lightweight `load_companies` toml path stays free of scraper
imports). That small `config → registry` dependency is worth it to keep the per-ATS knowledge in
one place.
