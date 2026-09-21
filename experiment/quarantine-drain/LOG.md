# Can the quarantine ledger be drained? — live re-probe, 2026-09-21

**Question.** ADR-0058 quarantines a Board after five consecutive 404/410 scrapes; ADR-0162 gave
that verdict a weekly expiry (parole) so a revived Board can come back. Neither gives the ledger a
**terminal** drain: a Board that really is gone forever keeps a row forever, and pays one dead
round-trip per parole cycle forever. Measured across the 10 most recent `pipeline.yml` runs the
standing total moves ≈ +1 / −0 per run and `cleared` reads 0 in every one, so the set grows without
bound. Before building a drain, measure (a) the real distribution of the ledger, and (b) whether
the Boards in it are actually gone.

**Data.** `data/state/board_failures.csv` and `data/state/board_priority.csv` pulled from HF
(`imPoseidon/headstart-index`, `snapshot_download` of `data/state/*`, **2026-09-21 18:11 UTC** —
the working tree's copy is gitignored and stale by construction). 898 rows.

## 1. The real distribution

The `update_ledgers failures` log samples quarantined Boards **alphabetically, capped at 20**, so
the log says "ashby" and tells you nothing about the population. From the file itself:

| strikes | rows |
|---|---|
| 1–4 (still accruing) | 16 |
| **5** | **184** |
| **6** | **698** |
| **quarantined (≥5)** | **882 of 898 (98.2%)** |

Nothing reaches 7: ADR-0162 shipped 2026-09-16, so every row has had at most one parole cycle, and
strike 6 *is* that cycle's re-confirmation.

**By ATS** (all rows / quarantined) — greenhouse dominates, not ashby:

```
greenhouse 334/326   ashby 175/173   lever 98/97   teamtailor 82/82   zwayam 61/59
personio   56/56     recruitee 30/29 rippling 17/17 trakstar 17/17    workable 13/12
workday     9/8      icims 4/4       eightfold 1/1  phenom 1/1
```

**By error class** — one class, essentially: 745 rows are a bare `HTTPError: HTTP Error 404:`; the
remaining 153 are lever's `RequestException: HTTP Error 404: no Lever board for {slug}`, which is
the same verdict with the slug interpolated. No 410 anywhere.

**Age of the verdict** (`now − last_seen_gone`, n=898, 0 unparseable): min 0.10 d, p50 **4.57 d**,
max **7.54 d**; 896 rows under 7 days. That is not "old evidence" — it is ADR-0162's parole
keeping every row re-confirmed within a week, exactly as designed. Only **2** rows are eligible for
parole at this instant, which is why the log reads "1 re-admitted on parole": the cohort is drained
continuously, not held.

### Parole already drains — the `0 cleared` reading is an undersampled rate

`cleared` counts rows a *successful scrape cleared in that run*. Cumulatively it is not zero. Of the
**23** Boards ADR-0162's 2026-09-16 probe found answering 200, five days later **22 are gone from
the ledger** (`parole_check.py`); only `ashby:todyl` is present, and it was cleared and then
re-quarantined — a flapper, not a failure to clear. 22 clears over ≈120 runs is ≈0.18/run, so a
10-run window reading 0 every time is expected. The set grows net because **new** Boards arrive
faster than recoveries leave, not because nothing leaves.

## 2. The live re-probe — n = 240, and it breaks the premise

Joining the ledger to `board_priority.csv` splits the quarantine into three strata that behave
completely differently. `updated_at` there is "the last snapshot that *included* the Board", i.e.
the last time it produced tech jobs:

* **recent-producer** (80) — priority row refreshed on/after 2026-09-15, so the Board served tech
  jobs days before it was struck out
* **stale-producer** (204) — has a priority row, older than that
* **never-produced** (598) — no priority row at all

`sample_quarantine.py` draws all 80 recent-producers, 60 random stale-producers and 100 random
never-produced (seed 20260921) → `artifacts/2026-09-21_sample-ids.txt`. Each was re-probed with the
**existing** `scripts/validate/recheck_boards.py`, which routes through `check_liveness.PROBES` —
the same probe functions the ledger-wide liveness sweep uses, so a verdict here means what a
verdict there means. 12 workers. Raw verdicts:
`artifacts/2026-09-21_reprobe-verdicts.csv`, transcript `artifacts/2026-09-21_reprobe.log`.

| stratum | population | n | live | dead | unknown | live % | open postings on the live ones |
|---|---|---|---|---|---|---|---|
| recent-producer | 80 | 80 | **60** | 20 | 0 | **75.0%** | 12,129 |
| stale-producer | 204 | 60 | 1 | 58 | 1 | 1.7% | 2 |
| never-produced | 598 | 99 | 2 | 89 | 8 | 2.0% | 901 |
| **total** | **882** | **239** | **63** | **167** | **9** | **26.4%** | **13,032** |

Stratum-weighted, that extrapolates to **≈75 of the 882 quarantined Boards being live right now
(9%)** — against the 3.0% ADR-0162 measured five days ago. One id of the 240 did not resolve to a
ledger row, hence n=239.

**This is a stratified sample of 239, not a census. It is evidence, not proof** — but the effect is
far too large to be sampling noise, and the mechanism behind it is identifiable.

### Almost all of it is one provider-wide outage

`live` by ATS: **zwayam 58 live / 0 dead**. Every other ATS combined: 5 live / 167 dead.

The zwayam cohort is 59 quarantined rows — essentially zwayam's entire presence in the ledger — and
every one of them:

* sits at **exactly 5 strikes** (a first-time quarantine, never paroled),
* was struck in one of three consecutive runs (55 at `2026-09-19T21:14:57`, 3 at `21:47:19`, 1 at
  `2026-09-20T00:50:16`),
* carries a priority row updated **2026-09-19** (58 of 59) — it produced tech jobs the same day it
  was struck out, **3,898** tech jobs across the cohort,
* and answers **live** today, 58 of 58 probed, the largest at 1,776 open postings
  (`careers.eaplworld.com`, `adani.openings.co` 1,607, `careers.microland.com` 1,127,
  `careers.persistent.com` 706, `career.crisil.com` 665, `epam.cluster3.openings.co` 588 …).

These are Persistent, Coforge, Cyient, Happiest Minds, ITC Infotech, Microland, EPAM, CRISIL, Adani,
Airbus, BMW TechWorks, Samsung. They did not close. **A provider outage on 2026-09-19 404'd zwayam
for long enough that every zwayam Board in the slice took five consecutive strikes and quarantined
together.** ADR-0058's premise — five consecutive 404s means *this Board* no longer exists — holds
for a Board failing alone and is falsified for a Board failing alongside its whole provider. Five
consecutive scrapes is five *runs*, ≈5 hours at the measured ≈60 min/run: short enough that one
afternoon's provider fault clears the bar.

Timestamp clustering alone does **not** prove an outage — `last_seen_gone` is one stamp per run, so
every Board struck in the same run shares it, and the largest non-zwayam clusters (greenhouse 51 +
ashby 31 + lever 18 at `2026-09-16T17:20:33`) are just ADR-0162's 652-Board first-parole cohort
being re-struck. What identifies zwayam is the conjunction: whole provider, all first-time, all
producing that same day, all live now.

### The five live Boards outside that cohort

| Board | postings | priority row |
|---|---|---|
| `phenom:careers.merckgroup.com` | 849 | none |
| `rippling:kindthread` | 52 | none |
| `greenhouse:capitalize` | 3 | 2026-09-15 / 1 tech |
| `ashby:hex` | 2 | 2026-09-17 / 13 tech |
| `ashby:sievo` | 2 | 2026-09-01 / 1 tech |

Both substantial ones are in the **never-produced** stratum — the one a drain would treat as
safest, precisely because the Board has contributed nothing. `careers.merckgroup.com` is live with
849 postings.

### 9 Boards cannot be verdicted at all

All 9 `unknown` are personio, and ADR-0162 hit the same wall (a Personio slug is a whole host while
the ledger carries bare tenants). Personio has **56** quarantined rows and the probe places none of
them. Any drain must refuse `unknown`, not read it as dead.

## 3. Pricing the two candidate drains

### (a) Promote a confirmed-gone Board to `dead` in `data/validate/liveness/<ats>.csv`

Refused on the measurement.

* On today's ledger it would delist the ≈75 live Boards above — **63 measured live in the sample
  alone, 13,032 open postings**, including 58 live zwayam Boards carrying 3,898 tech rows.
* `dead` removes the Board from `load_active_companies`, which removes it from
  `index_plan.live_keep_set`, so the next `index prune --apply` evicts **every one of its served
  rows as off-Board**. Proxying served rows by `board_priority.last_tech_jobs`: 6,049 tech rows
  across the 282 quarantined Boards that have a priority row, of which the all-live zwayam cohort
  alone is 3,898. ADR-0058 deliberately kept quarantine away from liveness for exactly this reason.
* It is the least reversible option available: the liveness ledger is committed to git and only the
  offline probes under `scripts/validate/` write it, so nothing in the pipeline can undo a wrong
  delisting. Parole cannot reach a Board that has left the candidate set.
* It converts a **bounded** ~1-week coverage gap that parole already heals (22 of 23, above) into a
  permanent delisting plus an index eviction.

### (b) Expire stale quarantine rows out of the failures ledger

Safe — liveness and the index are untouched — but it is not a drain.

Deleting a row returns the Board to the slice at 0 strikes. A genuinely-gone Board then 404s its way
back to 5 strikes and re-enters the ledger. The standing total dips and climbs back, and the cycle
costs **5 dead requests per Board** against parole's **1**, to learn the same thing parole already
learned. It changes the number the log prints, not the set of Boards scraped.

### What the ledger actually costs today

882 rows × one parole probe per 7 days ≈ **126 dead requests/day**, growing at the measured
+125 rows / 5 days ≈ +25/day. Unbounded, but slow: the liability is real and worth a terminal
drain eventually — it is not urgent enough to justify an irreversible delisting built on evidence
that is 26% wrong.

## 4. Verdict

**No drain ships.** The prerequisite is not machinery — `recheck_boards.py` (measure) and
`relocate_dead_boards.py --apply` (write `dead` into liveness, after checking whether the employer
merely moved ATS) already exist and already do exactly this, end to end. The missing piece is
**evidence good enough to act on irreversibly**, and three things have to land first:

1. **A correlated-gone guard in `update_ledgers failures`**, so an ATS-wide fault never becomes a
   per-Board verdict. **Not calibratable from this data: n = 1 outage.** Fitting a threshold to
   zwayam would be the one-sample generalisation this repo has been bitten by before; it needs a
   population of outages, which means instrumenting the correlation and waiting.
2. **Delisting keyed on the liveness probe's own DEAD verdict**, never on the scrape's recorded
   reason, and refusing `unknown` (9 of 239 here; 56 personio rows unverdictable).
3. **Repeated agreement over time** — two DEAD verdicts a week apart, not one. 20 of the 80
   recent-producers probed dead, so even "produced last week" is not a reliable liveness signal in
   either direction.

Until then ADR-0162's parole is the right mechanism: it is cheap, it is self-healing, and it will
clear the zwayam cohort on its own within days.

## Files

```
sample_quarantine.py                              stratified sampler (state dir -> ids.txt)
artifacts/2026-09-21_sample-ids.txt               the 240 sampled Board ids
artifacts/2026-09-21_reprobe-verdicts.csv         ats,slug,url,verdict,jobs
artifacts/2026-09-21_reprobe.log                  full streaming transcript
```

Re-running the probe: `python scripts/validate/recheck_boards.py <ids.txt> --workers 12 --out <csv>`.
