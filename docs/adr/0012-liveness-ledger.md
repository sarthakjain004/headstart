# ADR-0012: Liveness state as a TTL'd ledger keyed by `(ats, tenant)`

- Status: Accepted
- Date: 2026-07-02

## Context

`check_liveness.py` classified each board Live / Dead / Unknown and wrote the results to
`active/{ats}.csv` (live + job count), `active/.{ats}_dead` (a dotfile of dead tenants), and
`active/unresolved.csv` (still-unknown). A re-run skipped any tenant already in the live or dead
set and probed only the remainder — so it was *already* incremental for newly-discovered
companies. But the design had three problems that surface as discovery keeps adding companies to
the merged pool:

1. **Verdicts are frozen.** Once a board is Live it is settled and never re-probed, so its job
   count never refreshes. A Live-but-empty board (a 200 with zero jobs — kept Live, correctly)
   that later opens roles would never be noticed; a Live board whose company later leaves the ATS
   (now 404) stays a false-Live forever. There is no freshness dimension at all.
2. **State is scattered** across `active/{ats}.csv` + a hidden `.{ats}_dead` dotfile + a combined
   `unresolved.csv`, and the merged pool itself carries no verdict — so you can't look at a board
   and know its status without cross-referencing three files.
3. **It can't just move into the pool.** The obvious fix — a `status` column on
   `data/ats-tenants-merged/{ats}.csv` — fails because that pool is *rebuilt* by the merge scripts
   (`merge_tenants.py` regenerates it from scratch; `merge_harvest_into_tenants.py` and
   `merge_cc_into_tenants.py` rewrite it in place), so the verdict would be wiped on the next
   discovery/merge and every merge script would have to learn to preserve it. Discovery produces
   the pool; Liveness validates it — coupling the two stages into one file is the wrong seam.

## Decision

Hold liveness state in a **separate, committed ledger, one CSV per ATS**, at
`data/validate/liveness/{ats}.csv`:

```
ats,tenant,url,status,jobs,checked_at
```

keyed by `(ats, tenant)` — the same identity the old skip used, so no new reconciliation. `status`
is the CONTEXT.md three-state (`live` | `dead` | `unknown`) now persisted for *every* board;
`jobs` is the count at the last Live probe; `checked_at` (ISO date) is the freshness key that makes
everything else work.

**Re-probe policy (TTL).** A board is probed this run iff it is new to the ledger or its
`checked_at` is older than a per-status TTL:

| ledger state | TTL (env-tunable) |
| --- | --- |
| absent | always probe |
| `unknown` | `HEADSTART_UNKNOWN_TTL_DAYS` (default **3**) — see the amendment below |
| `live` | `HEADSTART_LIVE_TTL_DAYS` (default **7**) — refresh counts, catch Live→Dead |
| `dead` | `HEADSTART_DEAD_TTL_DAYS` (default **90**) — 404/NXDOMAIN rarely reverses |

`--force` re-probes everything. So adding companies to the pool never re-checks the whole thing,
and a settled board still refreshes on a cadence — fixing problem 1.

**The Active list becomes a view.** `config.load_active_companies` reads the ledger and yields the
boards with `status == live` and `jobs >= min_jobs`. The dead set is `status == dead`, the
unresolved set is `status == unknown` — the three scattered files collapse into one source of
truth (problem 2). The pool stays pure discovery output and merges never touch verdicts (problem 3).

**Migration.** A one-time `seed_liveness_ledger.py` reads the existing `active/{ats}.csv` (→ live)
and `active/.{ats}_dead` (→ dead) into the ledger, stamping `checked_at` from those files' date, so
the tens of thousands of already-computed verdicts carry over — dead stays cached, live falls due
for a refresh under the TTL.

## Rejected alternatives

- **A `status` column on the merged pool** — the pool is rebuilt by the merge scripts, so verdicts
  would be wiped; and it couples Discovery to Liveness. This is the whole reason for a separate ledger.
- **`checked_at` bolted onto today's `active/` files** — the smallest change and it would fix the
  freshness gap, but state stays split across three files and the dead dotfile carries no URL.
- **Fold liveness into the scrape run** — the scraper already hits every board, so a 404 there is a
  Dead signal and a returned list is a fresh count; writing verdicts back during the scrape would
  remove the separate pass entirely. The right eventual shape, but a bigger change — deferred.

## Consequences

Single source of truth for liveness; `active/{ats}.csv`, `.{ats}_dead`, and `unresolved.csv` are
superseded (left in place for now as the migration source and safety net; removable in a follow-up).
`config` gains a small dependency on `headstart.liveness` (pure CSV I/O + the TTL policy, no
network). The freshness refresh re-probes the whole Live set at once when the TTL lapses; scoping
that to the served/scraped subset is a future refinement. `sensehq` has a scraper but no liveness
probe yet, so it has no ledger — a pre-existing gap, unchanged here.

## Amendment (2026-08-14): `unknown` gets a TTL instead of "always probe"

The table above originally read **`absent / unknown` | always probe**. `unknown` now has its own
TTL, `HEADSTART_UNKNOWN_TTL_DAYS`, defaulting to **3 days**.

"Always probe" was the right instinct — we genuinely do not know, and a board that timed out today
may answer tomorrow — but it assumed every `unknown` is *transient*. Some are structural. A
Workday board answering `403` is outside the conclusive `_WD_GONE` set (`404`/`410`/`422`), so no
datacenter in the sweep can ever settle it; it survives all four escalating passes and is retried
in full on the very next run, forever. The same holds for a SmartRecruiters slug answering `400`
and for hosts that simply never respond. Measured on a 120-per-ATS sample (2026-08-14): ~140 such
boards, each costing the full four-pass escalation to learn nothing that changed.

Three days keeps the meaning intact. `unknown` still means "ask again soon" — the board is
re-probed roughly twice a week, and it is still never allowed to settle into a false `dead`, which
is the invariant this ADR exists to protect. What changes is only that we stop paying the entire
retry cascade on every run for a board whose answer has not moved in months. The floor matters:
this must stay far below `dead`'s 90 days, or `unknown` quietly becomes a verdict rather than the
absence of one. `tests/test_liveness.py::test_unknown_ttl_is_far_shorter_than_dead` pins that.

The alternative considered and rejected was widening the conclusive sets — treating Workday `401`
/`403` and SmartRecruiters `400` as definitive `dead`. That settles them permanently and costs
nothing, but a `403` is frequently a bot wall rather than an absent board (this same change added
bot-wall detection precisely because walls masquerade as refusals), so it manufactures exactly the
false-dead this ADR was written to prevent — and a false `dead` hides a real company for 90 days.
