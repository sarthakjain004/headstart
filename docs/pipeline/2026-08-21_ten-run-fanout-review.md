# Ten-run fan-out review — 2026-08-21

Successor to `2026-08-18_ten-run-log-review.md`. Runs reviewed (all `nightly-pipeline`,
chronological, 2026-08-21 03:29Z → 11:53Z), all at head `a30bbd8` — **no code changed across this
window**, so nothing here is a before/after code comparison; it is ten back-to-back samples of the
same pipeline.

| Run | Started (UTC) | Wall | Scrape max | Stage-owning board | Floor% |
|---|---|---|---|---|---|
| 32443588900 | 08-21 03:29 | 66.7m | 26.8m | smartrecruiters:AdeebaEServicesPvtLtd | 93% |
| 32446509145 | 08-21 04:19 | 62.1m | 30.8m | smartrecruiters:crossmark1 | 64% |
| 32449242159 | 08-21 05:05 | 54.9m | 24.0m | smartrecruiters:AdeebaEServicesPvtLtd | 91% |
| 32452572522 | 08-21 05:59 | 49.7m | 24.1m | smartrecruiters:AdeebaEServicesPvtLtd | 92% |
| 32458484544 | 08-21 07:24 | 48.7m | 23.9m | smartrecruiters:AdeebaEServicesPvtLtd | 93% |
| 32461622760 | 08-21 08:06 | 55.9m | 26.4m | smartrecruiters:AdeebaEServicesPvtLtd | 90% |
| 32466424417 | 08-21 09:08 | 56.6m | 24.9m | smartrecruiters:AdeebaEServicesPvtLtd | 89% |
| 32470666934 | 08-21 10:01 | 60.2m | 27.6m | smartrecruiters:AdeebaEServicesPvtLtd | 89% |
| 32475027789 | 08-21 10:58 | 49.7m | 23.4m | smartrecruiters:AdeebaEServicesPvtLtd | 93% |
| 32479274151 | 08-21 11:53 | 58.0m | 26.8m | smartrecruiters:AdeebaEServicesPvtLtd | 92% |

Mean wall clock 56.2m, range 48.7–66.7m (17% swing), no monotonic trend — noisy around a flat
mean, not regressing or improving run over run. In every run the critical path (Σ of stage maxima)
lands within 0.1–0.2 min of the wall clock — queueing/runner setup costs nothing; 100% of the
variance is real stage work.

## 1. `scrape` owns the run, every run, at a stable 40–50% share

Stage max across the ten runs: 26.8, 30.8, 24.0, 24.1, 23.9, 26.4, 24.9, 27.6, 23.4, 26.8 minutes —
44–50% of wall clock in every single run. `join`/`embed`/`merge` are single jobs (no fan-out, so
nothing to attribute), together costing 27–42m of Σ; their own spread (embed 4.9–11.9m, merge
5.8–17.7m) likely tracks the corpus delta each run writes but wasn't this review's focus.

## 2. The floor: two specific boards set the scrape ceiling in 10/10 runs

This is the headline finding. `smartrecruiters:AdeebaEServicesPvtLtd` is the scrape stage's
critical-path owner in **9 of 10 runs**, floor-bound at 86–93% — the tool's own diagnostic fires
every time: *"X% one board — floor-bound. A better packer cannot help it."* `successfactors:
careers.hcltech.com` is the #2 (occasionally #1) straggler in **10 of 10 runs**, floor-bound at
86–93%.

Sampled the raw shard logs directly rather than trusting the summary — `AdeebaEServicesPvtLtd`
reads **exactly 23,806 jobs, byte-identical across every run checked** (4 runs sampled: 1489s,
1310s, 1330s, 1306s wall — 21.8–24.8 min, stable, not growing):

```text
smartrecruiters:AdeebaEServicesPvtLtd: 23806 jobs in 1486s   (run 32479274151)
smartrecruiters:AdeebaEServicesPvtLtd: 23806 jobs in 1489s   (run 32443588900)
smartrecruiters:AdeebaEServicesPvtLtd: 23806 jobs in 1310s   (run 32449242159)
smartrecruiters:AdeebaEServicesPvtLtd: 23806 jobs in 1330s   (run 32466424417)
smartrecruiters:AdeebaEServicesPvtLtd: 23806 jobs in 1306s   (run 32475027789)
```

`careers.hcltech.com` reads 9,447–11,127 job-detail pages per run (its listing is a
`/sitemap.xml` urlset requiring one detail fetch per job), costing 1079–1438s (18.0–24.0 min).

## 3. Root cause, traced to source rather than inferred from timing alone

`src/headstart/scrapers/smartrecruiters.py:11–15` (introduced in PR #226, `bc0ce53`,
2026-08-20 19:00 — merged before every run in this window):

> shipping uncapped on purpose, to measure real cost/impact across a few pipeline runs before
> deciding a cap from data rather than from #202's projection a second time (#227)

`_MAX_PAGES = 50` (5,000 postings) is defined but its enforcement is commented out. **These ten
runs are that measurement window.** `AdeebaEServicesPvtLtd`'s 23,806 postings (238 pages at
`limit=100`) is read in full every run because nothing bounds it. `hcltech` is a separate,
pre-existing SuccessFactors cost (one HTTP fetch per job off a sitemap urlset, not a pagination
cap) — the same class of cost `careers.ey.com` was already parked for in PR #217 (see CLAUDE.md's
ATS-provider TODO).

**Projected saving if `_MAX_PAGES` were re-enabled** (labeled a projection, not a measurement —
assumes roughly linear cost in postings read, which pagination + concurrent detail-fetch may not
be exactly): capping Adeeba to 5,000 postings would cut its cost from ~1,480s to roughly
1480×(5000/23806) ≈ 310s, pushing `hcltech` (18–24 min) into the ceiling role instead. Net expected
wall-clock saving: computed per run as (Adeeba's actual wall − hcltech's wall that run) in the 9
runs where Adeeba was the owner — 4.8, 0.7, 4.4, 5.7, 7.3, 1.4, 4.9, 0.4, 7.1 minutes; mean ≈4.4
min/run (run 32446509145 excluded, see §4). A full fix (bounding both boards toward the ~800–900s
shard median) would plausibly save 10–13 min/run, but that second half is a rougher extrapolation.

## 4. One anomaly, single occurrence — flagged, not concluded

In run `32446509145` the stage owner was a *third* SmartRecruiters board, `crossmark1`, at 1850s
(30.8m) — actual/predicted ratio 2.34, well above Adeeba's and hcltech's near-1.0 ratios that same
run. Same uncapped-pagination class, different board, and it only appeared once in ten runs. One
occurrence is an anecdote per the skill's own rule — worth re-checking if it recurs, not
actionable alone.

## 5. Everything else checked and ruled out for this window

- **Egress/WARP**: 0/150 shard-runs (10 runs × 15 shards) showed DIRECT degradation, 0 logged
  `degrading to direct` — completely clean across the whole window.
- **Board errors**: 38–61 per run against 20,000 boards (0.19–0.31%), no shard ever hit its time
  budget — stable and low, not a contributor to the wall-clock swing.
- **Quarantine stock**: climbed 343 → 360 over the window (+17, ≈+1.7/run) — a standing ledger
  total (not this-run inflow), too gradual to explain any run-to-run swing, but worth a glance in
  a future review if the drift continues.
- **Corpus volume**: 1.49–1.53M scraped, 295–297K tech-kept (19.3–19.9%) — rock-stable across all
  ten runs, no ATS collapse, no volume anomaly, SmartRecruiters total held ~231–255K despite the
  uncapped pagination (only the single largest boards are actually oversized).
- **Description store**: description-store ids +3,074 and skip-list +2,487 over the window — smooth
  growth, no backlog buildup.

## 6a. Egress rotation and the IPs behind it — measured, and it disagrees with ADR-0067

Parsed the `spare_egress` lines directly out of the same cached shard logs (150 shard-runs, all
10 runs) rather than relying on the retries table's rotation *count* alone — every
`now egressing from <ip> via <colo> (first|moved)` line, matched against the `walled the current
IP` line that triggered each rotation.

**The IP pool is much larger than documented, and near-every rotation lands a genuinely new
address.** Per-shard churn (`unique IPs seen ÷ rotation events`) is 0.93–1.00 in effectively every
one of the 150 shard-runs — most shards show zero repeats across 40–205 rotations. Across the
whole window: **12,702 total rotation events, 11,007 distinct IPv6 addresses**, and the single
most-repeated address landed only **7 times out of 150 shard-runs**. ADR-0067 documented
"rotation yields a genuinely different IP only ~11 times in 30, because the colo never moves and
carries a 1-3 address pool" — this window's measurement (12,702 rotations vs. that ADR's 30)
directly contradicts the pool-size half of that claim. The colo-stability half still holds: every
shard's `colos` set is a single value (SJC, IAD, LAX, SEA, ORD, MSP, DFW — the colo genuinely
doesn't move within one shard's run), so what changed is specifically the address pool depth
*within* a colo, not the colo-pinning behavior. Worth updating ADR-0067, or re-running its
original 30-rotation measurement, since the two numbers can't both be current.

Colo distribution across all 150 shard-runs: IAD 3,990, MSP 1,583, SJC 2,085, LAX 1,550,
ORD 1,504, SEA 1,148, DFW 637, **SCL 205 (one shard, one run — `32449242159` shard 12)**. That one
Santiago-routed shard is also the single most anomalous rotation record in the whole dataset: 205
rotation events (vs. a typical shard's 40–150), the lowest per-shard churn (0.90, i.e. the most
repeat IPs of any shard sampled), and the highest network-retry count of any shard across all ten
runs (15,212, vs. a typical 4,000–10,000). One occurrence — flagged, not concluded on, per the
skill's own anecdote rule; worth a look only if a future run routes through SCL again.

**What's actually forcing the rotations: Workday, overwhelmingly.** Of 12,573 `walled the current
IP` events with an attributable board, **12,223 (97.2%) are Workday boards**, 350 (2.8%) Eightfold,
0 from any other ATS. Workday is roughly 40% of scraped volume in this window (§ corpus data,
prior report) but generates 97% of the wall-triggering rate-limit pressure — disproportionate to
its share, and spread across many distinct tenants rather than one or two bad boards:

```text
workday:trinityhealth/jobs                    496
workday:aah/External                          349
workday:adventhealth/AH_External_Career_Site  285
workday:texasroadhouse/externaljobboard       252
workday:ghr/us-emplsv                         195
workday:citi/2                                194
workday:dickssportinggoods/DSG                188
workday:kohls/kohlscareers                    186
workday:micron/External                       149
workday:ngc/Northrop_Grumman_External_Site    148
```

This is consistent with, and gives hard numbers to, the known shape of Workday's per-tenant rate
limiting — the spare-egress mechanism is doing exactly the job it was built for, rotating through
what turns out to be a deep, healthy address pool, and no shard degraded to DIRECT egress in this
window (§5).

## 6. On correlating against #231 / #226 / #230 / #222

All ten runs share head `a30bbd8` — every one of those PRs is already merged into all ten, so
there is no before/after split available from *this window's* log data to isolate any single PR's
effect. #226's effect is nonetheless directly attributable via source, not timing inference — it
is the named, quoted cause of §2/§3 above. #231 (fan-out width by measured egress state) and #230
can't be isolated the same way here: all ten runs used a uniform 15 scrape shards, so this window
carries no shard-count variation to compare against. Isolating either would need the specific
last-run-before / first-run-after pair around each merge, which sits outside this ten-run window —
worth a separate targeted comparison if wanted.
