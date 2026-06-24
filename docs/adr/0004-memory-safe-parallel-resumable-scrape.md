# ADR-0004: Memory-safe, parallel, resumable full-board scrape

- Status: Accepted
- Date: 2026-06-24

## Context

Scraping the liveness-validated Active lists is ~63k boards / ~3.3M Jobs. The old `scrape_all`
retained every Job in a `dict[str, Job]` (for dedup + the combined `docs/jobs.json`) and
`build_feed` copied them again — at harvest scale that OOMs near the end, after hours, losing
everything. The goal was also to make it heavily parallel.

## Decision

**Memory.** When streaming to `data/jobs/`, keep only a **`set[str]` of seen job ids**, write each
board's fresh Jobs to `{ats}.jsonl`, and **discard the Job objects**. The combined feed becomes
opt-in (`collect_feed`, default True for small runs; `__main__` forces it off for the Active-list
harvest — a 3.3M-Job `docs/jobs.json` would be gigabytes and is the OOM). The id set (~hundreds of
MB of strings, not GB of objects) also collapses the **duplicate slug forms** the liveness data
carries (e.g. `dollartree` appears under three slugs → same ids → written once).

**Parallelism — the knob is the company pool, not processes.** The work is network-bound and
`curl_cffi` releases the GIL per request, so threads already use every core; multiprocessing
wouldn't help and can't share the writer handles. Company-pool size defaults to `cores*4` capped at
64, overridable with `HEADSTART_WORKERS=N`.

**Per-board detail pools stay (rejected: one shared detail pool).** The 5 detail-fetching scrapers
each fan out their *own* bounded pool (`fan_out`, 8 workers; trakstar 4) per board. A single shared
global pool would bound total threads more tightly, but it would let many requests land on **one**
host at once (e.g. one `{slug}.hire.trakstar.com`) and trip its DataDome / rate limit — per-board
pools keep per-host concurrency polite. The accepted cost: peak in-flight requests ≈
`workers + workers*8`, so raising `HEADSTART_WORKERS` multiplies by the detail width.

**Resume.** A multi-hour harvest that crashes shouldn't restart from scratch. `JobWriter` keeps a
`.done` journal of completed board keys (`ats:slug`), flushed per board; `HEADSTART_RESUME=1` opens
the `.jsonl` files for append, loads the journal, and `scrape_all` skips boards already in it. Boards
are marked done on completion whether they succeeded or errored, so resume moves forward (re-run
fresh to retry failures). The marker is written only after a board's jobs are flushed, so a crash in
that small window just re-scrapes that one board — and a board split across a resume (the
duplicate-slug case) can re-emit lines too, since the in-memory id set doesn't persist; both are
dedupable by `id` downstream. Rejected alternatives: deriving done-ness from the `.jsonl` (can't tell
a 0-job board from an unstarted one; a partial line corrupts it) and one-marker-file-per-board (63k
tiny files).
