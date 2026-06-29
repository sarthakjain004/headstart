# ADR-0010: Dashboard feed built from the per-board `.jsonl`, not an in-memory copy

- Status: Accepted (supersedes the `collect_feed` / in-memory-feed part of [ADR-0004](0004-memory-safe-parallel-resumable-scrape.md))
- Date: 2026-06-29

## Context

ADR-0004 made the scrape stream every Job to per-ATS `data/jobs/{ats}.jsonl`, but kept an *in-memory*
path for the dashboard feed: the small curated run set `collect_feed=True`, which retained every Job
in `RunResult.jobs` so `build_feed` could shape `docs/jobs.json` from that list. So the curated run
held the same Jobs twice — once on disk in the `.jsonl`, once in memory — and nothing ever read the
`.jsonl` back. The `.jsonl` is the natural single source of truth.

## Decision

`scrape_all` **always streams to the `.jsonl` and never retains Jobs in memory** — `collect_feed` and
`RunResult.jobs` are removed, and `jobs_dir` is now required. The dashboard feed is **derived from the
`.jsonl`**: `build_feed(jobs_dir, errors)` reads every `{ats}.jsonl` back, dedups by `id` (resume can
re-emit a board's lines), and produces the same `{generated_at, count, errors, jobs}` shape the
dashboard already consumes. The run's `errors` map is passed in, since it isn't in the `.jsonl`.

The small-vs-large gating stays: `__main__` builds `jobs.json` only for the small curated run; the
millions-scale active-list harvest still produces only the `.jsonl` (a single feed of millions of
Jobs would OOM `build_feed` and be unloadable in a browser). `build_feed` loads all Jobs into memory,
so it is for the curated feed only.

## Rejected alternatives

- **Keep `collect_feed` / `RunResult.jobs`** — that is the redundancy this removes; nothing read the
  `.jsonl` back, so the in-memory copy was pure duplication.
- **Have the dashboard read the `.jsonl` directly** — the `.jsonl` are gitignored/local and not served
  by GitHub Pages, there are many of them, and they lack the feed's metadata wrapper. The dashboard
  needs one served `jobs.json`. True scale (filter/search over millions) is the AI search backend
  (Flask + LanceDB, ADR-0008), not a paginated static page — deferred as separate work.

## Consequences

Single source of truth and less code (the in-memory branch is gone). `build_feed` gained a dedup-on-
read pass. `scrape_all`'s signature changed (`jobs_dir` required, no `collect_feed`); callers and the
pipeline tests were updated. The streaming + resume mechanics of ADR-0004 are unchanged.
