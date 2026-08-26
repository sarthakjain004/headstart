# Workday's detail gap: what the NGC measurement actually found

**Date:** 2026-08-26
**Runs read:** 32942748996 (07:26 UTC) and 32936269675 (05:59 UTC), both `head=f565cbc`
**Board under investigation:** `workday:ngc/Northrop_Grumman_External_Site`

## Summary

The opening framing was that NGC's detail endpoint is refusing us, that egress rotation is being
spent trying to get past it, and that the fix is somewhere around throttling. **Measurement
disproved the central part of that.** NGC's detail endpoint serves us fine, and the misses are
not 429s. What is real is that the loss is invisible: Workday's detail pass maps every possible
failure onto one untyped `None`, so two runs that failed for demonstrably *different* reasons
produced identical-looking log lines.

The fix in this change is to make the detail pass say what it lost details to, using the machinery
`_paginate` has had for the listing pass since ADR-0076. Everything else in this document is
evidence, plus a recommendation on the recovery levers, which need a decision rather than a
silent pick.

## 1. The endpoint is not refusing us

Ran the real detail-fetch path (`fan_out_async` → `http.fetch_async`, same retry ladder, headers
and URL construction as the scraper) against the whole live board:

| arm | result |
| --- | --- |
| NGC, 200 details, width 6 | **0/200 missing**, `{HTTP 200: 200}`, 18s, 0 retries |
| NGC, 200 details, width 25 | **0/200 missing**, `{HTTP 200: 200}`, 11s, 0 retries |
| NGC, **3,691** details (the full board), width 25 | **0/3,691 missing**, `{HTTP 200: 3691}`, 318s |

The full-board arm spent 154 `429-ratelimit` retries and 142 `network` retries, and the existing
ladder recovered **every one of them**. Sample size: the entire board, twice over.

So NGC does rate-limit under sustained width-25 load — and the retry ladder already handles it.

Tool: `scripts/bench/probe_workday_detail.py` (the Workday counterpart to
`probe_eightfold_throttle.py`).

## 2. The CI misses are not 429s — proved from the run's own accounting

This is the part that matters most, because it is the same trap the freshteam change fell into a
week ago ("it gets 429s, so it should rotate like workday does" — measured: 72% 502, 28% timeout,
**zero** 429s, PR closed unmerged).

`spare_egress.note_settled` buckets every request the spare egress carried: `rescued` = settled
200, `walled` = settled on a status the request treats as a wall (429 for Workday), `other` =
anything else, including a request that never settled at all. NGC's shard reported:

```
workday: walled; spare egress rescued 31,156/31,156 walled request(s) (100%);
         40,362 attempt(s) carried, 7,444 settled non-wall
```

**`walled` = 0.** Not one Workday request on that shard finally settled on a 429. NGC's 3,536
misses are inside the 7,444 `other`.

The retry counters agree. That shard spent `429-ratelimit 1968, network 3930` in total, across
*every* board on it. A 429 that fails costs 2 retries, so at most ~984 requests shard-wide could
have been 429-failures — against 5,769 detail misses on that shard's Workday boards alone
(ngc 3,536 + hpe 1,107 + lonza 641 + thomsonreuters 415 + relx 70). **The overwhelming majority
of the misses consumed no retries at all**, which means they settled on the first attempt with
something that is neither 200 nor in `_TRANSIENT` — or raised immediately.

This also answers the 134s-vs-1,658s question. They are not the same failure:

- Run 32942748996 finished NGC in **134s** — ~28 requests/s, i.e. roughly one round trip each.
  Only a non-retried outcome is that fast.
- Run 32936269675 took **1,658s**, and its shard carried an anomalous `5xx 7904` retries (every
  other shard in that run: 1,300–2,100). 7,904 5xx retries ÷ 2 per failed request ≈ 3,950
  requests — against NGC's 3,569 misses. That run's misses look like **retried 5xx**, which cost
  the full backoff ladder and produced the 12× wall clock.

Same ratio, two different causes, one indistinguishable log line. That is the defect.

## 3. What it is *not*

Each of these was a live hypothesis and each was killed by measurement:

- **Not per-instance host.** Only `wd1` serves NGC; every other `wdN` returns 422 for the listing
  *and* the detail identically, so `_resolve_instance` cannot land on a host that lists but does
  not detail. (18 instances swept.)
- **Not per-time or per-shard-moment.** On one shard: `blackrock` (wd1) 0.3% finished 07:57:24,
  `ngc` (wd1) 95.8% at 07:57:31, `roche` (wd3) 0.2% at 07:57:33, `lonza` (wd3) 93.4% at 07:57:44.
  Good and catastrophic boards interleave second by second through the same proxy.
- **Not board size.** `>50%`-gap boards are 7–24% of boards in *every* size band from <100 to
  >2,000 details.
- **Not Job-id churn.** `_posting_key` prefers the detail's `jobReqId` and falls back when the
  detail is missing, so a lost detail could in principle rename a posting. Measured on NGC:
  **10/10 stable**. (But see §6 — `roche` renames 10/10.)
- **Not one uniform Workday problem.** `iheartmedia/ihm_technology_site` (42/42 missing) is a
  genuinely *different* bug: every one of its detail URLs returns a real
  `{"errorCode":"HTTP_404",...}` from any IP.

What it **is** correlated with is the shard: 46.0% missing on shard 0 and 34.7% on shard 7 against
0.4% on shard 13 and 3.0% on shard 6. That points at the shard's egress environment, not the
tenant — and the shard's egress environment is where the remaining evidence runs out, because the
scraper discards it.

## 4. The actual defect, and the fix in this change

`_job_detail` / `_job_detail_async` caught `http.RequestsError` and returned `None`, and
`_extract_detail` returned `None` for every non-200. `report_detail_gaps` then logged one INFO
line with a count. So a 404, a 429, a 503, a spent retry ladder, a severed connection and a
posting with no `externalPath` were all the same output.

`_paginate` has not had this problem since ADR-0076 — it collects a `Counter` of
`_failure_class(exc)` and reports `1 of 185 page(s) failed mid-crawl (HTTP 500 x1)`. The detail
pass now does the same thing with the same helper:

- `_job_detail` / `_job_detail_async` take an optional `classes` Counter and record the settled
  status (`HTTP 429`), the exception class for a request that never settled, `no externalPath`,
  or `unparseable` for a 200 whose body isn't the JSON the API promises.
- `_report_detail_losses` logs one line naming the classes, at **WARNING** past
  `_MAX_LOST_DETAIL_SHARE` (half) and INFO below it. A 96%-empty detail pass previously produced
  no warning at all. It **replaces** `report_detail_gaps`'s count-only line rather than adding to
  it — the two carried the same two numbers, and a second near-synonym line double-counted every
  Board for anything grepping them.
- The classes shown always total the loss count: anything that escaped labelling is reported as
  `unclassified xN`, so `(HTTP 404 x10)` on a 3,536-loss Board can never read as the explanation.

The line is worded to `_paginate`'s shape — `N of M thing(s) failed mid-crawl (…) — tail` — so one
regex reads both passes and the noun says which. The tail is narrow on purpose: it asserts only
that *this* pass does not mark the Board truncated. It does not claim the listing was whole
(`_paginate` can `mark_truncated` and return, so a Board can lose pages *and* details in one run),
nor that the loss is harmless — see §6 on `_posting_key`.

Verified against live boards:

```
WARNING workday:iheartmedia/ihm_technology_site: 42 of 42 detail(s) failed mid-crawl (HTTP 404 x42) — not a truncation (the listing pass reports its own)
```

One run of the pipeline with this in place settles what the remaining 7,444 `other` outcomes
actually are, which is the precondition for aiming any throttling or egress change at them.

## 5. Should the detail pass be load-bearing (`mark_truncated`)? — No

`report_detail_gaps` returns its count so a scraper whose detail pass is load-bearing can
`mark_truncated` on it (ADR-0053) — where load-bearing means, in `base.py`'s own words, "one
where `parse` drops the Job without it". Workday's `parse` keeps the Job, so it is not, and it
should stay that way, for two reasons. (Recorded as **ADR-0088**.)

**It would be a category error.** ADR-0053's Unauthoritative Board means *this Board's scraped
list cannot be read as its complete set of openings*. NGC's **listing** was complete — 185/185
pages in run 32942748996. Every posting on the Board was read; what is missing is enrichment of
postings we definitely have. Marking the Board unauthoritative would assert something false about
a different thing.

**And it would cost more than it saves.** Scope exclusion has **no bound and no drain**: a Board
short on every run never re-enters the eviction scope and serves its closed postings indefinitely
(measured precedent: 105 dead rows on `careers.qualcomm.com`, oldest 22 days,
`docs/eightfold/no-client-side-fix-for-replica-instability.md`). NGC misses ~96% of its details on
*every* run, so it would be excluded on every run, permanently — 3,691 rows frozen against
eviction forever, to protect against an eviction risk that does not exist here, because the
listing that drives eviction is complete. Unlike the ADR-0046 collapse guard it reports only a
Board count and never a row count, so the accretion would be invisible.

The honest description of NGC's state is "complete Board, degraded Jobs", and the description
store (ADR-0050) plus the gap ledger (ADR-0062) is the mechanism for that — **for the description
only**. Be exact about the limit: the store persists the description across runs, so that field
survives a lost detail. The other detail-only fields do not. `startDate` (the sole `posted_at`
source), `timeType`, `remoteType`, the real locations and `jobReqId` are re-derived from each
run's detail and go null when it is missing (ADR-0021). So "degraded Jobs" is fully recovered for
descriptions and merely *tolerated* for the rest — which is the intended cost, but it is not the
same claim, and §6's Lever A depends on the difference.

**One consequence is not tolerable and is not enrichment.** `_posting_key` prefers the detail's
`jobReqId`, so on a tenant whose fallback tiers disagree with it a lost detail *renames* the Job —
measured on `roche`, 10/10 postings renamed when the detail is absent, because `_looks_like_req_id`
rejects `202608-121268`. A renamed Job reads as one delisting plus one new posting, which is
eviction-shaped churn. NGC itself measured 10/10 **stable**, so the argument above holds there;
but the fix belongs in `_posting_key`'s detail-dependence (§6, Lever A's precondition), not in
marking the Board truncated — that would trade a narrow id defect for the permanent, undrainable
exclusion PR #316 measures below.

**This is not hypothetical, and another change is measuring the price right now.** PR #316 (open,
no behaviour change yet) tracked ADR-0053's exclusion across 16 runs and found a permanent
exclusion set already accreting on a different ATS: 23 SuccessFactors Boards — 82% of the
permanent set, 5,643 shielded rows (44.3%), growing monotonically (`careers.wipro.com` +30%,
`careers.hcltech.com` +39%). On Wipro, **9 "unreadable" detail pages in 4,273** exclude the whole
Board from eviction on every run, and a 60-page sample of those failures returned 60/60 HTTP 200.

Read that finding precisely: #316's root cause is a **classifier** defect, not the
truncate-on-detail-gap policy itself. `successfactors._titled_fields` returns `None` both for *we
could not read this page* and for *the tenant says this posting is closed*, and truncating on the
union counts closed postings as unread. #316's Option A **keeps** `mark_truncated` for a genuinely
unreadable page. So #316 does not conclude "a detail gap must never truncate", and this analysis
does not claim it does.

The two agree for a reason each states on its own ground. #316 fixes a Board *mistakenly* judged
short. Workday's is not short at all — the listing is complete, and its detail pass is not
load-bearing under `base.py`'s definition ("one where `parse` drops the Job without it"):
SuccessFactors' `parse` drops such a Job and so has standing to truncate, Workday's keeps it.
That contract carries the decision; #316 only prices being wrong. Recorded as ADR-0088.

## 6. Recovery levers — a decision is needed, not a silent pick

### Lever A — wire the ADR-0048 skip-list into Workday's detail pass (biggest effect, real tradeoff)

Workday **re-fetches every detail on every run** and never consults `have_details`. It is the only
large detail-pass ATS that ignores it; Eightfold uses it and says so
(`fetched 1876/3676 descriptions (1800 already held)`). The cost of that, measured on run
32942748996:

- `update_descriptions`: `workday: filled 5,076 from the store, **learned 185**`
- `skip-list: 415,236 Jobs held`

Roughly 290,000 Workday detail requests across the run produced **185** new descriptions. That
spend is why the `workday` group is walled **one second** into a shard
(`workday: origin returned 429 — spending this shard's spare egress for the rest of the run`,
logged at 07:55:19 for a shard that started at 07:55:18), which is what forces every subsequent
Workday request onto the shared WARP tunnel and into the rotation storms.

Cutting it would reduce NGC's requests to only its unsettled Jobs and let successes land on the
backlog instead of re-confirming text already held.

**Why this cannot simply be switched on.** Eightfold's detail carries only the description.
Workday's carries `startDate` (the *only* source of `posted_at` — the listing gives "30+ Days
Ago"), `timeType`, `remoteType`, `location`/`additionalLocations` (the rollup repair), and
`jobReqId`. Skipping a detail drops all of them, and on some boards it changes the Job's id:
measured live, **`roche` renames 10/10 postings** when the detail is absent, because
`_looks_like_req_id` rejects its `202608-121268` bulletField (it starts with digits, and neither
alternative in `_REQ_ID_SHAPE` admits that). A skip-list on top of that would rename postings
wholesale.

So Lever A needs one of: fix `_posting_key`'s detail-dependence first (worth doing regardless —
see below); persist the other detail fields beside the description; or accept the field loss
explicitly. That is a design fork, not an obvious call.

### Lever B — stop the self-inflicted rotation storm

NGC was the shard's #1 rotation consumer (demand 60 in one run, 33 successful rotations in the
other). Each rotation is a `systemctl restart warp-svc` that tears down the SOCKS listener every
Workday board on the shard is using. The leading unproven hypothesis is that this is what
destroys the in-flight detail requests — it would explain the fast, retry-free failures and the
strong per-shard correlation.

**I could not reproduce it.** On this laptop the same code rotated 18 times during the full-board
arm and lost nothing: 142 recoverable `network` retries and 3,691/3,691 successes. The platform
differs (`launchctl kickstart -k` vs `systemctl restart`), so a local negative is not a CI
negative. I am deliberately **not** proposing a change on this until the §4 logging returns one
run's worth of classes — that is precisely the evidence that would confirm or kill it, and
building on it now would repeat the freshteam mistake.

### Lever C — adjacent bugs found on the way, worth their own issues

1. **`iheartmedia`'s two boards 404 every detail** (`ihm_technology_site`, `iHM_Corporate_Site`,
   212 Jobs). Deterministic and reproducible from any IP — a real URL/tenant-shape bug, not
   throttling. With this change it now announces itself as `(HTTP 404 x42)`.
2. **`_looks_like_req_id` rejects a digits-first requisition id** (`202608-121268`), so `roche`
   and boards like it rename every posting whenever a detail is lost. Currently masked because
   roche's details almost always arrive.

## 7. Recommendation

1. Land the failure classification (this change) — it is safe, surgical, and it is the only thing
   that turns the remaining question into a one-run measurement.
2. Read the next run's `detail(s) failed mid-crawl (...)` lines for NGC and decide Lever B on
   that evidence.
3. Decide Lever A explicitly, after fixing `_posting_key`'s detail-dependence.
4. Do **not** `mark_truncated` on the detail gap (§5).
