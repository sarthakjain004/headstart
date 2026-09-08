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
Two deliberate INFO exceptions to "per-item is DEBUG": `index sync`/`prune` log every
added/evicted/pruned id (batched ~100/line — the merge log is the only record of *which*
rows changed, and DEBUG would record nothing in CI), and `scrape_run` names slow boards
(≥120 s) at INFO rather than WARNING, because a straggler board is routine enough that
WARNING would spam the annotation summary.

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
- `index.py` and `filter_tech.py` output moved from stdout to stderr (nothing parsed
  either).
- Scripts under `scripts/` that import the scrapers get WARNING+ on stderr for free via
  logging's last-resort handler; they can opt into the full format with one `log.setup()`.
- New prints in pipeline code are a defect: log through `headstart.log` instead.

## Amendment (2026-09-08): three of the claims above were not true of the code, and WARNING is a quota

**Status:** accepted. Corrects the "Decision" and "Consequences" sections above — the decision
itself stands unchanged; what follows is what the code did *not* do, and one constraint the
original write-up did not know about.

An audit of the whole logging seam against this ADR found the code and the ADR had drifted apart
in three places. Each is fixed in the same change as this amendment; the ADR is corrected rather
than rewritten, because what it *claimed* on 2026-08-10 is part of the record.

- **"`setup()` — called once at each CLI entry (every `python -m` stage plus `python -m
  headstart`)" was false.** `alerts/run.py` and `alerts/bot.py` are both `python -m` entry points
  and neither called it, so the alerts workflows ran with no `headstart` handler at all: their
  output reached stderr only through logging's last-resort handler, unformatted and untagged, and
  `bot.py` was still on bare `print`. All 17 entry points now call it, and the claim is true as
  written for the first time.
- **"the tag is the module name's last segment … without per-module invented tags" was false at
  the one entry point most likely to be read.** `headstart/__main__.py` passed a literal
  `log.get("headstart.feed")` to emit `[feed]` — the exact per-module invented tag this ADR set
  out to abolish, complete with a comment justifying it. It now passes `(__name__, __spec__)` like
  every other CLI module and logs under `[__main__]`. Nothing consumed `[feed]`.
- **"`docs/AI_Integration/embedding-throughput.md`'s rate recipe updated in this change" was
  false.** The recipe's `grep` patterns still used the dead `[embed]` tag, and all of them
  targeted the *mono* code path (`to embed:`, `done: embedded`) while the pipeline has always run
  the sharded one (`--assignment`, hence `assignment:` and `done: shard embedded`). Every recipe
  in that section returned nothing. They now match the sharded path, and the doc says why.

**WARNING is an annotation channel with a hard cap, not just a level — treat it as a budget.**
Under Actions this ADR renders WARNING/ERROR as `::warning::`/`::error::` workflow annotations,
and GitHub keeps **10 per step, 50 per job and 50 per run**; everything past that is dropped from
the run page silently. Nothing above said so, and the consequence was real: `index sync` logged
one WARNING *per excluded Board* (`_log_reasons`) and one *per Board* holding out-of-scope rows,
and a run excluding several hundred Boards spent the merge job's whole annotation budget on
routine exclusions — displacing `state_fetch`'s abort, the torn-store checks and every other
genuine error. That is the exact inversion of what annotations were put here for. Those lines are
now INFO with a single capped WARNING naming the set, and `scrape_run`'s per-shard egress and
fan-out-width lines moved to INFO for the same reason. **The level policy above therefore gains a
rule: a line that can fire once per Board, per shard or per item is never WARNING, however much
someone wants it on the summary page — the step summary is the uncapped surface for that.**

**One defect this ADR's own design invited.** `_Formatter.format` built its line from
`record.getMessage()` and returned it directly, never calling `super().format()` — so
`exc_info=True` at a call site was accepted and then **discarded**, everywhere in the repo, with
no sign that anything had been lost. A parse bug in any of the 25 scrapers named neither file nor
line. The formatter now renders and appends the traceback (caching it on the record the way
`logging.Formatter` does), and `tests/test_log.py` pins it. The general lesson is worth keeping:
a formatter that bypasses `logging.Formatter` silently opts out of every feature it does not
re-implement, so overriding `format` wholesale needs a test per feature the call sites use.

**Consumers of these lines are now under test.** `tests/test_log_contract.py` pins each log line
`scripts/runlog/` parses against its emitter — calling the emitter where CI can import it, and
otherwise checking the pattern's literals against the emitter's source with `ast`. Two analyser
regexes had already died silently against reworded emitters before it existed. Any new
`scripts/runlog/` pattern must join that table or be exempted with a reason, so "anything grepping
CI logs must use the module-name tags" is now enforced rather than asserted.
