# ADR-0162: Report the description gap as a drain, and reclassify the Boards that are not Scrapable

**Status:** accepted · **Date:** 2026-09-16 · **Amends:**
[ADR-0062](0062-drain-the-description-gap.md) (adds the drain report its level could not give, and
closes the off-slice hole it named and deferred) · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store whose backlog this counts),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the unauthoritative verdict that pins a
Board's ids), [ADR-0159](0159-a-hard-cap-marks-truncated-an-approximate-ceiling-does-not.md) (the
hard-cap signal a fuller fix would have to make machine-readable)

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
byte-identical in all five runs, and re-reading the live ledger a generation later (HF
`data/state/board_description_gap.csv`, `updated_at=2026-09-16`, 45,423 unsettled across 7,418
Boards) the same eight are *still* byte-identical: `oracle:jpmc-test` 2,692, `eightfold:micron`
1,523, `eightfold:nvidia` 1,378, `eightfold:amdocs-sandbox` 1,357,
`successfactors:careers.hcltech.com` 1,297, `eightfold:microsoft-tm2-dev-sandbox` 1,237,
`successfactors:hcltech.jobs.hr.cloud.sap` 1,172, `eightfold:citigroup-qa-sandbox` 1,127.

**None of this was visible in any single run.** The ledger is recomputed from scratch, so its total
is a *level*. A backlog that lost 500 rows and gained 500 prints exactly the same number as one
nothing touched, and a run that made real progress prints a bigger number than a frozen one
whenever the index grew. Establishing "it is not draining" took a hand diff of five logs. That is
the defect: the quota has been spent every run since 2026-08-18 with no metric able to say whether
it bought anything.

**And the review's own diagnosis of the frozen eight was wrong.** It read the eightfold cluster as
`docs/eightfold/no-client-side-fix-for-replica-instability.md`'s unauthoritative-scrape path.
Measured against the committed liveness ledger instead, every one of `micron`, `nvidia`,
`amdocs-sandbox`, `microsoft-tm2-dev-sandbox`, `citigroup-qa-sandbox`, `qualcomm` and
`nvidia-sandbox` is recorded **`dead`**. They are not being scraped badly; they are not being
scraped *at all*, and nothing can ever settle them.

Joining the live gap ledger to `load_active_companies(min_jobs=0)` — CONTEXT.md's **Scrapable
Board**, the exact call `scrape_plan` makes — gives the size of that class:

| | Boards | Jobs | share of the 45,423 backlog |
|---|---:|---:|---:|
| on a Scrapable Board | 7,284 | 31,831 | 70.1% |
| **not on a Scrapable Board** | **134** | **13,592** | **29.9%** |

The five-run table above is read from run logs, which do not outlive their retention, and from
`experiment/pipeline-review-2026-09-16/` — which is **gitignored**, so neither is reproducible from
this repo. The join below is, from the committed liveness ledger plus one ~1 MB HF file, and it is
what every figure in this ADR that is not from those logs was computed with:

```python
import csv
from huggingface_hub import hf_hub_download
from headstart import board_description_gap
from headstart.board_identity import lower_key
from headstart.config import load_active_companies
from headstart.ingest import board_failures

g = hf_hub_download("imPoseidon/headstart-index",
                    "data/state/board_description_gap.csv", repo_type="dataset")
f = hf_hub_download("imPoseidon/headstart-index",
                    "data/state/board_failures.csv", repo_type="dataset")
rows = {r["board"]: int(r["unsettled"]) for r in csv.DictReader(open(g))}
scrapable = {board_description_gap.key_for(c)
             for c in load_active_companies("data/validate/liveness", min_jobs=0)}
quarantined = {lower_key(b) for b in board_failures.quarantined(board_failures.load(f))}
off = {b: n for b, n in rows.items() if b not in scrapable}
quar = {b: n for b, n in rows.items() if b in scrapable and b in quarantined}
print(len(off), sum(off.values()), len(quar), sum(quar.values()), sum(rows.values()))
# 134 13592 123 908 45423   — the ledger of 2026-09-16
```

ADR-0062 already named this class — "the 166 off-slice Boards holding 15,177 Jobs — dead, excluded
or parked — are counted in the ledger but can never be picked, so the reported backlog is slightly
larger than the drainable one" — and left it in the count. "Slightly" is 30%.

## Decision

### 1. The gap line reports movement, not only a level

`update_ledgers gap` reads the ledger it is about to overwrite — the generation `scrape_plan` built
*this* run's slice from — and prints, beside the existing total:

```
gap: drain vs the 4,600 unsettled across 3 boards this run read:
     2,000 left the gap, 1,200 joined it, net -800
```

and gives each of the top-10 Boards its own delta (`2,692 unsettled (+0)  oracle:jpmc-test…`),
which is the per-Board form of the same evidence: a frozen Board says `(+0)` on its own line
instead of needing five logs side by side.

**`left the gap`, not `settled`.** The register asked for the word "settled" and it would have been
wrong. A row also leaves this count when it is reclassified unreachable — a disabled ATS, an
off-slice Board, or #185's expiry, whose population moved by ±1,700 across the five runs above —
or when its row leaves the store. None of those fetched a description. The settle rate is
`update_descriptions`' own `learned` count; this line reports movement, and its job is to tell
draining from frozen.

It is also a **per-Board** drain, because the ledger stores counts and not ids: a Board that lost
five and gained five nets to zero on both sides. That is a floor on the churn, which is enough for
that job, and the module says so rather than implying id-level precision it does not have.

A run with **no prior ledger, or an empty one**, prints `no prior ledger to compare against`
instead of numbers. Treating either as a prior of zero would print the entire backlog as inflow —
a spike that never happened.

### 2. A Board no scrape slice can contain is `unreachable`, not unsettled

`gap` now builds the Scrapable Board set from `load_active_companies(min_jobs=0)` — the same call
and the same `min_jobs` as `scrape_plan`, keyed through `board_description_gap.key_for` — and
counts a row whose Board is absent from it alongside the disabled-ATS and expired classes rather
than in the backlog.

That set is a **superset** of what a run finally offers. `scrape_plan` filters twice more after
this call: quarantined Boards (`board_failures.quarantined`) and ADR-0064's value gate. So a
handful of Boards this counts reachable would still be refused — measured, 123 Boards holding 908
Jobs, **2.0%** of the backlog, all of it quarantine. The error is deliberately in that direction:
it never calls a Board unreachable that a run could pick, and the converse is all the
classification needs. The quarantine arm is left out on its own evidence: it buys 2% from a
mechanism with **no drain at all** (755 Boards, zero clears in five runs — finding 2 of the same
review), so reclassifying on it would hide those rows permanently, which is exactly what this
decision rejects elsewhere. The value gate is per-run and not a property of the Board at all.

This is reliably derivable in a way no scrape-outcome heuristic is: `dead`, `parked`, aliased-away
and vendor-test are verdicts the liveness ledger already carries, and a Board absent from that list
is not merely unlikely to be scraped, it is outside the Scrapable set entirely. **And it drains.** The gap ledger is
rebuilt from scratch every run, so the moment a liveness probe calls a Board live again its rows
come straight back — unlike quarantine, which is the one-way door finding 2 of the same review
measured at 755 Boards with zero clears.

**Fail-open guard.** `load_active_companies` answers `[]` for a missing directory, which is
indistinguishable from "no Board is live". An empty answer therefore reclassifies nothing and
warns, because acting on it would mark the entire backlog unreachable and hand the next run a
quota of nothing. A *partial* loss is not caught — see Consequences.

### 3. The rest of the stuck class is measured, not reclassified

`gap` also counts the unsettled Jobs whose Board this run attempted and could not read
authoritatively (membership in `unauthoritative_boards.json`, resolved by prefix per ADR-0049,
the same test `_authoritative_scrape` already makes):

```
gap: 5,711 unsettled Job(s) sit on 42 Board(s) whose scrape this run was not authoritative
     (ADR-0053), so this run is no evidence about them either way
```

(those two figures are the live measurement below, not an example)

They keep their count and their quota. **Nothing is reclassified on that signal.**

## Why not reclassify on scrape authority too

Joining the live gap ledger to the live `unauthoritative_boards.json` of the same generation: 42
gap Boards holding **5,711 Jobs (12.6% of the backlog)** were unauthoritative that run. Reading
their recorded reasons is what settles the question:

| Board | unsettled | reason |
|---|---:|---|
| `oracle:jpmc-test.fa.oraclecloud.com` | 2,692 | `read 10000 of 10150 requisitions — the API serves no offset past 10,000` |
| `successfactors:careers.hcltech.com` | 1,297 | `1838/11207 job pages unreadable` |
| `workday:rbc/rbcglobal1` | 248 | `ConnectionError: … curl: (56) Connection closed abruptly` |
| `workday:hp/externalcareersite` | 233 | `2 of 43 page(s) failed mid-crawl (ConnectionError x2)` |
| `workday:hcmportal/search` | 206 | `2 of 79 page(s) failed mid-crawl (ConnectionError x2)` |
| … 37 more, mostly the same `ConnectionError` shape | | |

Only the first is structural. Most of the class is a transient mid-crawl connection failure that
the next run will not repeat, and writing those Jobs off would delete a repair that currently
works. A single run's unauthoritative verdict is not evidence about any particular id, which is
what `gap` has always said and what stays true.

Deriving the one structural mechanism is not free either. "This Board hit a hard cap" is a real,
deterministic property — ADR-0159 makes the exact-vs-approximate split the scraper's own decision —
but the outcome it produces is still free text inside `BaseScraper.truncated`. Reading it back
would mean matching on that wording, which is the shape of `board_failures.is_gone`, whose literal
`"HTTP Error 404"` already silently misses `BrowserHTTPError`'s `"HTTP 404"`. Doing it properly
means a structured channel through `mark_truncated` → `harvest.RunResult.truncated` →
`ShardReport` → `scrape_join` → a new state file, plus a per-call-site audit of ~20 scrapers. That
is ADR-0159's territory, and it should be taken on its own evidence.

And a hard cap on its own does not prove any *particular* id unreachable: the capped window is the
first 10,000 by the API's own ordering, so an unsettled id may sit inside it with a failed detail
fetch. The honest rule would be "capped Board **and** not re-emitted this run" — #185's `expired`
shape on weaker evidence — which is another decision, not a corollary.

The third mechanism in the review's ~12,100 estimate is neither: `tesla` (1,538 of 1,538
unrecorded, in no `detail loss events` line) has no description path at all. That is a scraper gap,
and no classification keyed on scrape authority would ever touch it.

## Consequences

* The reported backlog drops ~30% — from 45,423 to ~31,831 on the ledger measured here — and that
  is the honest number: the 13,592 it removes were never drainable. **It does not free slice
  slots.** `_gap_picks` filters its candidates to the live company list already, so an off-slice
  Board was never occupying one of the ~2,130. The register's premise that those slots are
  re-reserved for the frozen eight is false for the eightfold cluster; it remains true for
  `oracle:jpmc-test` and `tesla`, which are both live and both still picked.
* `scripts/runlog/fanout_ledgers.py` surfaces all of it. Two lines are **optional by design** — the
  drain line and the per-Board deltas are absent with no prior ledger, the blocked line is absent
  at zero — so its patterns treat them as optional and it says which is missing rather than
  reporting a parse failure. (CLAUDE.md's own "optional log fields vanish silently" rule; a
  required group there would drop every top-10 row of a first run.) `tests/test_log_contract.py`
  pins every one of these lines against the real emitter.
* The drain is computed against **the ledger this run read**, not against the previous run. When a
  merge fails and state does not reach HF — finding 1 of the same review, where R3 re-read R1's
  state — the comparison silently spans two runs. The line's wording says "this run read" for that
  reason, and the ledger's `updated_at` column is where the generation is recoverable.
* `gap` now reads `data/validate/liveness/`, which is committed to git and so is always present in
  the join. It is the same read `scrape_plan` already does each run.
* The fail-open guard catches a **total** loss of that directory, not a partial one. A single
  absent or unparseable `{ats}.csv` — `config.load_active_companies` warns and skips a file whose
  stem is not a registered ATS — would leave the set non-empty and silently take that ATS's whole
  backlog with it. Accepted rather than fixed here: `scrape_plan` reads the same directory and
  would equally refuse those Boards, so the gap ledger would still be describing the slice it
  actually gets. Named because nothing counts it.

## Alternatives considered

* **Reclassify on this run's unauthoritative verdict** — one line, no new state, and wrong: the
  live measurement above shows the class is dominated by transient `ConnectionError`. The current
  behaviour is pinned by `test_gap_keeps_the_ids_of_a_board_whose_scrape_was_not_authoritative`,
  and it stays.
* **A per-Board "consecutive unauthoritative" streak ledger**, the direct analogue of
  `board_failures.csv` — the shape a fuller fix would take, but it is a fifth state file on the HF
  round-trip and it carries quarantine's own one-way-door risk (755 Boards, zero clears in five
  runs). Worth building on a measured denominator, which the blocked line now supplies.
* **Report the drain as a cumulative counter in the ledger** — survives a stale-state hop, but adds
  a column to a file three stages read and makes the ledger stateful when its whole design is to be
  recomputed from scratch (ADR-0062).
* **Drop off-slice Boards from `board_description_gap.save` instead of from the count** — same
  ledger, but the *reason* a row vanished would be invisible, and the header count is what makes
  the 30% legible.
* **Diff the ledgers in `fanout_ledgers.py` instead of in the emitter** — no production change at
  all, but the ledger does not ride the run logs, so the tool would need the HF state of two runs;
  and the number is wanted in the run that produced it, which is the whole complaint.
