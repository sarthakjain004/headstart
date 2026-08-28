# ADR-0096: One key for both Board ledgers

**Status:** accepted · **Date:** 2026-08-28 · **Amends:**
[ADR-0027](0027-measured-scrape-cost-ledger.md) (the cost ledger's key),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (which taught the planner to *pair* the
two keys rather than unify them) · **Relates to:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (where `board_key` comes from),
[ADR-0022](0022-tech-priority-board-ordering.md) (the ledger that was already keyed this way)

## Context

The pipeline keeps two per-Board ledgers on the same HF round-trip, and until now they named the
same Boards differently.

| ATS | `board_cost.csv` (`{ats}:{slug}`) | `board_priority.csv` (`board_key`) |
| --- | --- | --- |
| greenhouse | `greenhouse:stripe` | `greenhouse:stripe` — identical |
| zoho | `zoho:flintex.zohorecruit.com` | identical |
| **workday** | `workday:https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite` | `workday:nvidia/NVIDIAExternalCareerSite` |
| **personio** | `personio:croftstone.jobs.personio.com` | `personio:croftstone` |

Neither key was chosen; both were inherited. Cost is written by `harvest`, which holds the slug it
is fetching — for Workday that slug *is* the careers URL, pod included. Priority is written from
Job ids, and a Job id is `{ats}:{board_key}:{native}`, so anything derived from ids speaks
`board_key`. 17 of 20 ATSes have nothing to strip and the two agree; only Workday and Personio
diverge.

Nothing was broken. ADR-0049 taught `scrape_plan` to carry **both** keys for each Board and read
each ledger with its own, and it works: measured 2026-08-28, the cost lookup hits 99.9% on Workday
and 100% on Personio, 97.2% overall. But the arrangement is a standing trap, and it has now been
sprung twice — once as the bug ADR-0049 fixed (4,611 Boards scored 0.0 because the wrong key was
used), and once in analysis, where joining the two ledgers produced "6,906 Scored Boards have no
cost row" and an inference of a mispricing that did not exist.

Two properties are worth having beyond that:

- **`board_key` survives a pod migration.** Workday moves tenants between data centres — the
  scraper carries `_resolve_instance` precisely because *"the URL's `wdN` goes stale … and the
  board would read as empty."* Keyed by URL, a migrated Board looks brand new and loses its whole
  measured history to the ATS median. Keyed by `board_key`, the pod is not part of the name.
- **`board_key` is one name per Board.** `accenture.wd3` and `accenture.wd103` are one Board on
  two pods — 333 such pairs in the live ledger.

## Decision

**Both ledgers are keyed by `board_identity` (i.e. `board_key`).**

- `harvest` records cost under `board_identity(company)`. A map built once per shard; the
  scraper's own `{ats}:{slug}` stays the working identity for **resume, errors and the `on_board`
  callback**, because those name the URL this shard is actually fetching.
- `scrape_plan` looks the ledger up with the same key, and `_gated_boards` takes one key instead
  of a `(cost_key, priority_key)` pair.
- The ledger on HF is re-keyed once by `scripts/migrate/rekey_board_cost.py`.

`board_key` rather than `{ats}:{slug}` because unifying the other way would mean re-keying every
Job id — Workday ids would carry a full URL with `://` and `/` — and re-indexing 330k rows, while
*losing* the 333 pod collapses. `board_key` is also the shorter, readable form, and already the
canonical identity everywhere else: `_dedupe_boards`, `prune`'s keep-set, `PARKED_BOARDS`.

## Migration

Measured on the live ledger: **85,839 rows → 85,825 keys, 14 merged, 0 unmappable**, with 72,006
rows already board_key-shaped. Only 14 collapse, because a pod pair only holds two cost rows when
both URLs were scraped, and dedupe usually admits one.

Not a self-healing read: **`board_key()` is not idempotent.** Workday's parses a careers URL and
raises `ValueError` on anything else, so a re-key applied twice fails loudly — which is the safe
direction, but means a lazy read path would have to track what it had already converted. A one-off
rewrite leaves no transitional code to forget to delete.

Case is preserved. `board_key()` does not fold it, and the read side does not either:
`_dedupe_boards` lowercases only to *choose* among variants, then keeps that row's own spelling —
the exact string `scrape_plan` looks up. The ADR-0023 case-variant rows are a separate concern and
were measured contributing **0 s** of mispricing.

## Consequences

The two ledgers are now joinable, which is what both people who got this wrong were trying to do.

**One run of degraded packing if the migration is skipped.** A writer emitting `board_key` against
a ledger still keyed by slug finds no prior row for Workday and Personio, so those Boards fall to
their ATS median for one run and re-measure. Not harmful — the EWMA recovers next run — but the
migration is what avoids it, so run it in the same change.

**Resume and cost now use different keys within one shard**, deliberately. Resume answers "did I
already fetch this URL", cost answers "how expensive is this Board". Merging them would make a
resumed shard skip a pod it had not actually read.

**ADR-0049's pairing is retired, not its lesson.** It remains the record of what reading one
ledger with the other's key costs.
