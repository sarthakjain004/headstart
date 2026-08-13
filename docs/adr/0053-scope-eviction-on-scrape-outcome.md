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

There is a per-Board error map — `harvest.scrape_all` fills it, `scrape_run` writes it into the
shard report, `scrape_join` reads it — and it was thrown away one hop short of sync. But carrying
it alone fixes nothing here, and it is worth being exact about why.

**That map only records Boards that raised, and a Board that raises writes no Jobs at all.**
`scrape_all` writes a Board's Jobs in the `else` branch of the same `try` whose `except` fills
`errors`, so the two sets are disjoint: an errored Board never reaches `data/jobs`, so
`_scraped_boards` never sees it, so sync could not have evicted from it anyway. It was already
safe.

The Boards that flap are the opposite case. `eightfold._api_search` hits a rate limit
mid-pagination, `break`s, and returns the postings it has — **without raising**. `harvest` records
nothing, the Board emits Jobs, and it looks completely scraped. The knowledge exists at the moment
of the break (the API returns `data.count`, so the scraper knows it has 300 of 850) and dies there.
Workday's `_paginate` is the same shape: it counts 404'd pages mid-crawl and *logs* "board partial",
then discards it. SuccessFactors' search walk breaks on a non-200 likewise.

## Decision

**A scraper that returns a list it knows is short says so, and a Board whose list is not
authoritative — for that reason or because it raised — is removed from the eviction scope
entirely.**

The missing half is the signal. `BaseScraper` gains `truncated: str | None`; a scraper that gives
up mid-crawl sets it instead of staying quiet, and keeps returning the Jobs it did fetch, because
those are real and worth indexing. `scrape_all` reads it after a successful fetch and reports the
Board as unfinished beside the errors it already collects. Three scrapers set it today — eightfold
on a non-200, empty page, or the page ceiling; workday on 404'd pages mid-crawl; successfactors on
a non-200 in its search walk — and any scraper that learns to detect its own truncation joins them
by setting one attribute.

`errors` and `truncated` stay separate up to the join, because a Board that returned 300 of 850
postings did not "fail" and logging it as a failure would make every count read wrong. They are
unioned at `scrape_join`, where the only question is the one sync asks: **is this Board's list
authoritative?**

`scrape_join` distils the shard reports' two outcome maps into
`data/state/unauthoritative_boards.json` — named for the question, not for either channel feeding
it — and `index sync` subtracts those Boards from the set `plan_sync` is allowed to evict from.
Such a Board's missing rows are then untouchable regardless of how many it lost, because "absent
from this run's corpus" is not "closed" when the list was never authoritative.

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

**The collapse guard stays as the backstop.** It now covers what remains: a scraper that cannot
detect its own truncation, and a shard hard-killed before it writes a report. The ratio heuristic
is the second line rather than the only one, and it is still the only cover for Boards under
`COLLAPSE_FLOOR` rows whose scraper stays silent.

**`pipeline-smoke.yml` cycle 2 goes green, honestly.** It truncates a 6-row Board, which is below
`COLLAPSE_FLOOR`, so the guard alone could never satisfy it — the test was red from the day it was
written and its failure message named this fix. The alternative, lowering the floor to 6, would
have passed the test while weakening a deliberate exemption and making small live Boards that
genuinely stop hiring stop draining. Green for the wrong reason.

**A Board that errors every run is never pruned by sync, and nothing else will reach it either.**
`index prune` only evicts rows whose Board is not *live in the ledger*, and nothing demotes a
persistently-erroring Board: `scripts/validate/check_liveness.py` is run by hand, not by any
workflow. So a Board that fails every scrape while staying `live` keeps its rows indefinitely.

That is the deliberate trade — serving a stale row beats blanking a live employer out of search
every other run — but it is unbounded, and it was previously bounded by accident, because the
ratio guard released as soon as a Board's loss dropped under a quarter. Two things would close it:
running liveness on a schedule so a dead Board leaves the ledger, or ageing rows out on
`first_seen`. Neither is in this change.

**A shard killed outright still slips through.** `_shard_report.json` is written in `scrape_run`'s
`finally`, reached when the inner time budget's SIGTERM becomes `SystemExit` — but not when the
66-minute *step* timeout hard-kills the process. The fragment still uploads (`if: always()`), so
that shard's partially-scraped Boards land in scope with no error recorded, and only the collapse
guard covers them. This is the same class of gap the guard exists for, and why it stays.

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
