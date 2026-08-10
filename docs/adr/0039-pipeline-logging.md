# ADR-0039: Pipeline logging through one stdlib seam

**Status:** accepted · **Date:** 2026-08-10

## Context

Every pipeline stage and scraper logged through ad-hoc `print(..., file=sys.stderr,
flush=True)` — or stdout, depending on the module. Tags were invented per module and had
drifted (`[scrape]` in harvest, `[plan-scrape]` in scrape_plan but `[plan]` in embed_plan,
nothing at all in filter_tech); `index.py` wrote to stdout while its neighbours wrote to
stderr; nothing carried a timestamp locally; and there was no verbosity control of any kind.

Two observability gaps had already cost real debugging time. First, **board errors were
counted but never shown**: `scrape_all` collects per-board messages into `RunResult.errors`,
and the pipeline path printed only `(N board errors)` — 150–250 messages per run, dropped.
Diagnosing a failing ATS meant re-running boards locally. Second, **the scrapers were
completely silent**, exactly where the known silent failure modes live: a Workday middle page
that 404s leaves a partial board (whose unfetched jobs then evict as though closed —
the open ADR-0014 scoping issue), detail-pass failures degrade to `None` fields (the
ADR-0021 nulls), and every `http.fetch` retry — the rate-limit pressure signal — was
invisible. `scrape_all` even had an `on_board` hook documented as "the hook for live
per-board logging", with zero callers.

## Decision

**Stdlib `logging` behind one thin module, `headstart/log.py`.** No new dependency; the
module owns three things:

- **`setup()`** — called once at each CLI entry (every `python -m` stage plus
  `python -m headstart`). Attaches a single stderr `StreamHandler` to the **`headstart`
  root logger** — deliberately not the global root, so the ML stack's own chatter
  (`sentence_transformers`, `huggingface_hub`) is neither adopted nor amplified.
  `StreamHandler` flushes per record, so the stream-incrementally rule holds. Level comes
  from `HEADSTART_LOG` (default `info`; `debug` turns on per-board / per-retry detail).
- **`get(__name__, __spec__)`** — the logger factory. A module run as `python -m` imports
  with `__name__ == "__main__"`, which would fall outside the `headstart` root and never
  reach the handler; `__spec__.name` still carries the real dotted name, so CLI modules pass
  both. Library modules (`harvest`, `http`, the scrapers) pass just `__name__`.
- **One format**: `HH:MM:SS [tag] message`, where the tag is the module name's last segment
  ([scrape_run], [workday], [http]) — so a merged CI log still says which stage or scraper
  spoke, without per-module invented tags. WARNING+ carries its level name, and **under
  GitHub Actions renders instead as a `::warning::` / `::error::` workflow annotation**, so
  anomalies surface on the run's summary page rather than inside fifteen shard logs
  (generalizing what `state_fetch` hand-rolled). stdout is reserved for machine-shaped
  output — the planners' `{"shards": ..., "count": ...}` echo stays a `print`.

**Level policy.** INFO is the stage's summary/progress narrative (what the old prints said);
DEBUG is per-item detail (each successful board, each HTTP retry); WARNING is an anomaly the
run survives (a failed embed batch, a partial Workday board, the grouped board-error
summary); ERROR is a fatal abort — every `exit 1` path (`state_fetch` exhaustion, the prune
keep-set guard, torn-store checks) now emits an `::error::` annotation before exiting.

**The dropped signals now log.** `scrape_run` wires the `on_board` hook: each failed board
logs one INFO line *live* — a shard killed by its CI time budget has already streamed every
failure it saw, matching the banking design — and the run ends with one WARNING summarizing
errors grouped by exception type × ATS. Successful boards log at DEBUG. In the scrapers,
`BaseScraper.report_detail_gaps` logs one INFO line per board when a detail pass came back
with `None`s (the ADR-0021 tripwire), `http.fetch`/`fetch_async` log each retry at DEBUG,
and Workday's `_paginate` warns when middle pages 404ed — the tripwire for the partial-board
eviction hole, whose *fix* (excluding partial boards from the eviction scope) is a separate
decision against ADR-0014, not taken here.

## Options considered

- **A tidy `print` wrapper** (uniform tags only): smallest diff, but no levels, no verbosity
  switch, no annotation seam — it would answer none of the "show me only the failures in CI"
  needs and would be rebuilt as this ADR later.
- **Structured JSONL event stream per shard**: the best forensics (the index-churn
  investigation would have wanted it), but new artifact plumbing for a need
  `board_cost.csv` + the new WARNING lines mostly cover. Revisit if log-grepping becomes the
  bottleneck again.
- **structlog / loguru**: a dependency the base install (`dependencies = ["curl_cffi"]`)
  would carry into every CI shard, for features stdlib logging already provides at this
  scale.

## Consequences

- Old tags are gone; anything grepping CI logs must use the module-name tags
  (`[embed]` → `[embed_run]` — `docs/AI_Integration/embedding-throughput.md`'s rate recipe
  updated in this change).
- `index.py` output moved from stdout to stderr (nothing parsed it).
- Scripts under `scripts/` that import the scrapers get WARNING+ on stderr for free via
  logging's last-resort handler; they can opt into the full format with one `log.setup()`.
- New prints in pipeline code are a defect: log through `headstart.log` instead.
