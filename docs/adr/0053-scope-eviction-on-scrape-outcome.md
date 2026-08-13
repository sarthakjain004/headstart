# ADR-0053: Scope eviction on a Board's scrape outcome, not on whether it emitted a line

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** [ADR-0046](0046-index-collapse-guard.md)

## Context

ADR-0046 shipped the collapse guard and named its own successor in the same breath: it is "a
stopgap for the blast radius, not the cause", holding a Board's evictions "until the scrape reports
per-Board outcomes and the sync can scope on them instead". This is that change.

The cause is one line of inference. `index._scraped_boards` decides a Board was scraped **iff it
emitted at least one job line**, so a scrape truncated by a rate limit is indistinguishable from a
Board that delisted everything it did not re-emit — and the rest of its rows are evicted as gone.
Measured across five production runs, that evicted and re-added whole Eightfold Boards on
alternating cycles: NVIDIA, Qualcomm and Micron jobs left search for roughly two hours at a time
and came back, forever.

The guard infers truncation from a *ratio* — a Board losing more than `COLLAPSE_RATIO` of its rows
is presumed truncated. That works where the loss is large and the Board is big, and by design it
does nothing below `COLLAPSE_FLOOR` (20 rows), where a large ratio is only a handful of rows.

But the outcome was never actually unknown. `harvest.scrape_all` records a per-Board error map,
`scrape_run` writes it into the shard report, and `scrape_join` already reads those reports. It
read them only to print a summary count. The signal existed end to end and was thrown away one hop
short of the consumer that needed it.

## Decision

**A Board whose scrape errored is removed from the eviction scope entirely.**

`scrape_join` distils the shard reports' error maps into `data/state/scrape_errors.json`, and
`index sync` subtracts those Boards from the set `plan_sync` is allowed to evict from. An errored
Board's missing rows are then untouchable regardless of how many it lost, because "absent from this
run's corpus" is not "closed" when the Board errored.

Two details are load-bearing.

**The keys are translated, not copied.** Shard reports key errors `{ats}:{slug}` — the scrape
list's key — while eviction scope is keyed by `board_key()` (ADR-0049). Those are the same string
for Greenhouse and Eightfold and *not* for Workday, whose slug is the entire careers URL:
`workday:https://x.wd1.myworkdayjobs.com/Site` has to become `workday:x/Site` or the lookup matches
nothing while appearing to work. Keys that cannot be resolved are dropped with a warning rather
than written through unconverted.

**The file is written on every run, including clean ones.** `data/state` round-trips through the HF
dataset, so a run that skipped the write would leave the previous run's errors in place and protect
Boards that scraped fine this time.

`data/state` already rides the `corpus-state` artifact to the job that runs `index sync`, so no
pipeline plumbing changes.

## Consequences

**The collapse guard stays, and becomes the backstop it was meant to be.** It still catches the
case this ADR cannot: a scraper that swallows its own failure and returns a short list with no
error recorded — `scrapers/eightfold.py`'s `break` on a non-200 mid-pagination is exactly that. The
two are complementary, and the ratio heuristic is now the second line rather than the only one.

**`pipeline-smoke.yml` cycle 2 goes green, honestly.** It truncates a 6-row Board, which is below
`COLLAPSE_FLOOR`, so the guard alone could never satisfy it — the test was red from the day it was
written and its failure message named this fix. The alternative, lowering the floor to 6, would
have passed the test while weakening a deliberate exemption and making small live Boards that
genuinely stop hiring stop draining. Green for the wrong reason.

**A Board that errors every run is never pruned by sync.** That is the intended trade, and it is
already bounded elsewhere: `index prune` evicts rows whose Board is not live in the ledger, and a
Board erroring persistently eventually leaves the ledger through liveness. Sync's job is not to
garbage-collect Boards it never successfully read.

**Unresolvable error keys fail open, not closed.** A malformed key is dropped and its Board keeps
the old inferred behaviour. Failing the other way — protecting a Board we could not identify —
would be a silent, unbounded eviction freeze.

## Alternatives rejected

**Lower `COLLAPSE_FLOOR` so the ratio guard covers small Boards.** One line, and it would make the
smoke test pass. Rejected: it does not carry the outcome signal, so it still cannot tell a
truncation from a delisting on any Board — it only moves the threshold at which it guesses. It also
worsens the regression ADR-0046 already records, where a small Board that stops hiring keeps its
rows.

**Ship the error map in its own artifact.** Rejected as unnecessary: `data/state` already travels
from the join to the index job, and adding an artifact adds a failure mode (a missing download) to
buy nothing.

**Have sync read the shard reports directly.** Rejected: the fragments stop at the join stage by
design, and widening the index job's inputs to the whole fan-out's telemetry couples it to the
scrape's file layout. One distilled file at a stable path is the narrower seam.
