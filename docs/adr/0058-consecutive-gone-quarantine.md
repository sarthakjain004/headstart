# ADR-0058: Confirmed-dead boards quarantine via a consecutive-gone ledger in `data/state/`

**Status:** accepted · **Date:** 2026-08-18 · **Relates to:**
[ADR-0012](0012-liveness-ledger.md) (the liveness ledger stays probe-owned),
[ADR-0022](0022-tech-priority-board-ordering.md) (whose partial-harvest rule is why this gap existed)

## Context

A dead board is invisible to every existing demotion mechanism. Reviewing 19 consecutive pipeline
runs (2026-08-16 → 2026-08-18): 287 boards returned HTTP 404 at least once, 1,347 failure rows in
total, and the boards pinned in the priority head — greenhouse:hibu, greenhouse:dmcengineering2024,
successfactors:careers.ltimindtree.com among them — failed in **all 19 runs** while the committed
liveness ledger still carried them `live` with healthy job counts from probes 3–6 weeks old.

Tracing why nothing demotes them:

- **Nothing in `src/headstart/` writes the liveness ledger.** Only the offline probes under
  `scripts/validate/` do. The pipeline reads it (`config.load_active_companies`) and nothing more,
  so demotion waits for a human to run a probe.
- **The priority ledger cannot demote either.** `update_ledgers priority` snapshots the boards
  *present in the jobs output*; a board that 404s produces no jobs, is absent from the snapshot,
  and the ADR-0022 partial-harvest rule carries its row unchanged — the rule cannot tell "not
  scraped this run" from "scraped and gone". A dead board therefore keeps the score it earned
  while healthy and stays pinned in the deterministic priority head forever.

The waste is bounded (~9.5 scrapes/run land on head-pinned dead boards) but permanent, and it
compounds: the higher a board's score at death, the more firmly it is pinned.

## Decision

A third `data/state/` ledger, `board_failures.csv`, maintained by a new
`update_ledgers failures` subcommand in the join stage and read by `scrape_plan`:

- Only the **gone** class of failure counts — HTTP 404/410. A 429, a 5xx, a timeout or a TLS
  error is a *fetch* failure and never ages a board (Workday alone raised 2,840 fatal 429s on
  live boards across the same 19 runs).
- A board is quarantined — dropped from the scrape slice — only after **5 consecutive gone-runs**
  (`QUARANTINE_AT`). Runs that don't select the board leave its row untouched (the same
  partial-harvest rule the other ledgers follow), so 5 strikes is weeks of agreement, not one bad
  afternoon. Any *successful scrape* deletes its row entirely — including a zero-job one: the
  shard reports carry `boards_ok` (every board that completed without raising) precisely because
  a zero-job success leaves no corpus lines and would otherwise be indistinguishable from "not
  scraped". Alive-and-empty must clear, or a board that empties after a few 404s would sit one
  strike from quarantine forever.
- For the gone-verdict to be reachable, **no scraper may swallow a listing-level HTTP error into
  an empty result**. This change removed exactly that in lever (dual-host 404 → `[]`), workday
  (first-page 404 → "no jobs"), rippling, join, ripplehire, eightfold's sitemap surface, and
  successfactors (all surfaces empty now probes the host root and raises if it is gone). Per-job
  *detail* failures stay isolated — one job's failed description must not sink a board — and
  content-shaped dead signals on HTTP 200 (keka's dead-portal markers, freshteam's HTML-404-at-
  200) stay as empty results, since there is no HTTP status for the ledger to count.
- The filter applies in `scrape_plan` **only**. `live_keep_set` — which feeds `index prune` — is
  deliberately untouched: shrinking it would evict the board's served rows as a side effect of a
  scraping decision, and rows on a quarantined board should instead age out through the normal
  scrape-diff path if the board ever returns, or wait for a probe to mark it dead.
- The ledger fails **open** everywhere: a missing or torn file quarantines nothing.

## Options considered

1. **Quarantine in `data/state/` (chosen).** No CI pushes to git; the committed liveness ledger
   stays the probe-owned truth; recovery is automatic (one successful scrape clears the streak).
2. **CI commits `status=dead` back to `data/validate/liveness/`.** Single source of truth, but a
   bot commit to a public repo's main every 2 hours, races with manual probe runs, and a scrape
   heuristic would overwrite probe-grade verdicts in place.
3. **Two-phase: pipeline observes, a scheduled probe demotes in git.** Cleanest ownership, but
   dead boards keep burning scrape budget until the second job runs, and it adds a workflow.

Option 1 keeps the authority boundary (probes own `dead`; the pipeline owns only its own
scheduling) while closing the loop immediately.

## Consequences

- Boards 404ing for 5 consecutive selected runs stop being scraped; the log line
  `quarantine: skipped N of M confirmed-gone board(s)` makes the effect visible per run.
- The liveness CSV may say `live` while the pipeline skips the board. That is intentional — the
  CSV records the last *probe* verdict, the quarantine records the scrape's own experience — but
  anyone reconciling the two should read `data/state/board_failures.csv` first.
- A board that dies and later revives is re-admitted automatically: its first successful scrape
  clears the streak. Until then it costs nothing.
- `update_ledgers failures` is `continue-on-error` in the workflow, like `cost`: a missed update
  costs one run of memory and must not sink a run that already scraped successfully.
