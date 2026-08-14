# ADR-0055: Bound the collapse guard's hold

- Status: Accepted
- Date: 2026-08-14
- Amends: [ADR-0046](0046-index-collapse-guard.md) — the guard's threshold and intent stand; what
  it *does* on a trip changes from refusing to rate-limiting.

## Context

ADR-0046 stopped the index flapping: when a Board would lose more than `COLLAPSE_RATIO` (25%) of
its indexed rows in one run, `plan_sync` withheld that Board's evictions entirely, because a
rate-limited truncation is indistinguishable from a mass delisting at the line level.

It works, and it made a second problem. **Nothing else can ever reach a held row.** Tracing every
path that deletes:

- `plan_sync` — the only Board-scoped evictor, and the `continue` skips it.
- `index prune` — evicts only ids whose Board is **absent from the ledger**. A held Board is still
  Live, so prune structurally cannot reach it. `check_liveness` is manual, so nothing demotes it.
- `_take_upgrades` — deletes then re-adds; net zero.
- `compact` — copies live rows verbatim.

So a Board the scrape can never fully re-fetch accretes rows forever. The hold is also
all-or-nothing, which compounds it: while held, the Board stops evicting *ordinary* churn too, so
`missing` becomes the frozen unreachable subset **plus** every posting that has since closed, and
it only grows. Release requires `missing/len(ids)` to fall under 25%, but the guard itself pins
`missing` up while freezing `ids` except for adds.

Production, over ~22 hours:

| | withheld | Boards |
|---|---|---|
| first run reviewed | 267 | 7 |
| last run reviewed | 953 | 12 |

Monotonic, 3.5x. And three Boards withheld an *identical* count on every run —
`ashby:clera` (119), `ashby:careerswift.ai` (23), `zoho:dataeconomy.zohorecruit.in` (40) — which
is the signature of a stable subset the scraper can never re-fetch, not of transient noise.

ADR-0046's Consequences said those rows "persist until the Board falls below the threshold". The
code contradicted it: the guard removed the mechanism that would lower the ratio.

ADR-0053 does not cover these Boards. Its `truncated` signal is set only by `eightfold`, `workday`
and `successfactors`; `ashby` and `zoho` — the worst offenders — never enter
`unauthoritative_boards.json`, so the blunt ratio is the only thing acting on them.

## Decision

**Cap the loss instead of refusing it.** A Board that trips the guard now evicts its oldest missing
rows up to `COLLAPSE_RATIO` of its size, and withholds the remainder. `held` reports what was
withheld, not what was missing.

The cap is deliberately the guard's own threshold, so the invariant ADR-0046 actually set out to
protect is unchanged: **no single truncated scrape can collapse a Board.** What changes is that a
row which stays missing run after run does eventually leave, so the backlog converges instead of
ratcheting. A Board seeing a genuine mass delisting now sheds it over several runs rather than one
— slower than ADR-0014, which is the price of not believing any single scrape.

The drain is ordered by the `first_seen` stamp the table already carries, oldest first: a row we
have not been able to confirm for longest is the likeliest to be genuinely closed. Rows predating
the column have no stamp and sort first, which is correct — they are the oldest of all. The id
breaks ties so the plan is deterministic. `first_seen` reaches `plan_sync` as a plain mapping, so
the planner keeps its "no LanceDB import" property and stays testable on the base-deps CI install.

## Alternatives considered

- **Age-out in `prune`.** Prune is Board-scoped by liveness, not row-scoped by staleness; teaching
  it to reach into Live Boards would give two different evictors overlapping authority over the
  same rows, which is how ADR-0049's scoping bug happened.
- **A consecutive-misses ledger** (evict after K runs missing). More precise — it distinguishes
  "unreachable" from "old" — but it is new per-id state on the hot path, and the cap already
  converges. Worth revisiting if the drain proves too blunt.
- **Raising `COLLAPSE_RATIO`.** Trades one arbitrary threshold for another and does not end the
  ratchet; a permanently truncated Board still never drains.

## Consequences

- The withheld count stops growing without bound. On a persistently truncated Board it converges
  on exactly the rows the scrape can still see (pinned by
  `test_a_persistently_truncated_board_drains_instead_of_ratcheting`).
- **A silently-and-permanently truncating Board will, over several runs, lose rows that may still
  be open.** This is the real cost and it is accepted: those rows are unverifiable, serving them
  indefinitely is its own harm, and ADR-0053 already removes from scope every Board that *can*
  report its own truncation. A scraper that detects truncation and reports it is strictly the
  better fix; this is the backstop for the ones that cannot.
- The guard's warning stays loud, and now means "this Board is draining, and here is what is still
  withheld" rather than "this Board is frozen".
- `index sync` reads one extra string column (`first_seen`) alongside the ids. No vector read, so
  the cost is negligible against the scan already happening.
