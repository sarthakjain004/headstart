# Ten-run pipeline log review — 2026-09-21

**Window:** the 10 most recent `pipeline.yml` runs, `35569584172` → `35595828212`
(2026-09-21 06:41–12:21 UTC). All ten share one head SHA, **`96c0c702`** (#513), so nothing here is
confounded by code shipped mid-window. One of the ten (`35578784666`) was **stood down** by the
compaction gate and has no stages to measure, so every per-stage figure below is over **9 runs**.

Analysed from a worktree pinned at `96c0c702` (the runlog analysers parse with the *local* registry;
a stale branch silently drops ids and blames the pipeline). Tooling: `scripts/runlog/`'s eight
analysers plus `scope_exclusion_persistence.py`.

## Wall clock

| run | wall | owner | scrape max | join | merge |
| --- | ---: | --- | ---: | ---: | ---: |
| 35569584172 | 37.5m | scrape 11.8m | 11.8m | 10.6m | 10.2m |
| 35572417059 | 39.9m | merge 12.7m | 11.1m | 10.9m | 12.7m |
| 35575571888 | 37.1m | scrape 12.4m | 12.4m | 11.3m | 8.5m |
| 35579899915 | 44.0m | merge 16.2m | 12.6m | 10.9m | 16.2m |
| 35583945947 | 37.5m | merge 11.5m | 10.6m | 10.2m | 11.5m |
| 35587348049 | 31.9m | scrape 11.3m | 11.3m | 9.5m | 6.8m |
| 35590192500 | 30.2m | scrape 10.8m | 10.8m | 8.4m | 6.6m |
| 35592873553 | 33.1m | scrape 11.4m | 11.4m | 10.4m | 7.2m |
| 35595828212 | 35.4m | scrape 11.4m | 11.4m | 11.0m | 8.5m |

Queue/setup is 0.2–0.3m in every run — the runs are not waiting on infrastructure. `scrape` and
`merge` alternate ownership; no single stage dominates. `embed` never exceeds 4.1m because the
matrix is only 2–3 shards for 202–340 new Docs.

## Findings

### 1. The HF private-storage quota is already being hit (CRITICAL)

`usedStorage` across the window: **75.73 → 99.12 GB**, +23.37 GB over 9 runs (**+2.60 GB/run**),
while `live` stayed flat at 7.21–7.97 GB. Re-measured directly against the Hub after the window:
**99.12 GB used, 7.27 GB live, 1 commit.**

The reclaim step reported `squashed this run` on **every one** of the 9 runs and freed nothing. Its
threshold is `SQUASH_ABOVE_GB: "40"`, and `usedStorage` never fell below 40 GB even immediately
after a squash, so the threshold was permanently tripped and the step ran unconditionally while
being a no-op — `super_squash_history` only makes blobs *eligible* for HF's asynchronous
collection, and the step never re-read `usedStorage` to notice.

This is not a projection. Three `cleanup-index` runs (2026-09-18, 09-19, 09-20) died at
`Upload the cleaned index` with:

```
403 Forbidden: Private repository storage limit reached, please upgrade your plan
```

**PR #515 (`b49e4ac8`, "Delete the orphaned blobs instead of waiting for HF to collect them")
fixes exactly this and merged at 12:35 UTC today — after every run in this window, and after the
in-progress run `35599079608` (also `96c0c702`) had already started.** So the fix is merged and
**unexercised**: the first run at `b49e4ac8` is the verification point, and `usedStorage` is the
signal to read.

### 2. Cadence is ~2x what the storage math assumes (HIGH)

Start-to-start deltas across the window: 37.6, 39.9, 37.2, (12.8 stand-down), 43.9, 37.6, 32.0,
30.2, 33.2, 35.4 min — mean ~34 min. Over a wider window (99 completed runs, the 2.57 days to
2026-09-21) that is an **observed 38.6 runs/day**, with the 34.6-min median gap implying a 41.6/day
ceiling; 38.6 is the figure storage inflow follows. The reclaim threshold was sized against
"~19.4 runs/day" and `schedule`'s note cites a 74.1-min mean run duration. Runs are now roughly
half that, so the `chain` job produces about twice the runs, and therefore about twice the blob
inflow, that the 40 GB threshold was derived from. Re-derive the threshold against the measured
cadence rather than the historical one, even after #515.

### 3. cleanup-index is failing, so compaction is not happening (HIGH)

3 of the last 6 `cleanup-index` runs failed — all three at the same step, all three on the storage
403 above. Compaction is the one job here that cannot merely be late: its starvation is what
rejected every upload on 2026-08-27, and `_deletions/` growth is quadratic in runs against HF's
10,000-file per-directory limit. Same root cause as finding 1, different blast radius, so verify it
separately after #515 lands.

One unrelated observation from the same logs: the `window` job spent **35 minutes** polling
`pipeline in flight (1); re-checking in 60s` before finding a gap. At a 34-min cadence with 30–44
min runs, the window is genuinely narrow.

### 4. Oracle's `TotalJobsCount` over-states servable rows, so complete scrapes are marked truncated (HIGH)

Eight Oracle Boards are scope-excluded on **9 of 9** runs with messages of the form
`read 27 of 600 requisitions — the rest is unread, not absent`. I reproduced the shortfall live and
then swept each Board's full offset range past the empty page the walk stops on:

| pod | stated `TotalJobsCount` | walk read | full sweep found | over-count |
| --- | ---: | ---: | ---: | ---: |
| `ialmme-test.fa.ocs` | 600 | 27 | **27** | 22.2x |
| `eofe-dev3.fa.us2` | 806 | 49 | **49** | 16.4x |
| `edca-test.fa.us2` | 373 | 43 | **43** | 8.7x |
| `jpmc-dev3.fa` | 6,786 | 1,608 | **1,608** | 4.2x |
| `hdpc-dev6.fa.us2` | 1,477 | 407 | **407** | 3.6x |
| `eofe-dev11.fa.us2` | 1,623 | 605 | **605** | 2.7x |
| `hcpd-test.fa.ca2` | 636 | 307 | **307** | 2.1x |

**7 of 7: the walk loses nothing.** In every case the walk terminated on an empty page at an offset
*past* the last data page and *below* `_OFFSET_CEILING`, having collected every reachable row. The
scrape is complete; the verdict is wrong, because it is compared against a counter that does not
count servable requisitions. `TotalJobsCount` lies in the same direction `hasMore` already does
(CLAUDE.md's Oracle entry).

The consequence is the one ADR-0053 has no drain for: these Boards never re-enter the eviction
scope, so their closed postings are served indefinitely, and the run's own scope-exclusion total is
inflated by Boards that were read in full.

A hypothesis I held and killed by measurement: I first read `offset=50 → 0 rows` beside
`offset=200 → 5 rows` as a pagination bug abandoning real requisitions. It is not — offset 50 is
not on the scraper's 200-step grid, and the sweep on that grid matches the walk exactly. The fix is
to the *verdict*, not the walk: an empty page below the offset ceiling means the walk reached the
true end. Any change must keep the genuine `_OFFSET_CEILING` cases (`egud`, `eluq`, `eluq-dev19`,
`jpmc-test`) excluded, because those really are unreachable past row 10,000.

### 5. `successfactors:careers.hcltech.com` is 65% of all withheld eviction candidates (MEDIUM-HIGH)

ADR-0053 withheld 3,339–3,882 eviction-candidate rows per run across 35–42 Boards (mean ~3,620).
`careers.hcltech.com` alone accounts for **~2,364 of them in every run** — about 65% — on
`872/3876 job pages unreadable`. It is also the shard floor board in run `35579899915`
(753s, 95% of its shard, `actual/predicted` 1.84). One Board, one failure mode, the majority of the
withheld set.

### 6. Two boards inject ~8,600 near-duplicate rows and hold two top-5 priority slots (MEDIUM)

Probed live:

- **`recruitee:rebootmonkey`** — 4,377 postings, 2,562 distinct titles across **2,352 distinct
  locations**: essentially one role ("Data Center Technician") replicated per city worldwide.
  Priority rank **#3**, ~4,334 rows in the served table.
- **`smartrecruiters:EndeavorITSolution`** — 8,478 postings, **8,454 of them in Indore, MP**,
  dominated by repeats (`Internship / Training for PHP` x49, `Fresher Android Developer Training
  Program` x43, `Android Developer` x39). Priority rank **#5**, ~4,252 rows — 8.4% of all
  smartrecruiters tech jobs from a single board.

`index prune`'s duplicate check cannot reach either: the postings carry distinct ids, so they are
duplicates semantically, not by identity. Together they are ~1.7% of the 507,7xx-row served table,
and they consume two of the five slots the priority ledger ranks highest.

> **Correction and addition, 2026-09-21 — the 8.4% is wrong (6.5%), "distinct titles across
> distinct locations" is the wrong instrument, and the two boards are *not* the same shape.**
> Both boards re-measured live through their registered scrapers before being parked
> ([PR #520](https://github.com/sarthakjain004/headstart/pull/520); captures and method in
> `experiment/near-duplicate-spam-boards/`). The posting/title/location counts above all reproduce
> exactly. Four things change.
>
> **8.4% → 6.5%, and the denominator is now named.** Against `data/state/board_priority.csv` pulled
> fresh the same day, `smartrecruiters:EndeavorITSolution` is **4,252 of the 65,453 tech rows the
> ledger credits to smartrecruiters across its 3,605 scored boards = 6.5%**. (A pull an hour earlier
> read 65,444 / 3,606 — the ledger moves every run; the ratio is 6.50% on both.)
>
> **The more striking share was missing: `recruitee:rebootmonkey` is 4,334 of 10,684 recruitee tech
> rows across 1,304 scored boards = 40.6%** — two fifths of everything that ATS contributes, from one
> board.
>
> **"2,562 distinct titles across 2,352 distinct locations" is not self-evidently damning — it reads
> the opposite way.** On this board **no exact title repeats more than 4 times**, so a distinct-title
> ratio scores it 58.5% unique and therefore *ordinary*. The duplication only becomes visible after
> stripping each title's per-city tail: the board collapses to **70 title stems, one of which — "data
> center technician" — holds 4,249 of the 4,377 postings (97.1%)**, runner-up 36. Any future claim of
> this kind needs the stem, not the raw title count.
>
> **The two boards are the same defect from opposite ends, and only one of them is detectable
> cheaply.** Reboot Monkey is one role across 2,352 cities; Endeavor is one city (99.7% Indore, 5
> locations in total) repeating a handful of roles. A bounded prevalence sweep over the top 30
> priority boards (23 measurable) separates them completely: Reboot Monkey's 97.1% top-stem share is
> **5.3x the 18.3% maximum among the other 23**, while **Endeavor ranks 19th of 25 on that same
> measure (2.9%)**. Endeavor's own markers — one city, 49 copies of one exact title — are *beaten* by
> `successfactors:careers.hcltech.com` (2,975 postings across **4** locations, **399** copies of one
> exact title) and `careers.wipro.com` (404) — both floors, since those two boards could only be
> measured on their tech subset — and both are real employers doing genuine bulk requisition hiring
> this index wants. **So every cheap ratio strong enough to catch Endeavor evicts HCLTech and
> Wipro first**, which is the load-bearing reason PR #520 parks two measured boards rather than adding
> a near-duplicate gate to the index path. Caveat on that sweep: 23 boards drawn from the head of the
> priority ledger say nothing about the tail.
>
> The severity ranking above is unchanged, as is everything else in this finding.

### 7. 23.3% of the served table is non-tech (MEDIUM)

`role_trends` reports `non-tech: 118,263–118,338 of 507,673–507,886 served rows (23.3%)` — the same
figure to one decimal in all 9 runs. ADR-0017's gate is recall-biased by design, so this is the
documented cost rather than a defect, but it means roughly one row in four of a tech-jobs index is
not a tech job. That is a tuning decision worth making deliberately (issue #186's territory), not a
number to keep quoting as acceptable by default.

### 8. The quarantine ledger never drains (MEDIUM)

Every run: `N ledger rows (0 cleared) | 880–882 at/over 5 strikes … this run moved it +1 / -0`.
So **~98% of the ~898-row quarantine ledger is at the terminal strike count**, nothing is ever
cleared, and `scrape_plan` re-admits exactly **1** Board on parole per run. The sampled entries are
flat `HTTP Error 404` at 6 strikes — a definitive gone-response, not a flaky one. A 404 at 6 strikes
belongs in the liveness ledger as `dead`, not in a quarantine set that is re-paroled forever.
(The sample the emitter prints is alphabetical and capped at 20, so it does not establish which ATS
dominates — that needs the state file, not the log.)

> **Correction, 2026-09-21 — the ledger does drain, it is not ~98% confirmed-gone, and the
> promote-to-`dead` recommendation is withdrawn
> ([ADR-0170](../adr/0170-a-provider-outage-is-not-a-gone-verdict.md)).**
> Three claims above do not survive measurement against the state file and a live re-probe.
>
> **`0 cleared` is an undersampled rate, not a broken mechanism.** That field counts rows a
> successful scrape cleared *in that run*. Of the 23 Boards ADR-0162's 2026-09-16 probe found
> answering 200, **22 are gone from the ledger five days later** — ≈0.18 clears/run over ≈120 runs,
> so a 10-run window reading 0 every time is the expected observation, not evidence that nothing
> clears. Relatedly, "re-admits exactly 1 Board per run" is not a stuck mechanism either: parole
> re-confirms every row weekly, so `now − last_seen_gone` is p50 4.6 d / max 7.5 d with 880 of 882
> rows under a week, and only ~2 rows are parole-eligible at any instant.
>
> **The set is ~9% live, not ~98% confirmed-gone.** "At the terminal strike count" is a fact about
> the counter, not about the Boards. A stratified re-probe through the liveness sweep's own
> `check_liveness.PROBES` (n=239 of 882; all 80 Boards whose priority row was refreshed in the last
> week, 60 of 204 older producers, 100 of 598 that never produced) returned **63 live / 167 dead /
> 9 unverdictable**, which stratum-weights to **≈75 of 882 live right now**. Live share is 75.0% in
> the recent-producer stratum against 1.7–2.0% in the other two, and **58 of the 63 are zwayam**: all
> 59 zwayam rows sit at exactly 5 strikes, 55 share `2026-09-19T21:14:57`, all produced tech jobs
> that same day, and 58 of 58 answer live today. That is one provider outage quarantining a whole
> ATS at once, not 59 dead Boards — five consecutive scrapes is five *runs*, ≈5 hours at this
> review's own measured cadence.
>
> **So "a 404 at 6 strikes belongs in the liveness ledger as `dead`" is withdrawn.** On today's
> ledger it would delist those ≈75 live Boards; and because `dead` drops a Board from
> `load_active_companies` it also drops from `index_plan.live_keep_set`, so the next
> `index prune --apply` would evict every one of their served rows as off-Board. Liveness is
> committed to git and only the offline probes write it, so the pipeline could not undo it.
>
> **What stands:** the set does grow net — arrivals outpace recoveries — and the standing cost is
> real: 882 rows × one parole probe per 7 days ≈ **126 dead requests/day**, growing ≈ +25/day,
> unbounded. A terminal drain is still wanted. What it needs first is a correlated-gone guard so a
> provider outage never becomes a per-Board verdict, and that threshold cannot be fitted to the
> single outage in this data. The alphabetical-cap caveat above was right, and reading the state
> file is what found greenhouse (326) rather than ashby (173) dominating.

### 9. The re-derive queue is climbing (LOW-MEDIUM — watch)

`update_meta`'s `queued to re-derive` over the window: 286, 308, 178, 169, 278, 391, 462, 633, 628.
The last five runs rise monotonically until the final flat reading. Every run is a **no-sweep** run
(`derivations v13 stored, v13 in code`), so this is ADR-0062's re-derive queue, not a version bump.
Not yet a defect; worth a second window before treating it as one.

### 10. `filter_tech`'s successfactors kept% is not a tech-share measurement (LOW)

`successfactors 37,502 / 37,652 = 99.6%` in all 9 runs, against 11.4–71.2% for every other ATS.
Cause: SuccessFactors builds every field from the job page, and `tech_detail_wanted` gates the
detail pass, so a non-tech posting never becomes a Job and never reaches the corpus. It is the only
gated ATS where this happens — the other ten emit the posting from listing fields regardless, which
is why their kept% stayed normal. The scraper's own comment already records this for run
`35193130454`; making the gate conditional on `have_details` restored whole Boards for *direct*
callers but left the pipeline's own funnel denominator post-gate.

**The gate itself is recall-safe — I verified rather than assumed.** Across
`jobs.pirelli.com`, `jobs.webasto.com`, `career.nexans.com` and `jobs.crh.com` (2,273 postings),
the slug-derived title the gate sees agreed with the real title+department verdict on **2,273 of
2,273**, dropping **0** genuinely-tech postings, and `department` was populated on **0** of them, so
`tech_filter`'s rule 4 cannot fire for this ATS at all. Every other gated scraper
(apple, eightfold, gem, jazzhr, phenom, rippling, smartrecruiters, trakstar, workday, zwayam) passes
a `department_of` accessor. This is purely an observability defect: report the gated count so the
real tech share stays readable.

### 11. `fanout_retries.py`'s egress detector false-positives (LOW — analyser, not pipeline)

`DIRECT !MISMATCH` fired on **6 of 9** runs, 1–3 shards each, while `0/15` shards logged
`degrading to direct` in **every** run. In 4 of those 6 the analyser's own excess figure is
**negative** (−71, −89, −78, −74) — the flagged shard spent *fewer* rate-limit retries than a
healthy one, which is the opposite of the degraded signature it is detecting.

Mechanism: `DIRECT_RATIO` was calibrated on shards with 5,000–19,000 `network` retries. This
window's shards run 33–1,200, with whole-run totals (11,903–15,825) comparable to what a *single*
shard used to spend. A near-zero denominator inflates `429/network` without any degradation —
`network=0` prints `inf`. Add an absolute-volume guard before the ratio is trusted.

### 12. Apple is the recurring scrape floor board, but the win is ~1 minute (LOW)

`apple:jobs.apple.com` is the slowest single board in 6 of 9 runs at 90–95% of its shard's wall
(638–744s), and the cost ledger's `apple` median is **600.6s** against 66.0s for the next ATS.
`c5984f38` already parked `oracle:ejwl…` as a recurring floor board, so parking is established
practice — but the marginal gain here is small and should be stated as such: several boards cluster
near 600s (`oracle:hcbt` at 535–651s, `successfactors:careers.wipro.com`, `jazzhr:bright…`), so in
`35595828212` removing apple moves the scrape max from 685s to ~617s — about **1.1 min of a 35.4
min wall**. The scrape stage's ~11-min ceiling is set by the cluster, not by apple alone.

## Checked and found healthy — do not re-investigate

- **`reassignments: 0 of 389,4xx (0.00%)` on all 9 runs is correct, not a dead counter.** Family
  assignment is deterministic nearest-centroid over a Job's stored vector, so it can only move when
  the Job is re-embedded (0–6 per run) or the centroids change. The denominator 389,4xx is exactly
  `served − non-tech`.
- **The ADR-0083 grace period is working.** `carried in` vs `reappeared` looks alarming read raw
  (17–84 of 944–1,253), but most carried ids sit on Boards this run never scraped, so they keep
  their state by design. Of the ids that actually got a second look, **6–31% reappeared**
  (`reappeared / (reappeared + evict)`), and the queue is stable at 945–1,253 rather than growing.
- **Oracle pagination loses nothing** — 7/7 pods, walk == full sweep (finding 4).
- **The stood-down run and its skipped `chain` are correct.** `cleanup-index` `35578655484` ran
  08:35–08:49 and its handback dispatched `35579899915` at 08:49:17 as `github-actions[bot]`.
- **`index prune` evicting 0 (0 off-Board + 0 duplicate) on all 9 runs is expected** — the keep-set
  is a static 120,543 Scrapable Boards across the window.
- **Board error rate is healthy**: 39–57 errors per run, **0.2–0.3% of 20,000 attempted**, no shard
  hit its time budget in any run. Workday dominates (21–33 `HTTPError`, 5–12
  `UnexpectedListingResponse`) proportional to its share of the slice.
- **embed is clean**: 0 failed docs in every run, all shards on `cpu` (so rates are comparable),
  `actual/predicted` 0.65–0.96, and `embed_merge` received every planned fragment (3/3, 2/2).
- **Derivations are churning, not decaying.** Over the window experience is +328 gained / −314 lost
  and salary +340 / −339 — balanced. The individual-run spikes (126 experience `lost` in
  `35590192500`, 135 `gained` in `35592873553`) net out.
- **Cost coverage is 100%** (`20000/20000 boards measured`) in every run, so no straggler is hiding
  behind an ATS-median estimate.

## Method notes

Live probes in findings 4, 6 and 10 hit the real endpoints from this session, per CLAUDE.md's rule
that a claim about how an endpoint behaves must be measured rather than reasoned about. The
SuccessFactors gate measurement required constructing scrapers directly, which sets
`have_details = None` and turns the gate **off** — that is what makes an ATS's ungated tech share
readable at all, and it is why the 10–16% keep rates there are the honest denominators.
