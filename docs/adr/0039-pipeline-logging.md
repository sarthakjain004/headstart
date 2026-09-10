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

## Amendment (2026-09-08, second): the level policy the code actually has, and where the rule is enforced

**Status:** accepted. Reconciles the "Decision" section's level policy with the amendment above —
which contradicted it the moment it landed — and records two constraints the original write-up
assumed rather than stated. The decision itself still stands.

**"DEBUG is per-item detail" is not the rule, and was never the code.** The Decision above puts
per-item detail at DEBUG and then lists two deliberate INFO exceptions to it. The amendment above
demoted every per-item WARNING it found to **INFO** — not one of them to DEBUG. Both cannot be the
policy. Plainly, the rule now:

- **INFO is per-item detail**, and the "two deliberate exceptions" are not exceptions; they were
  the rule, stated early. A pipeline run is a CI job whose log is its only record: `HEADSTART_LOG`
  defaults to `info` and nothing in `.github/workflows/` sets it otherwise, so a line written at
  DEBUG does not exist in the one place anyone reads it. That is exactly what the `index sync`
  exception already argued ("the merge log is the only record of *which* rows changed, and DEBUG
  would record nothing in CI"); it generalises to every per-item line.
- **DEBUG is what a local run wants and CI must not pay for** — `http.fetch`'s per-retry line,
  `scrape_run`'s per-Board timing. Counted at `dd982fe` (`git grep -c` over
  `src/headstart/**.py`, before this correction's own edits): **4** `.debug(` call sites against
  190 `.info(` and 51 `.warning(`. "Per-item is DEBUG" describes 4 lines in 245, so the ADR was
  describing an intention, not the seam. The commit is named because the first version of this
  bullet quoted 4 / 189 / 53 — the tree *before* the commit it shipped in, stale on arrival — and
  because these three counts move with every logging change. The ratio is what is load-bearing;
  it survives a drift of a few lines either way.
- **WARNING remains a budget**, per the amendment above: never a line that can fire once per
  Board, per shard or per item, however much it deserves the summary page.

**The bounded first-occurrence idiom is one helper, `log.FirstOnly`.** How a fault that repeats
gets reported without spending an annotation per occurrence: the first occurrence warns and
carries its traceback, every one after it informs, and the caller's own count or
`log.named_sample` says how far it reached. It is a class in `headstart/log.py` rather than a
pattern to copy because the copies diverged into four spellings — a module-level set
(`config.board_identity`), a counter (`harvest.scrape_all`), a list plus a chosen emit function
(`index_plan.live_keep_set`) and a second counter plus a second chosen emit function
(`scrapers/workday.py`, still on its own spelling) — each re-justifying the same rule
in its own comment block, and one of them shipped the bug the shape exists to prevent:
`index_plan` bounded the *level* to the first Board and left `exc_info=True` unconditional, so a
systemic `board_key` failure printed one full stack per Board — the same flood, one indirection
later. Level and traceback are now chosen together, in one place, and cannot be bounded
separately.

That one place cannot be reached from `alerts/store.py`, and the exception is deliberate: the
whole `alerts/` package is copied into the Space image, laid down beside `app.py` with no
`headstart` package to import from — the same constraint that already makes that module's logger
a bare `logging.getLogger`. Its `Store.get` Hub-failure arm was an `_log.error(..., exc_info=True)`
running **once per Account**, so a Hub outage cost 40 `::error::` annotations and 40 tracebacks
(measured, 40 Accounts against one outage); and it was invisible to both of the checks below,
being neither on the scrape path nor lexically inside a loop, while `alerts/run.py`'s own bounded
catch-all never saw it because this arm fires first and answers `None` rather than raising. It is
now bounded by a module-level flag spelling `FirstOnly`'s contract by hand, pinned by
`tests/test_alerts_store.py`. That is the one sanctioned copy of the idiom; wherever the seam is
importable, importing it remains the rule. Its cost is stated where it is paid: the demoted lines
land at INFO, which the Space's `logging.lastResort` handler does not print, so in the deployment
the first occurrence is the only one a reader sees.

**The run-context line is a log line, so it lives in `log.py` too — and `alerts/` now emits
one.** `stage= run= attempt= sha=` is what says *which* run a log belongs to once it is off the
Actions page, and it started as `ingest/observability.context` because the pipeline stages were
its only callers. They were not: `alerts/run.py` and `alerts/bot.py` are `python -m` entry points
wired into `alerts.yml` and `bot.yml` — the latter every fifteen minutes, so 96 runs a day of
otherwise indistinguishable lines — and neither emitted one, because `alerts/` may not import from
`ingest` (CLAUDE.md's repo conventions). Moving beats importing: it is now `log.context`, beside
`FirstOnly` and `named_sample`, which are there for exactly the same reason — their callers sit on
both sides of the pipeline package, and a correlation line is part of what a log line *is*, which
is what that module owns. `observability` keeps its other three seams (step summary, shard-report
round trip, error summary): each of those is about an artifact a run leaves behind rather than a
line it writes. Two consequences. The tag the line carries changes with it, from `[observability]`
to `[log]` — nothing parses this line (`tests/test_log_contract.py` says so in as many words: no
analyser reads it, a human greps it), so the change costs nothing, but a doc or a habit keyed on
the old tag is now wrong. And the `stage=<the emitting module's own name>` rule that file enforces
holds across the move: the two new call sites say `stage=run` and `stage=bot`, so `stage=run
run=32671773723` reads oddly for the one module whose name collides with the field beside it.
The curated-feed entry (`python -m headstart`) still does not call it, now for the only reason
that survives the move: no workflow runs it, so there is no run for it to name.

**The WARNING rule is test-enforced, on a partial scope — know which.**
`tests/test_log_levels.py` parses source with `ast` and fails on any WARNING site absent
from an allowlist that must name *why* that line is bounded to one per run. Two things about it
matter at the call site. It is a **compile-time** check rather than a `logging.Filter`, because a
filter suppresses records after the fact — hiding the volume rather than preventing it — while
the budget is spent at emit time, in code a reader will copy. And its scope is
**`src/headstart/scrapers/*.py` plus `harvest.py`**: the scrape path, where a per-Board line is
easiest to write and a shard's ~1,300 Boards make it costliest. Everything else — the ingest
stages, `alerts/`, `config.py`, `search.py` — is **unchecked**, so a green run is evidence about
the scrape path only. (Widening the walk to the rest of `src/headstart/` is the obvious next step
and is not taken here.)

**The Space renders a second line format, and one level choice depends on it.**
`deploy/hf-space/app.py` calls no `log.setup()`, and `deploy-space.yml` does not copy `log.py`
into the image — there is no `headstart` package there at all, which is why `search.py` builds its
logger with `logging.getLogger` directly. So `headstart.search`'s records reach stderr through
`logging.lastResort`: a bare stderr handler, **level WARNING, no formatter** — no clock, no
`[tag]`, no level name, just the message. Two consequences the ADR's "one format" claim does not
cover. Below WARNING nothing is emitted at all in the deployment that serves users, which is why
`search.py`'s boot line about a served table missing columns is a WARNING and not INFO — it is not
a per-item line, and at INFO it would be invisible exactly where it matters. And anything grepping
Space logs must not expect the `[tag]` the pipeline's own consumers key on.

### Amendment, 2026-09-09: why each unhandled `FirstOnly` site is clean

`FirstOnly.report` attaches `sys.exc_info()`, which is thread-wide rather than per-frame — the
same rule `logging`'s own `exc_info=True` follows. Eight of its fifteen call sites are lexically
inside the `except` they report on, so the stack is theirs by construction and needs no argument.
The other seven report a *condition*, and they are clean for three different reasons. The
distinction is design rationale, so it lives here rather than in the class's docstring, where it
had grown to outweigh the nine lines of code it described.

- **By construction** — `scrapers/workday.py`'s detail-loss tally, the original of the shape: a
  threshold tripping at the end of a detail pass, with no `except` anywhere above it that could
  still be handling something.
- **By measurement** — `spare_egress`'s five tunnel checks. An `ast` sweep of `src/headstart` for
  a network call lexically inside an `except` found none, and both of `http.py`'s entries into
  them sit outside its `except RequestsError`. This is the weaker guarantee: it holds for the
  call graph as it is, and the first caller that dials while handling an exception starts
  attaching that exception's stack to a line about WARP.
- **By the handler being the point** — `config`'s identity fallback. `_report_identity_failure`
  is only ever reached from `board_identity`'s own `except` arm, so the live exception is
  precisely the `board_key()` failure the line is about. It sits one frame below the handler
  rather than inside it, which is why the census counts it as outside even though its stack is
  the right one.

`tests/test_log.py` recomputes the census from the source rather than trusting either document.
That test exists because a hand-written count shipped stale three times during this overhaul —
once inside the very commit correcting a different stale count.
