# ADR-0096: One key for both Board ledgers

**Status:** accepted · **Date:** 2026-08-28 · **Supersedes:**
[ADR-0059](0059-two-board-keyspaces.md) (which kept the two keyspaces, deliberately) ·
**Amends:** [ADR-0027](0027-measured-scrape-cost-ledger.md) (the cost ledger's key),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (which taught the planner to *pair* the
two keys rather than unify them), [ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) (whose gate now
takes one key) · **Relates to:**
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
`board_key`. Only Workday and Personio override `board_key()`; every other ATS has nothing to
strip and the two spellings already agree.

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
  two pods — 333 such pairs, counting live liveness rows grouped by case-folded `board_key`
  (a different grouping gives a different number, so the method is part of the figure).

## Why ADR-0059 said no, and what changed

ADR-0059 examined this and kept the split on purpose: *"Anything reading `board_cost.csv` stays on
`f"{ats}:{slug}"`, because that is what `harvest` writes. 'Fixing' it to match the priority ledger
would have broken cost lookups for the same 13,402 boards — the mirror of the bug being fixed."*

That is correct, and it is an argument against changing the **read** side alone. This ADR changes
the **write** side as well, and adds a read-time shim for rows written before it, so there is no
window in which a lookup misses. ADR-0059's objection is answered rather than overruled.

## Decision

**Both ledgers are keyed by `board_identity` (i.e. `board_key`).**

- `harvest` records cost under `board_identity(company)`. A map built once per shard; the
  scraper's own `{ats}:{slug}` stays the working identity for **resume, errors and the `on_board`
  callback**, because those name the URL this shard is actually fetching.
- `scrape_plan` looks the ledger up with the same key, and `_gated_boards` takes one key instead
  of a `(cost_key, priority_key)` pair.
- The ledger already on HF re-keys itself, via a read-time shim — see Migration below.

`board_key` rather than `{ats}:{slug}` because unifying the other way would mean re-keying every
Job id — Workday ids would carry a full URL with `://` and `/` — and re-indexing 330k rows, while
*losing* the 333 pod collapses. `board_key` is also the shorter, readable form, and already the
canonical identity everywhere else: `_dedupe_boards`, `prune`'s keep-set, `PARKED_BOARDS`.

## Migration: a read-time shim, not a script

The first draft of this ADR shipped a one-off migration script and called the gap before it ran
"one run of degraded packing… not harmful". **Review measured that and it was wrong**, in the
direction that matters. Against the live ledger, reading slug-keyed rows with `board_identity`
finds nothing for Workday, so:

- the ADR-0064 value gate stops gating **7 giants totalling 194 min** — `dollartree` (3,233 s),
  `advanceauto` (1,758 s), `lowes` (1,712 s), `oreillyauto`, `trinityhealth`, `michaels`, `circlek`
- `costs_for` then prices each at the Workday median, **4.8 s**

A 54-minute Board packed as 4.8 seconds is the exact makespan failure ADR-0064 exists to prevent.
And it would not have been one run: nothing prunes this ledger, so both spellings would coexist
until someone remembered the script.

So `board_cost.load()` normalises legacy keys on the way in (`_rekeyed`). Measured with the shim,
on an un-migrated ledger: **15 Boards gated, all 7 giants among them, 97.2% hit rate** — identical
to the behaviour before this ADR.

**It is idempotent, which is what lets it sit on the read path.** `board_key()` parses a careers
URL and raises on anything else, so a second pass over an already-converted key keeps it. That same
property is why a *write*-side self-healing migration was rejected — it would have had to track
what it had already converted.

The ledger then migrates itself: `update_ledgers cost` does load → update → save, rewriting every
row, so the file is board_key-keyed after the first run and the shim is a no-op on all of it.
Remove the shim once no ledger in flight predates 2026-08-28. Where a Board appears under both
spellings for that one run, the **newer** measurement wins.

Measured shape of the one-off change: **85,839 rows → 85,825 keys, 14 merged, 0 unmappable**,
72,006 already board_key-shaped. Only 14 collapse, because a pod pair holds two cost rows only when
both URLs were scraped and dedupe usually admits one.

Case is preserved. `board_key()` does not fold it, and the read side does not either:
`_dedupe_boards` lowercases only to *choose* among variants, then keeps that row's own spelling —
the exact string `scrape_plan` looks up. The ADR-0023 case-variant rows are a separate concern and
were measured contributing **0 s** of mispricing.

## Consequences

The two ledgers are now joinable, which is what both people who got this wrong were trying to do.

**Resume and cost now use different keys within one shard**, deliberately. Resume answers "did I
already fetch this URL", cost answers "how expensive is this Board". Merging them would make a
resumed shard skip a pod it had not actually read.

**ADR-0049's pairing is retired, not its lesson.** It remains the record of what reading one
ledger with the other's key costs.
