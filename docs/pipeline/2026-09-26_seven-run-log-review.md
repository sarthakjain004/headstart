# Seven-run log review — runs 36200233818 … 36218633315 (2026-09-26)

Seven consecutive `pipeline.yml` runs, 2026-09-25 23:14 to 2026-09-26 05:35 UTC, all on `21e78144`,
all `success`, all chained dispatches. The two scheduled runs in the window (`36200786616`,
`36209522164`) had zero jobs: the chain's dispatch took the group's single pending slot and displaced
them, by design. The next run, `36221241950`, is used only as a control where noted.

| run | wall | scrape max | join | embed (shards) | merge | Board errors | table rows |
|---|---|---|---|---|---|---|---|
| 36200233818 | 51.9m | 23.1m | 15.4m | 3.6m (4) | 8.6m | 1,555 | 512,468 |
| 36203593207 | 59.9m | **29.0m** | 14.8m | 3.9m (3) | 10.9m | 1,059 | 512,472 |
| 36207289951 | 53.6m | 22.6m | 13.0m | 3.2m (3) | 13.7m | 1,059 | 512,399 |
| 36210287475 | 51.7m | 23.2m | 14.8m | 2.9m (2) | 9.4m | 1,879 | 512,317 |
| 36213020963 | 54.7m | 25.5m | 14.7m | 3.0m (1) | 10.5m | 1,628 | 512,246 |
| 36215851608 | 55.7m | 24.2m | 15.1m | 2.9m (1) | 12.2m | 1,118 | 512,279 |
| 36218633315 | 52.7m | 23.2m | 15.7m | 2.7m (1) | 10.0m | 1,569 | 512,310 |

Every run scraped all 80,000 Slice Boards: nothing undone, deferred or budget-killed.

**Method.** All 148 job logs, with GitHub's echoed workflow source filtered out, were clustered into
6,390 line templates, and every template was reviewed. The join, plan, embed and chain logs were
also read end to end. The eight `scripts/runlog/` analysers ran from a worktree at the runs' SHA.
All 105 per-shard `ShardReport` JSONs and `board_cost.csv` files were pulled out of the fragment
artifacts by HTTP range reads, so no 85 MB artifact was downloaded whole. The 218 check-run
annotations were fetched. Claims about endpoints were measured live, with sample sizes given below.

Severity: **BUG** (wrong data or wrong decision), **DEGRADATION** (lost coverage or quality),
**WASTE** (time or storage for nothing), **LOG** (misleading or missing output).

## Bugs

### 1. SuccessFactors reads a throttled Board as a clean empty one (BUG, 1/7 runs, latent 7/7)
- **What happened.** On `36218633315` shard 13, 19 Boards logged
  `nothing via sitemap HTTP 429, search HTTP 429 at startrow 0 … -> 0 job pages`, then
  `0 jobs … (scraped clean, no postings)`. They landed in `boards_ok`, so they were inside eviction
  scope, and about 260 live ids went Unconfirmed. That produced the grace-period spike from 199 to 983.
- **Why the rows survived.** One more throttled scrape of those Boards would have evicted every row.
  The next run got lucky. Of the 187 ids checked live on entergy, lubrizol, timken and cmc, 180 were
  still listed.
- **Code.** `fetch_raw` calls `mark_truncated` only `if listed`. The NB comment in the same function
  already names the fix.
- **Same path.** A 403/404-every-run Board (witron, hagergroup) never earns a gone-verdict.
  `jobs.sap.com` now serves a sitemapindex and a 403, so it reads 0 jobs every run where it read
  322 tech jobs on 2026-09-23.

### 2. Oracle's pager scope-excludes `oracle:hcbt` every run (BUG, 7/7)
- **What happens.** `self._offset += _PAGE_SIZE` runs before the empty-page break. A Board stating
  9,621 ends its walk on the empty page at offset 9,800 with `_offset == 10_000`. It therefore takes
  the "API serves no offset past 10,000" hard-cap branch instead of ADR-0169's "an empty page is the
  end".
- **Cost.** It is the largest Oracle Board (529 s per run). ADR-0053 excludes it from eviction with no
  drain, so its closed postings are served indefinitely.
- **Verified.** Code read plus one live walk: stated 9,619, 9,609 read, empty page at 9,800.

### 3. Jibe discards postings that no scraper reads (BUG, 7/7, ~9,250 postings)
- **Mechanism.**
  1. `_icims_readable` fetches the apply host's `robots.txt`.
  2. `robots_verdict` maps any 4xx to ALLOW.
  3. `parse` then drops every posting on that host as "iCIMS covers it".
- **Where it goes wrong.** For 97 zero-yield clients, the backing tenant is `dead` in `icims.csv` and
  answers 404 for both `robots.txt` and `sitemap.xml`. Verified on ascension and conduent, with a live
  tenant as control. So the postings are read by nobody: ascension 3,172, conduent 1,243, mhm-services
  1,087, riteaid 983, and others.
- **Premise.** ADR-0189 decision 3 assumed "readable" implies "covered". That does not hold for dead
  tenants.

### 4. 36 ADP Workforce Now Boards are ADP's own QA/test clients (BUG, 7/7)
- **Example.** `adp:77f11391…/19000101_000001` has 1,797 rows, company `WFNPJL969`. Its top title
  is "NEW" (310 rows), then "BVT Analyst" and "RECT AUTO REQS_…", and 1,421 rows are at
  "BVT Location, Anchorage, AK".
- **The rest.** The other 35 look the same: `WFNQABVT41`, "FARM 61 BVT4", "NAS TEST CODE- Prod
  Enablement". Four cid pairs serve identical posting sets.
- **Scale.** 21–25 of them are scraped per run, 27–34k rows, 4–6k Board-seconds. About 190 of their
  rows are served, e.g. "Test Architect_may16".
- **Cause.** Discovery landed the test cids, and nothing excludes them.

### 5. Casing fossils are never evicted (BUG, 7/7)
- **Scale.** 1,969 stored rows on 343 Boards (Workday 1,824, SmartRecruiters 145).
- **What they are.** Each Board key's casing differs from the key the scope uses, e.g.
  `workday:boeing/external_careers` against `EXTERNAL_CAREERS`.
- **Why they never go.**
  - `plan_sync` resolves the Board in the id's own casing, so these ids are never in eviction scope.
  - `plan_prune` evicts only case duplicates, and these are singletons.
- **They are closed.** The newest Workday fossil `posted_at` is August. For SmartRecruiters, 0 of 12
  sampled are still listed.

### 6. The priority ledger never decays Boards that emit no job lines (BUG, 7/7)
- **Cause.** `update_ledgers.priority` builds its snapshot from job lines only.
- **What gets carried at stale scores.** Clean-empty Boards, raising Boards and Boards that left the
  Scrapable set. That is 4,960 rows holding 17.9% of all tech credit.
- **Visible effect.** The top 10 includes two parked Boards (`recruitee:rebootmonkey`,
  `smartrecruiters:EndeavorITSolution`) and one dead test tenant (`oracle:jpmc-test`) on every run.
  This violates CONTEXT.md's "Scored Board". The Head has room today (48.5k scored of 56k slots), so
  it will only start costing real seats once the Head fills.
- **Related.** Amazon's CAPTCHA-short reads are blended into its score.

## Degradations

### 7. Scope exclusion shields rows that should leave (7/7)
- **`freshteam:abnhire`.** It keeps 729 rows out of scope on every run. They are not unread postings.
  They are live postings the current tech filter rejects: the widget returns exactly 1,000 jobs,
  271 of which are tech.
- **`oracle:eluq`.** Scope-excluded rows are 342 → 346 → 350 and growing.
  - The API caps at offset 10,000, but a second walk in `POSTING_DATES_DESC` order reaches the other
    end: the two orders together read 11,475 of 11,500.
  - A sample of 20 ids outside both reads: 17 were closed.

### 8. Jibe clients wholly on held iCIMS Boards cost minutes for zero jobs, and set shard walls (7/7)
- **Cost.** 120–140 clients per run drop every row as iCIMS-covered, at 2.7–3.3k Board-seconds per run.
  commonspirit alone takes 515 s for 6,366 rows dropped.
- **Why it sets walls.** Shards start Boards in priority order, so these zero-score Boards start last.
  That is exactly what pushed the three over-budget shards: `36203593207` shard 13 (+339 s, commonspirit),
  `36213020963` shard 4 (apexservicepartners) and `36215851608` shard 9 (aus).
- **Why the gate misses it.** The value gate never evaluates them, because its 600 s floor skip runs
  before the zero-jobs veto.
- **The fix.** CLAUDE.md's Jibe landing rule already says to park them. 298 clients qualify on
  page-1 evidence.

### 9. The failures ledger misses gone signals (7/7)
- **Pattern.** `_GONE` matches only `HTTP Error 404|410`, so the Boards below error every run and
  can never quarantine (ADR-0162).
- **Missed signals.**
  - JazzHR "200 without the jobs_table shell; tenant departed", 20 per run. Live: 3 of 3 checked show
    an inactive career page.
  - Jobvite `302 …?invalid=1`.
  - DNS NXDOMAIN, checked on 3 hosts against 8.8.8.8.
- **Scale.** 39 Boards failed non-gone on all 7 runs.
- **Summary line.** The "did not read as gone" summary groups by exception class only. So
  "jobvite RequestException ×92" hid that these were transient `-> 404` responses.

### 10. Amazon loses listing pages to CAPTCHA (6/7 runs; truncated 4/7)
- **Per run.** 0–72 of 266 pages come back as CAPTCHA HTML on a 200 and are counted lost with no
  retry. That is up to 4,577 postings unread per run.
- **Measured retry** (n=2 walks, from a residential IP): an immediate retry recovered little. A single
  re-fetch 2–3 minutes later recovered 8 of 8 pages.

### 11. ADP recruiting outage; Workday maintenance mislabelled (2/7 and 1/7)
- **ADP recruiting.** HTTP 500 on all 276, then 511, Boards (02:10–03:16 UTC, every shard, many
  egresses). It was ADP's outage. Nothing was lost: the errors raised, so the Boards were
  scope-excluded.
- **Error text.** The errors name no URL, so it can't be told whether the site record or the listing
  failed.
- **Workday.** It now answers 303 to `community.workday.com/maintenance-page` ("Workday is currently
  unavailable.") and sometimes sends an XML Application_Error body on a 200. Both are labelled
  `unexpected-body` and never retried.

### 12. Taleo `scripps` serves ~265 closed requisitions as live (7/7)
- **Numbers.** 261–267 of 303 detail pages per run are Taleo's "The job is no longer available." page.
  They are logged as detail losses.
- **Verified.** 12 of 12 sampled pages.

### 13. Descriptions the scraper could read but doesn't (7/7)
- **Oracle.** fa-exty (12 of 12 probed), fa-exvn (11/12) and eibd (6/6) carry their text in
  `ExternalResponsibilitiesStr` / `ExternalQualificationsStr`, which is never read. fa-exty alone has
  96 unsettled Jobs.
- **Why the gap stays flat.** The description gap sits at 2,129–2,163 Jobs on about 404 Boards.
  Most of it is genuinely absent at source (Oracle boilerplate-only payloads, 140 of 140 empty zwayam
  microland details). But since ADR-0089 nothing settles it, so it is re-counted, and zwayam's 339
  are re-fetched, every run.

### 14. `update_meta` "lost" derivations are one-run flaps (7/7, low)
- **Counts.** Experience "lost" per run: 6, 2, 0, 8, 13, 2, 17. Salary: 1, 3, 0, 0, 2, 7, 10. Each
  mirrors the next run's "gained".
- **Why it isn't a regression.** All runs were on the same SHA. A degraded read nulls an input for
  one run.

## Waste

### 15. Empty ADP career centres (12–13% of all scrape Board-time, 7/7)
- **Numbers.** 6,707 ADP Tail Boards returned 0 postings on every look. About 2,200 run each time, at
  608–663 serial minutes per run.
- **Why they are slow.** ADP's process-wide 0.4 s pacer holds about 51% of every shard's worker time.
  An empty ADP Board takes a median 16 s, against 0.1–2 s elsewhere.
- **Pacer use.** One paced request per Board per run goes on re-resolving a company name that could
  be cached. No ADP 429 appeared in 105 shard-runs.

### 16. Merge re-downloads 20 stale vector indexes (7/7)
- **What grows.** Every `refresh-indexes` leaves another ~395 MB IVF_SQ `auxiliary.idx`, and nothing
  removes the old ones until the daily compaction.
- **Numbers.** By the newest run, 21 index copies were 8.3 GB of 14.9 GB live storage. The merge fetch
  grows 0.41 GB per run and is its slowest step (150–251 s).
- **Bound.** Compaction does bound it: peak ~18 GB live at ~26 runs per day.

### 17. Duplicate or needless work on the critical path (7/7)
- **Unused state files.** scrape-plan fetches 327 `role_trend_board_deltas` files it never reads,
  one more each run.
- **Repeated parsing.** The join re-parses the 3.2M-line scrape three times and decompresses the
  description store three times: about 3–3.5 min.
- **CUDA wheels.** `.[embed]` installs 2.3–2.9 GB of CUDA wheels on CPU-only runners, 3–6 times per
  run.
- **Closed postings.** HCLTech re-fetches ~1,270 known-closed postings every run, and Zoho ~2,420.
- **Embed cost model.** It over-charges 2–4× on long Docs (≤4096 bucket: 4.9 s measured against
  18.0 s assumed), which doubled the embed shard count on busier runs.

## Risks with a date

### 18. `ubuntu-latest` becomes Ubuntu 26 on 2026-10-19
- **Where it shows.** 148 of 148 jobs carry the migration notice.
- **Why it matters.** WARP's install is proven only on 24.04, and WARP is dialled on 105 of 105
  shards (Workday 429s within ~25 s).
- **What exists already.** Cloudflare's `resolute` repo exists, but the install step is untested
  there.

### 19. Unpinned dependencies
- **`huggingface_hub`.** scrape-plan runs 2.0.0 (released 2026-09-24) while the rest run 1.33.0.
- **sentence-transformers.** It warns that `get_sentence_embedding_dimension` and `tokenize` are
  renamed. It is unbounded, so the removal will crash embed and role trends.
- **Model revision.** The nomic model loads with `trust_remote_code=True` and no pinned revision, and
  its Hub calls are unauthenticated even on cache hits.

## Log defects (7/7 unless noted)

| Line | What is wrong |
|---|---|
| grace-period line | Says emit-nothing Boards "leave the eviction scope" (false; that is finding 1). It names no Boards that went from some jobs to 0. |
| `reclaim_storage` | Prints "reclaimed 1.60 GB" while deleting 2.06 GB, because HF's `usedStorage` lags. Its docstring figures are stale. |
| `_deletions` threshold comment | Models growth as ~7.7n². Measured growth is linear at ~45 per run, so the 3,000 backstop fires ~66 runs after a missed cron, not ~1 day. |
| scrape-plan | Prints "rest estimated from their ATS median" at 100% coverage. Uses the retired "priority/exploration" names. Serial minutes are unlabelled. Names 10 of 34 gated Boards. |
| Fresh coverage | `DEGRADED` threshold (2%) was calibrated on 20k Slices. It fired on 5 of 15 shards of a normal run. |
| failures ledger | The quarantine sample prints the standing stock alphabetically, not this run's arrivals. |
| `held_refetch` | "N of M due" counts ids already evicted (95%). Tesla is missing from its list. |
| harvest | Raises `##[warning] … unexpected` annotations with tracebacks for classified outcomes (32 annotations). |
| zoho | Counts closures as detail losses (96–98% of its "losses"). |
| spare egress | Says "tesla … Boards lost" when Tesla actually rotated (1/7). |
| phenom | 70 "read N of N (100.000%) … carries the 0 missing id(s)" lines per run. |
| `fanout_retries.py` | Doesn't know Zoho's `http-302` class. |
| merge upload | tqdm progress bars are 15–20% of merge logs. |

## Checked and fine

- **Quarantine (ADR-0162) engages.** 167 Boards reached 20 strikes on the newest run. The control run
  skipped all 167 (`quarantine: skipped 167 of 167`).
- **The three-run error cycle is the Tail rotation.** Tail cohorts are {1,4,7}, {2,5} and {3,6}.
  Quarantined-then-released dead Boards clustered in the first cohort after ADR-0229 raised the
  threshold from 5 to 20.
- **Counts reconcile exactly.** Corpus minus non-English minus refused cross-site duplicates, plus
  Boards outside the Slice, equals the table's rows. The same chain gives the vector store's count.
- **Pipeline mechanics.**
  - Chain dispatch worked 7/7, with a 14–18 s hand-off.
  - Compaction runs daily (~5 h after its cron time). `complete publication: yes` 7/7.
  - WARP connected 105/105, with no egress losses.
  - 0 errors and 0 crashes; all 50 tracebacks are caught per-Board exceptions.
- **Earlier fixes held** (from the 2026-09-24 review):
  - Zoho "no jobs blob" is gone.
  - taleo_be losses fell from ~1,050 to 98 per run.
  - The Workday second pass recovers 14–31 pages per run.
  - The planner's actual/predicted median is 0.97–1.03.
- **SuccessFactors' 99.4% tech keep is by design.** The scraper gates on the URL slug before fetching,
  and none of 262 gated-out pages checked live had a tech title.
- **Amazon.** All 29 Amazon evictions were real closures.

## Fixes

Every finding above is being fixed in its own PR group, under ADRs 0238–0245. This section lists the
merged PRs once they land.
