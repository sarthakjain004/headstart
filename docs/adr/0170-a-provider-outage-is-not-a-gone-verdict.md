# ADR-0170: A provider outage is not a gone-verdict, so the quarantine ledger gets no terminal drain yet

**Status:** accepted · **Date:** 2026-09-21 · **Amends:**
[ADR-0162](0162-a-gone-verdict-expires-quarantine-parole.md) (its parole is confirmed working and
stays the only drain; its 3.0%-live measurement is superseded) ·
**Amended by:** [ADR-0206](0206-prune-evicts-a-board-parole-reconfirmed-gone.md) (served rows get a drain on prerequisite 3 alone: a verdict
parole re-earns a week later evicts; the zwayam cohort, all at five strikes, keeps its rows) ·
**Relates to:** [ADR-0058](0058-consecutive-gone-quarantine.md) (the five-strike premise this
falsifies for correlated failures),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the other withholding mechanism with no
drain — same shape, and the same reason not to force one)

## Context

The quarantine ledger has a redemption path (ADR-0162's weekly parole) but no **terminal** drain: a
Board that is gone forever keeps a row forever and pays one dead round-trip per parole cycle
forever. Measured over the 10 most recent `pipeline.yml` runs the standing total moves ≈ +1 / −0 per
run and `cleared` reads 0 in every one, so the set looked like it only grows. This ADR was opened to
give it a drain. **The measurement refused the change.** Full workings:
`experiment/quarantine-drain/LOG.md`.

### The ledger, read from the file rather than the log

`data/state/board_failures.csv` pulled from HF 2026-09-21: **898 rows, 882 quarantined (98.2%)** —
184 at 5 strikes, 698 at 6. By ATS it is **greenhouse 326**, ashby 173, lever 97, teamtailor 82,
zwayam 59, personio 56, the rest under 30. The log's own sample list says "ashby" only because the
emitter sorts alphabetically and caps at 20; it is not a distribution and must not be read as one.
One error class carries everything: 745 bare `HTTPError: HTTP Error 404:` plus 153 of lever's
`no Lever board for {slug}` spelling of the same verdict. No 410.

The verdicts are **fresh**, not stale: `now − last_seen_gone` is p50 4.57 d, max 7.54 d, with 896 of
898 under seven days. That is parole re-confirming every row weekly, which is also why only 2 rows
are parole-eligible at any instant and the log reads "1 re-admitted".

### `0 cleared` is an undersampled rate, not a broken mechanism

`cleared` counts rows a successful scrape cleared *in that run*. Of the 23 Boards ADR-0162's
2026-09-16 probe found answering 200, **22 are gone from the ledger five days later**; the one
remaining (`ashby:todyl`) was cleared and then re-quarantined. 22 clears over ≈120 runs is
≈0.18/run, so a 10-run window reading zero every time is the expected observation. Parole works. The
set grows net because new Boards arrive faster than recoveries leave.

### The re-probe: 26% of the quarantine is live, and it is one outage

Joining the ledger to `board_priority.csv` (whose `updated_at` is the last snapshot that *included*
the Board) splits the quarantine into three strata. A stratified sample — all 80 recent-producers,
60 of 204 stale-producers, 100 of 598 never-produced, seed 20260921 — was re-probed through
`scripts/validate/recheck_boards.py`, i.e. through `check_liveness.PROBES`, the same functions the
ledger-wide liveness sweep uses.

| stratum | population | n | live | dead | unknown | live % |
|---|---|---|---|---|---|---|
| recent-producer (priority row ≥ 2026-09-15) | 80 | 80 | **60** | 20 | 0 | **75.0%** |
| stale-producer | 204 | 60 | 1 | 58 | 1 | 1.7% |
| never-produced (no priority row) | 598 | 99 | 2 | 89 | 8 | 2.0% |
| **total** | **882** | **239** | **63** | **167** | **9** | **26.4%** |

Stratum-weighted that is **≈75 of 882 quarantined Boards live right now (9%)**, against the 3.0%
ADR-0162 measured five days earlier, and the 63 measured-live Boards carry **13,032 open postings**.
A 239-Board stratified sample is evidence, not proof — but the effect is far outside sampling noise
and its mechanism is identifiable.

`live` by ATS is **zwayam 58 / dead 0**; every other ATS combined is 5 live / 167 dead. The zwayam
cohort is 59 rows — essentially zwayam's whole presence in the ledger — and every one of them sits
at exactly 5 strikes (first-time quarantine), was struck in one of three consecutive runs (55 share
`2026-09-19T21:14:57`), carries a priority row updated **2026-09-19** so it produced tech jobs the
same day it was struck out (3,898 tech jobs across the cohort), and answers live today: Persistent
(706 postings), Microland (1,127), Adani (1,607), EAPL (1,776), EPAM, CRISIL, Coforge, Cyient,
Happiest Minds, ITC Infotech, Samsung, Airbus, BMW TechWorks.

**A zwayam outage on 2026-09-19 404'd the provider for long enough that every zwayam Board in the
slice took five consecutive strikes and quarantined together.** ADR-0058's premise — five
consecutive 404s means *this Board* no longer exists — holds for a Board failing alone and is
falsified for a Board failing alongside its whole provider. Five consecutive scrapes is five *runs*,
≈5 hours at the measured ≈60 min/run, which one afternoon's provider fault clears easily.

Read the clustering carefully: `last_seen_gone` is one stamp per run, so *any* Boards struck in the
same run share it, and the largest non-zwayam clusters (greenhouse 51 + ashby 31 + lever 18 at
`2026-09-16T17:20:33`) are only ADR-0162's 652-Board first-parole cohort being re-struck. What
identifies zwayam is the conjunction of whole-provider, all-first-time, all-producing-that-day and
all-live-now — not the shared timestamp.

## Decision

**No terminal drain ships.** ADR-0162's parole remains the ledger's only drain, and it is the
correct one for now. Both candidates were priced and both are refused:

**(a) Promote a confirmed-gone Board to `dead` in `data/validate/liveness/<ats>.csv`** — refused on
the measurement. On today's ledger it delists the ≈75 live Boards above (63 measured, 13,032
postings), and because `dead` drops the Board from `load_active_companies` it also drops from
`index_plan.live_keep_set`, so the next `index prune --apply` evicts every one of their served rows
as off-Board — 6,049 tech rows across the 282 quarantined Boards that have a priority row, of which
the all-live zwayam cohort alone is 3,898. It is also the least reversible option in the repo: the
liveness ledger is committed to git and only the offline probes under `scripts/validate/` write it,
so the pipeline cannot undo a wrong delisting and parole cannot reach a Board that has left the
candidate set. ADR-0058 kept quarantine away from liveness for exactly this reason, and that call
stands. It would convert a bounded ~1-week coverage gap that parole already heals into a permanent
delisting plus an index eviction.

**(b) Expire stale quarantine rows out of the failures ledger** — rejected as accounting. Deleting a
row returns the Board to the slice at 0 strikes; a genuinely-gone Board 404s its way back to 5 and
re-enters the ledger. The standing total dips and climbs back, at 5 dead requests per Board per
cycle against parole's 1, to learn what parole already learned. It changes the number the log
prints, not the set of Boards scraped.

**The prerequisite is evidence, not machinery.** `recheck_boards.py` (measure, LIVE/DEAD/UNKNOWN
through the sweep's own probes) and `relocate_dead_boards.py --apply` (mark `dead`, after checking
whether the employer merely changed ATS — that check found 46 of 292 had moved) already implement
this path end to end. Three things must land before it may be pointed at the quarantine:

1. **A correlated-gone guard in `update_ledgers failures`**, so an ATS-wide fault never becomes a
   per-Board verdict.
2. **Delisting keyed on the liveness probe's DEAD verdict**, never on the scrape's recorded reason,
   and refusing `unknown` — 9 of 239 here, all personio, whose 56 quarantined rows the probe cannot
   place at all (a Personio slug is a whole host while the ledger carries bare tenants).
3. **Repeated agreement over time** — two DEAD verdicts a week apart. 20 of the 80 recent-producers
   probed dead, so "produced last week" is not a reliable liveness signal in either direction.

### Why the guard is not in this change

Because it cannot be calibrated yet: there is **one** outage in the data. Any threshold — "x% of an
ATS's scraped Boards reported gone this run" — fitted to zwayam would be a number derived from a
single event and then enforced by tests, which is the one-sample generalisation this repo has
already paid for twice (`docs/icims/`'s `datePosted` census exists because a single board's defect
became a provider-wide absolute). The honest order is: measure the correlation across a population
of outages first, then set the threshold, then build the drain on top of it. Guessing it here would
put a recall-affecting guard in the scrape path on n=1.

## Options considered

1. **No drain; parole stays the only one, and the preconditions are recorded (chosen).** Costs the
   882 rows × 1 probe / 7 days ≈ **126 dead requests/day**, growing ≈ +25/day — real, unbounded, and
   slow. Buys not delisting 75 live Boards and not evicting their served rows on evidence that is
   26% wrong.
2. **Delist only the `never-produced` stratum** (598 Boards, 2.0% live), on the theory that a Board
   which never contributed a tech job cannot cost coverage. Rejected on its own numbers: 2.0% of 598
   is ≈12 live Boards permanently delisted, and ADR-0162 built the entire parole mechanism to
   recover *12 Boards / 264 tech postings*. This option would re-create precisely the loss that ADR
   exists to prevent. Worse, both substantial live Boards in the sample sit in this stratum —
   `phenom:careers.merckgroup.com` at 849 postings and `rippling:kindthread` at 52 — because a
   brand-new scraper's Boards have no priority row yet, so "never produced" selects for recent
   bring-up as much as for absence.
3. **Ship the correlated-gone guard now and defer the drain.** Tempting: it is the actual defect,
   it is small, and it is recall-safe in direction. Rejected because the threshold is uncalibrated
   at n=1 (above), and a guard that withholds strikes on a wrong threshold silently stops
   quarantining real dead Boards — a failure that reports nothing.
4. **Raise `QUARANTINE_AT` from 5 so an outage cannot clear the bar.** Rejected: it does not
   discriminate, it only buys time. A longer outage still quarantines the whole provider, while
   every genuinely dead Board pays the extra strikes. The signal that separates the two cases is
   correlation across Boards, not count per Board.
5. **Exclude the zwayam cohort by hand and drain the rest.** Rejected: a one-off edit that fixes
   today's ledger and leaves the next outage to do the same damage, and it reads the sample as a
   census — 5 of the live Boards found are not zwayam.

## Consequences

- **The zwayam cohort needs no action.** Parole re-admits each Board within 7 days of its strike and
  their priority scores (up to 514 tech jobs) put them in the head, so they are selected
  immediately and cleared on their first successful scrape. Expect the quarantine total to fall by
  ≈59 without intervention; if it does not, parole is broken and that is the bug to chase.
- **The ledger total will keep climbing**, and that is now a known accepted cost with a recorded
  reason, not an unexamined one. It is the same shape as ADR-0053's undrained scope exclusion, and
  for the same reason: forcing a drain without trustworthy evidence trades an invisible cost for a
  user-visible one.
- **ADR-0162's 3.0%-live figure is superseded, and the method with it.** A single unstratified sweep
  of the whole ledger averaged a 75%-live stratum into a 2%-live one and reported 3%. Any future
  measurement of this ledger must stratify on `board_priority.updated_at`, and must report the live
  share **per ATS** — one provider at 58/58 was invisible in the aggregate.
- **`update_ledgers failures`'s sample list is alphabetical and capped at 20.** It has now misled
  one investigation about which ATS dominates. Read the state file, not the log, for any claim about
  the population.
- Nothing in the scrape, index or liveness path changes, so there is no eviction, no slice movement
  and no board-count change to reconcile.
