# ADR-0059: The priority ledger keys on `board_key`, the cost ledger on `{ats}:{slug}` — deliberately

**Status:** accepted · **Date:** 2026-08-18 · **Amends:**
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (its "mis-scores a colon-bearing Board" note about
`pick_boards` understated the problem) · **Relates to:**
[ADR-0022](0022-tech-priority-board-ordering.md), [ADR-0027](0027-measured-scrape-cost-ledger.md)

## Context

Two `data/state/` ledgers are keyed per Board, and they are keyed **differently** — which was
never written down, so a reader had no way to know which key a lookup needed.

- **`board_priority.csv`** is written by `update_ledgers priority` from
  `corpus.board_of(job_id)`. Job ids are built as `{board_key()}:{native_id}`, so its keys are
  the **`board_key` shape**: `workday:acme/Careers`, `personio:acme`.
- **`board_cost.csv`** is written by `harvest` from `f"{company.ats}:{company.slug}"` and read
  back by `scrape_plan` the same way, so its keys are the **scrape-list shape**:
  `workday:https://acme.wd1.myworkdayjobs.com/Careers`.

For most ATSes these are the same string. For the two that override `board_key()` — Workday
(slug is the whole careers URL) and Personio (slug is the whole host) — they are not, and
nothing said so. `pick_boards` looked priority scores up as `f"{ats}:{slug}"` under a comment
asserting `# matches corpus.board_of`. It does not:

| | boards |
|---|---|
| in the scrape list | 66,745 |
| Workday + Personio (keys cannot match) | 13,402 (20.1%) |
| …of those, holding a priority row¹ | 4,611 (Workday 3,784, Personio 827) |
| …matched by the old `{ats}:{slug}` key | **0** |

¹ against a local `board_priority.csv` snapshot (2026-08-16). The live ledger rides the HF
dataset, so treat the 4,611 as indicative of scale, not exact.

So every Workday and Personio board scored 0.0 regardless of what it had earned, and could enter
a slice only through the random exploration tail — the largest ATS by job volume, never
prioritised. The cost ledger was fine throughout, because both of its ends used the same key.

## Decision

Keep the two keyspaces, and make each lookup say which one it is in.

- Anything reading **`board_priority.csv`** goes through `config.board_identity(company)` — the
  real `board_key()`, with a documented fallback to `{ats}:{slug}` for a slug so malformed the
  scraper cannot build one. Sites: `board_priority.pick_boards`, `scrape_plan` (slice count,
  cold-start cost, within-shard ordering), `scrape_run`'s no-assignment path.
  `embed_run.order_by_priority` already used `board_of` and needed no change.
- Anything reading **`board_cost.csv`** stays on `f"{ats}:{slug}"`, because that is what
  `harvest` writes. "Fixing" it to match the priority ledger would have broken cost lookups for
  the same 13,402 boards — the mirror of the bug being fixed.

Not unified, because unifying means rewriting one of the two ledgers on the HF state round-trip
and re-keying every row of a file the next run reads — a migration whose only payoff is
aesthetic. The ledgers are written by different stages from different inputs, and each is
internally consistent.

## Consequences

- Those 4,611 boards regain their earned priority. No board loses a score: the old key matched
  nothing for them, so every change is 0 → *something*.
- Adding a third ledger means choosing a keyspace deliberately. `board_failures.csv` (ADR-0058)
  chose `board_key`, and normalises the shard reports' `{ats}:{slug}` through
  `board_failures.board_key_of` on the way in — the same conversion `scrape_join` applies.
- The remaining ADR-0049 caveat is unchanged and now isolated: a native id containing a colon
  makes `board_of` return a phantom Board that no real `board_key` matches, so that Board
  mis-scores. Rare, and it costs ordering rather than rows.
- A cheap invariant for anyone touching this: a Workday board's priority key contains `/` and no
  `://`; its cost key contains `://`. If a lookup mixes those, it is in the wrong keyspace.
