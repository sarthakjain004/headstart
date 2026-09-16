# ADR-0162: Report the description gap as a drain, and measure the stuck class before classifying it

**Status:** accepted · **Date:** 2026-09-16 · **Amends:**
[ADR-0062](0062-drain-the-description-gap.md) (which reserves the slice quota this reports on) ·
**Relates to:** [ADR-0050](0050-persist-descriptions-across-runs.md) (the store whose backlog this
counts), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the unauthoritative verdict that
pins a Board's ids),
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (which narrowed that
verdict but left the hard-cap arm unconditional)

## Context

ADR-0062 reserves `GAP_FRAC` of every run's exploration tail for Boards holding unsettled
descriptions and predicted the backlog would drain in ~18 runs. It has not.

Measured across the five runs of 2026-09-16 (`experiment/pipeline-review-2026-09-16/artifacts/
join-stage.md`, finding 4 — runs `35058831217`, `35063022985`, `35067130555`, `35071940457`,
`35078301418`):

| | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|
| unsettled Jobs | 47,726 | 45,960 | 45,738 | 46,669 | **48,407** |
| gap Boards | 7,376 | 7,411 | 7,386 | 7,378 | 7,361 |
| slice slots given to gap Boards | 2,128 | 2,138 | 2,128 | 2,134 | 2,136 |

**Net +681 over ~4h50m of continuous running** — it grew. Eight of the top ten gap Boards are
byte-identical in all five runs (`oracle:jpmc-test` 2,692, `eightfold:micron` 1,523,
`eightfold:nvidia` 1,378, `eightfold:amdocs-sandbox` 1,357, `successfactors:careers.hcltech.com`
1,297, `eightfold:microsoft-tm2-dev-sandbox` 1,237, `successfactors:hcltech.jobs.hr.cloud.sap`
1,172, `eightfold:citigroup-qa-sandbox` 1,127). Store-wide, `embed_plan`'s `prior store` grew
84,332 → 84,383 embedded ids with no description against 2–6 ADR-0050 upgrades per run.

Two facts matter more than the size.

**First, none of this was visible in any single run.** The ledger is recomputed from scratch, so
its total is a *level*. A backlog that settled 500 and gained 500 prints exactly the same number
as one nothing touched, and a run that made real progress prints a bigger number than a frozen one
whenever the index grew. Establishing "it is not draining" took a hand diff of five logs. That is
the defect: the quota has been spent every run since 2026-08-18 with no metric able to say whether
it bought anything.

**Second, at least part of the backlog is structurally unsettleable.** Confirmed for the largest
Board: `oracle:jpmc-test.fa.oraclecloud.com` returns exactly 10,000 jobs on every run (Oracle's
offset ceiling, `scrapers/oracle._OFFSET_CEILING`), which calls `mark_truncated` unconditionally —
correctly, per ADR-0121's hard-cap carve-out — which makes the Board unauthoritative, which keeps
it out of `update_ledgers gap`'s `_authoritative_scrape`, which means its 2,692 ids can neither be
settled nor reclassified `expired` under #185. They are pinned, and the Board is re-reserved a
slice slot every run regardless.

## Decision

### 1. The gap line reports movement, not only a level

`update_ledgers gap` reads the ledger it is about to overwrite — which is the generation the
*current* run's plan was built from — and prints, beside the existing total:

```
gap: drain vs the 46,669 unsettled across 7,378 boards this run read:
     1,204 settled, 2,942 newly unsettled, net +1,738
```

and gives each of the top-10 Boards its own delta (`2,692 unsettled (+0)  oracle:jpmc-test…`),
which is the per-Board form of the same evidence: a frozen Board says `(+0)` on its own line
instead of needing five logs side by side.

It is a **per-Board** drain, because the ledger stores counts and not ids: a Board that settled
five and gained five nets to zero on both sides. That is a floor on the churn, which is all that
is needed to tell draining from frozen, and the module says so rather than implying id-level
precision it does not have.

A run with **no prior ledger** prints `no prior ledger to compare against` instead of numbers.
Treating a missing file as a prior of zero would print the entire backlog as inflow — a spike that
never happened.

### 2. The stuck class is measured, not reclassified

`gap` also counts the unsettled Jobs whose Board this run attempted and could not read
authoritatively (membership in `unauthoritative_boards.json`, resolved by prefix per ADR-0049,
the same test `_authoritative_scrape` already makes):

```
gap: 12,104 unsettled Job(s) sit on 83 Board(s) whose scrape this run was not authoritative
     (ADR-0053), so this run is no evidence about them either way
```

They keep their count and their quota. **Nothing is reclassified `unreachable`.**

## Why not reclassify

The review asked for an `unreachable` class covering ~12,100 Jobs (~25% of the backlog). That
number is an aggregate over three different mechanisms, and only one of them is confirmed:

* **A hard offset cap** (`oracle:jpmc-test`, 2,692) — confirmed, and genuinely repeat-identical.
* **eightfold replica instability** (~7,700 across micron / nvidia / amdocs-sandbox /
  microsoft-tm2-dev-sandbox / citigroup-qa-sandbox) — *inferred*. eightfold reports 1–4 `partial`
  Boards per run but the logs never name which, and
  `docs/eightfold/no-client-side-fix-for-replica-instability.md` describes a Board that
  oscillates. A flaky replica is exactly the case that must **not** be written off.
* **No description path at all** (`tesla`, 1,754 of 1,754 unrecorded in five runs, appearing in no
  `detail loss events` line) — neither capped nor unauthoritative. It is a scraper gap, and an
  `unreachable` class keyed on scrape authority would never touch it.

Deriving the one confirmed mechanism is not free either. "This Board hit a hard cap" is a real,
deterministic property, but today it exists only as free text inside `BaseScraper.truncated` —
`mark_truncated`'s docstring names the hard cap as a distinct shape while the value it stores is
the caller's own wording. Reading it back would mean matching on that wording, which is the exact
shape of `board_failures.is_gone`, whose literal `"HTTP Error 404"` already silently misses
`BrowserHTTPError`'s `"HTTP 404"`. Doing it properly means a structured channel through
`mark_truncated` → `harvest.RunResult.truncated` → `ShardReport` → `scrape_join` → a new state
file, plus a per-call-site audit of ~20 scrapers. That is a cross-cutting schema change, and it
should be taken on its own evidence, not folded into a reporting fix.

And a hard cap on its own does not prove any *particular* id unreachable: the capped window is the
first 10,000 by the API's own ordering, so an unsettled id may sit inside it with a failed detail
fetch. The honest rule would be "capped Board **and** not re-emitted this run" — #185's `expired`
shape on weaker evidence — which is another decision, not a corollary.

So the class is measured first. The `blocked` count is an upper bound on it, per run, from data
already on disk; after a few runs it gives whoever builds the classification a denominator instead
of a five-log hand diff.

## Consequences

* Log-only. The ledger's contents, the slice, the quota and every stored artefact are byte-for-byte
  what they were; the ~2,130 slots are still spent on the stuck Boards. This change makes that
  visible, it does not stop it.
* `scripts/runlog/fanout_ledgers.py` surfaces all three additions. Two of them are **optional by
  design** — the drain line and the per-Board deltas are absent with no prior ledger, the `blocked`
  line is absent at zero — so its patterns treat them as optional and it says which is missing
  rather than reporting a parse failure. (CLAUDE.md's own "optional log fields vanish silently"
  rule; a required group here would drop every top-10 row of a first run.)
* The drain is computed against **the ledger this run read**, not against the previous run. When a
  merge fails and state does not reach HF — finding 1 of the same review, where R3 re-read R1's
  state — the comparison silently spans two runs. The line's wording says "this run read" for that
  reason, and the `updated_at` column of the ledger is where the generation is recoverable.

## Alternatives considered

* **Reclassify on this run's unauthoritative verdict alone** — one line, no new state, and wrong:
  a 429 or a single flaky replica read would write off Jobs the next run settles. The current code
  deliberately leaves those unsettled (pinned by `test_gap_keeps_the_ids_of_a_board_whose_scrape_was_not_authoritative`), and that
  stays.
* **A per-Board "consecutive unauthoritative" streak ledger**, the direct analogue of
  `board_failures.csv` — this is the shape a real fix would take, but it is a fifth state file on
  the HF round-trip and it carries quarantine's own one-way-door risk (finding 2 of the same
  review: 755 Boards that can never be un-quarantined because nothing re-probes them). Worth
  building on a measured denominator, not on a five-run sample.
* **Report the drain as a cumulative counter in the ledger** — survives a stale-state hop, but adds
  a column to a file three stages read and makes the ledger stateful when its whole design is to be
  recomputed from scratch (ADR-0062).
* **Diff the ledgers in `fanout_ledgers.py` instead of in the emitter** — no production change at
  all, but the ledger does not ride the run logs, so the tool would need the HF state of two runs;
  and the number is wanted in the run that produced it, which is the whole complaint.
