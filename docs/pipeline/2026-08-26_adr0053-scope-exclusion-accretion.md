# ADR-0053's scope exclusion: what it actually shields, and why it never lets go

**Date:** 2026-08-26 · **Harness:** `scripts/runlog/scope_exclusion_persistence.py` (cross-run
persistence), `scripts/eval/successfactors_detail_gap_audit.py` (live Board verification) ·
**Captures:** `experiment/adr0053-scope-exclusion/artifacts/` (untracked — `experiment/` is
gitignored; see §6) ·
**Follow-up to:** `docs/eightfold/no-client-side-fix-for-replica-instability.md` §"105 confirmed-dead
rows", which recorded the same ratchet on Eightfold and named a bounded exclusion as the priority.

## The one-paragraph answer

The **run total** of eviction-candidate rows ADR-0053 withholds is *not* growing — it oscillates
around ~1,000 across the 16 most recent successful runs (945 → 813, min 813, max 1,391), and two
runs read in isolation can move in either direction. What *is* growing is the per-**Board** shielded
count on the 28 Boards excluded on **every** run: `successfactors:careers.hcltech.com` 179 → 248
(+39%) and `successfactors:careers.wipro.com` 73 → 95 (+30%, strictly monotone) over the same 20
hours. The run total hides this because the transient Boards churn in and out around it.

And the dominant cause is not a truncated scrape at all. On the 23 SuccessFactors Boards that make
up 82% of that permanent set, the **listing came back whole** and only the per-job detail pass came
up short — and a live check of 4,833 pages (all 4,433 on Wipro, 400 sampled on HCL) found **74
non-parsing pages and every one of them is the tenant's own *this requisition does not exist*
page**, with zero genuinely unreadable. **These Boards are excluded from the eviction scope
forever because closed postings are being misread as unread ones.** (*Excluded*, not *held* —
`held` is ADR-0046's collapse-guard cap, a different mechanism with a different unit and a drain.
The table below keeps the three apart.)

## Vocabulary — three mechanisms, three log lines, never interchangeable

CONTEXT.md's glossary is authoritative; this document uses it exactly. All three fired in run
`32942748996` and each reported itself separately:

| Mechanism | Unit | This run's line |
|---|---|---|
| **Scope exclusion** (ADR-0053) | per-**Board**, list not authoritative | `scope exclusion keeps 813 eviction-candidate row(s) out of scope across 74 Board(s)` |
| **held** (ADR-0046, drained by ADR-0055) | per-**Board** cap, >25% of rows lost at once | `collapse guard: withheld 226 evictions across 3 Boards … each drained up to 25% of its rows` |
| **Unconfirmed** (ADR-0083) | per-**Job**, first absence | `grace period: 871 id(s) unconfirmed, awaiting a second look before eviction` |

Only the first has **no bound and no drain**. The collapse guard drains 25% per run since ADR-0055;
the grace period is a two-scrape delay, not a hold. Nothing in this document infers which mechanism
fired from an outcome — each claim is read off that mechanism's own line.

**Eviction semantics are post-ADR-0083.** Every run read here has `ee97ebc` as an ancestor
(verified with `git merge-base --is-ancestor`), so a single absence only marks a Job **Unconfirmed**;
eviction needs a second consecutive *scrape of that Board*. Where this document says a row cannot be
evicted, it means the Board never re-enters the eviction scope at all — the grace period is not what
is holding it.

## 1. The measurement harness

`scripts/runlog/scope_exclusion_persistence.py` reads the `merge` job's `index sync` stage across N
runs and reports, per Board, how many of the runs in the window excluded it and what its shielded row
count was each time. Two log lines feed it:

- `scope-excluded Board: {board} — {why}` — **every** excluded Board with its `mark_truncated`
  reason. Shipped with ADR-0053 itself (#130), but as a *batched* `_log_ids` line until
  `8825031` (#160, 2026-08-18) reshaped it into one `_log_reasons` line per Board. The harness
  parses both shapes: reading only the newer one makes every pre-#160 run report **zero
  exclusions**, which is indistinguishable from a clean run. (Run `31851372193`, 2026-08-14, really
  had 11 excluded Boards and first read as 0 — the trap named in CLAUDE.md, hit and fixed.)
  Note this is *not* #280, which left this line untouched and added the row-count line below;
  an earlier draft of this document credited the reshape to #280 and was wrong.
- `{n} eviction-candidate row(s) kept out of scope on {board}` — the row cost, but only for the
  **top 10** Boards (`index._TOP_OUT_OF_SCOPE_BOARDS`), and only in runs built after PR #280
  (`960d991`, 2026-08-24). A Board outside the top 10 is shown as `·`: excluded, count unknown.

Why persistence and not the total: only ~20,000 of ~66,000 live Boards are in any run's slice
(`load_active_companies(min_jobs=0)` = 66,401, the set `scrape_plan` draws from), so the run total
tracks *which Boards happened to be scraped*, not the mechanism's effect. A Board's streak is much
less sensitive to that.

**But the streak's denominator is runs, and CLAUDE.md's unit is scrapes of that Board.** The
harness cannot supply the stricter unit: `index sync` logs the scraped-Board *count* (the `corpus:`
line) and never the scraped-Board *list*, so a Board that simply was not in a run's slice and a
Board that was scraped, came back authoritative and re-entered the eviction scope are both just
"not excluded this run". Every number below is stated with that limit in mind, and it cuts two
ways:

- **`N/N` is safe in the direction it is used here.** A Board can only be excluded in a run that
  scraped it, so "excluded on all 16 runs" does mean 16 scrapes, all of which excluded it. What it
  is *not* is a complete census: a Board excluded on every scrape it got but absent from some run's
  slice scores below 16. **The 28 is a lower bound on the persistent set, not its size.**
- **A gap is not a drain.** `k/16` for `k < 16` cannot be read as "it drained on the other
  16 − k runs" without separately confirming, from the *scrape* stage's logs, that the Board was
  in those runs' slices. §2 flags the one place this document leans on that and says what it would
  take to settle it.

## 2. The trend — the total is flat, the persistent Boards ratchet

The 16 consecutive **successful** runs `32844794201` (2026-08-25 11:55Z) → `32942748996`
(2026-08-26 07:26Z) — two runs inside that span (`32875901493`, `32898197891`) ended `cancelled` and
are left out — all heads descending from `99369d7`:

```
rows   945  910 1106 1391 1303 1160  888  971  983 1049  915  879  920 1057 1276  813
Boards  99   94   97   92   92  103   94   90   99   92   75   73   70   61   72   74
```

**No trend in the total.** First eight runs mean 1,084; last eight mean 987. Two runs against each
other prove nothing here, exactly as suspected.

**But split the total by *why* each Board was excluded and the leak separates from the noise.**
Summing only the named rows whose Board's reason is `N/M job pages unreadable`:

```
total       945  910 1106 1391 1303 1160  888  971  983 1049  915  879  920 1057 1276  813
detail-gap  283  294  300  314  315  326  323  330  332  441  491  350  358  390  370  426
```

The total ends 14% *below* where it started; the detail-gap component ends **51% above** (283 → 426),
and rises in 12 of the 15 steps. Because only the top 10 Boards get a row line, every class figure in
this document is a **lower bound**, not a total — `named` runs 598–1,206 against run totals of
813–1,391.

Underneath it, the Boards excluded on **all 16 runs** (28 of them — a lower bound on the
persistent set, per §1) move only one way:

| Board | run 1 → run 16 | change | truncation reason |
|---|---|---|---|
| `successfactors:careers.hcltech.com` | 179 → 248 | +69 (+39%) | `1745/10525 job pages unreadable` |
| `successfactors:careers.wipro.com` | 73 → 95 | +22 (+30%), **strictly monotone** | `9/4273 job pages unreadable` |
| `successfactors:careers.te.com` | 31 → 36 | +5 (+16%), **strictly monotone** | `3/2070 job pages unreadable` |
| `workday:dickssportinggoods/DSG` | 70 → 76 | +6 (+9%) | facet slice capped at 2,000 |
| `eightfold:ngc.eightfold.ai` | 23 → 65 | +42 (+183%) | `no new postings on sweep 2 — got 3649 of 3692` |

hcltech's series is `179 187 192 201 201 209 203 210 212 212 212 213 220 234 244 248` — one dip in
sixteen steps. This is the ADR-0055 signature verbatim: that ADR unwound the collapse guard's ratchet
after watching `withheld` climb 267 → 953 and finding three Boards withholding an *identical* count
every run. The same shape is here, on the mechanism ADR-0055 did not touch.

Contrast the Boards whose truncation is **transient**: `eightfold:careers.qualcomm.com` runs
`35 38 31 32 31 24 59 45 29 32 - - 20 - - 48`, where `-` is a run that did **not** exclude it.

**What that contrast does and does not establish.** The two classes are certainly different in
the data: hcltech is `16/16` with a rising trail, qualcomm is `12/16` with a trail that moves both
ways. But per §1 a `-` is ambiguous — qualcomm may have re-entered the eviction scope and drained
on those four runs, or may simply not have been in their slices, and this harness cannot tell.
The part that needs no slice information is the shape of the values *inside* qualcomm's own
excluded runs: `59 → 45 → 29`, and `32 → 20`. Those falls are real, but they too have two causes —
rows were evicted, or the next scrape simply returned them and they stopped being
eviction-candidates — and this harness separates neither. Either way they are movement, and
hcltech's `179 … 248` (one dip in sixteen) has none.

So the honest form of the claim is about **variance, not drainage**: the SuccessFactors detail-gap
class ratchets monotonically because its shortfall reproduces on every single scrape, while the
eightfold class oscillates because its shortfall does not. That is the distinction Option D keys
on, and it is measured. What is *not* established is the stronger sentence an earlier draft
carried — that eightfold "re-entered the eviction scope and drained" on its `-` runs. Settling
that needs the `scrape` stage's logs for runs 11, 12, 14 and 15 checked for a
`careers.qualcomm.com` board line — ~15 shard logs per run, which is why it was not done for this
pass, and nothing below depends on it.

Across a longer, daily-sampled window (12 runs, 2026-08-14 → 2026-08-25) the excluded-**Board** count
goes `11 9 17 16 17 34 59 66 51 65 80 75`. That growth is real but **confounded, and must not be read
as the mechanism worsening**: two commits taught SuccessFactors to report its own detail shortfall
inside the window, and each one added a cohort.

- `e4ccecf` (#193, 2026-08-19) — "a lost detail page is not a delisting"; the first SuccessFactors
  Boards appear in the exclusion set on exactly that day.
- `3cddc4b` (#266, 2026-08-23) — a title-less 200 counts as a detail-fetch loss;
  `careers.hcltech.com` and `careers.wipro.com` first appear on exactly that day.

Both changes were right about the thing they fixed (#266 closed a *false eviction* — HCL jobs were
being deleted while still open). What follows is that they were wrong about the classification.

## 3. Root cause — the listing is whole; the "unreadable" pages are closed postings

Of the 28 Boards excluded on all 16 runs:

| Class | Boards | Shielded rows (named, all 16 runs) |
|---|---|---|
| **detail gap — listing whole** (`N/M job pages unreadable`), all SuccessFactors | 23 | 5,643 (44.3%) |
| listing cap — Workday slice capped at 2,000 with no facet left to split | 4 | 1,182 (9.3%) |
| listing cap — SuccessFactors RSS 30 MB read cap (`jobs.crh.com`) | 1 | outside top-10 |

The detail-gap class is 82% of the permanent set and over half of the last run's shielded rows
(426 of 813). Its shortfalls are tiny relative to the Board: **1 of 2,062** on
`careers.bureauveritas.com`, 1 of 1,435 on `bertrandt`, 1 of 293 on `komatsu.jobs`, 9 of 4,273 on
Wipro. **One unreadable detail page in two thousand excludes the entire Board from eviction, on every
run, permanently.**

### Verified live against the Boards, 2026-08-26

The listing surface is not truncated at all. One GET, two seconds, no `cut_short`:

```
careers.wipro.com   : kind=urlset bytes=836,768   listed=4,433
careers.hcltech.com : kind=urlset bytes=1,936,314 listed=10,523
```

The detail pages that "fail" all return **HTTP 200**. Sampling 60 of them found zero non-200s and
zero timeouts — 17 of 60 simply carried no job. Re-fetching one URL 12 times over a single session
returned the same empty shell on 11 of the 12: not flaky, not rate-limited, not an anti-bot
interstitial.

The control settles what the shell means. Requesting a requisition that **cannot exist**:

```
nonexistent-req   https://careers.hcltech.com/job/Totally-Fake-Role/999999-en_US/  200  ~111,009 chars
untitled-real     https://careers.hcltech.com/job/Snowflake-Technical-Lead/…/      200  ~111,009 chars
titled-real       https://careers.hcltech.com/job/SME-JAMF/…/                      200   124,506 chars
```

**The "unreadable" job page is the tenant's own "this requisition does not exist" page**, to
within a few hundred characters. Wipro behaves identically (96,632-char shell for a fabricated
requisition). Both tenants keep serving the URL from a sitemap they regenerate without dropping the
closed entries — every HCL sitemap entry carries the same `<lastmod>2026-08-22</lastmod>`, so the
sitemap cannot be used to tell them apart either.

**It is not byte-identical, and a fix must not assume it is.** An earlier draft of this document
said "byte-for-byte", which review falsified. Re-measured on HCL 2026-08-26, diffing whole bodies:

- Two *fabricated* requisitions differ in exactly **one line**: the page echoes its own id back
  (`jobID : '999999\x2Den_US'` vs `'888888\x2Den_US'`). No two ids can ever produce equal bytes.
- A real-but-closed posting differs from a fabricated one by that same `jobID` line **plus** a
  ~514-char `ctid` tracking `<script>` block, which the tenant emits on the *first* response of a
  connection and omits thereafter. Hence the 11/12 above rather than 12/12: fetching one URL over
  **12 fresh sessions** gave **12 distinct bodies** (111,523 / 111,524 chars); over one shared
  session, 11 identical after the first (111,010, with the first at 111,524).

So the shell drifts by ~514 chars on a ~111,000-char page, against a ~13,500-char gap to the
nearest real posting (titled HCL pages measured 121,344–127,018 across a 150-page sample). A length window discriminates cleanly;
a hash or `==` would score **every** closed page `unreadable` and invert the conclusion. That is
what `_SHELL_SLACK = 1024` in the audit script is, and why Option A below is written as "score
against the control", never "compare bytes".

`scripts/eval/successfactors_detail_gap_audit.py` scores every failing page against that control:

```
successfactors:careers.hcltech.com: n=400   open=333   gone=67  unreadable=0  in  41s   (random sample)
successfactors:careers.wipro.com:   n=4433  open=4426  gone=7   unreadable=0  in 390s   (every listed page)
```

**Zero genuinely unreadable pages on either Board.** HCL's 67/400 = 16.8% matches the pipeline's own
`1745/10525` = 16.6%. Wipro's sweep is exhaustive, not a sample: **all 4,433 listed pages**, of which
exactly **7** are closed postings — and those 7 are what keeps 95 rows out of the eviction scope, a
13.6x over-reaction to a shortfall that is not a shortfall.

Note what that implies for Wipro specifically. Only 7 listed ids failed to build, so at most 7 of the
95 shielded rows can be explained by a page we could not read; the other ~88 are indexed ids that are
**not in the live listing at all** — fully delisted postings the served table cannot shed. The one
path by which a shielded row could still be open is the tech filter newly dropping a Job whose
description changed, which is neither measured here nor plausible at that scale.

### The chain, stated once

`_titled_fields` returns `None` for two different conditions — *we could not read this page* and *the
tenant says this posting is gone* — and `report_detail_gaps` counts both as a loss. `lost > 0` calls
`mark_truncated`, `scrape_join` writes the Board into `unauthoritative_boards.json`, `index sync`
subtracts it from the eviction scope, and `prune` cannot reach it because the Board is still Live
(ADR-0023 evicts only off-Board and duplicate rows). Nothing else deletes. The rows stay.

ADR-0053's own Consequences section predicted this in one sentence — "a Board that fails every scrape
while staying `live` keeps its rows indefinitely … it is unbounded" — and named the two fixes it did
not make. This is that consequence, measured, with a third cause it did not anticipate: the Board is
not failing at all.

## 4. The user-visible harm

**How many.** `index sync` computes the shielded count from the live table (`index_ids - fresh`), so
these are served rows, not an estimate: **813–1,391 per run**, of which the detail-gap class is
**283–491 and rising**. On the two Boards verified live, essentially all of it is dead: an indexed
row absent from `fresh` on a Board whose listing came back whole either failed to parse (**74 of 74**
non-parsing pages, over 4,833 sampled across the two Boards, are closed) or is not in the live
listing at all. The residue that could still be open is a row the tech filter newly dropped, which
needs the description to have changed — not measured here, and not plausible at this scale.

**Sample sizes, stated plainly.** Wipro: **4,433 of 4,433** listed pages — exhaustive. HCL: **400 of
10,523** random, seed-pinned. Detail-fetch outcome distribution: **60/60 HTTP 200** on a separate
probe of each Board, and **11 of 12** identical responses on one URL repeated over a shared
session, so this is not rate-limiting and not flake. (The twelfth is the first response of the
session, which carries the tracking block described in §3 — over *fresh* sessions all 12 differ,
which is why the classifier is a length window and not an equality test.)

**Independently reproduced during review, 2026-08-26.** A separate 150-page random sample of the
HCL sitemap returned **150/150 HTTP 200** with **32 generic shells (21.3%)**, against this
document's 16.8% and the pipeline's own 16.6% — consistent within sampling error at n=150. The
fabricated requisition `/job/Totally-Fake-Role/999999-en_US/` returned HTTP 200 with
`<title> Job Details | HCLTech</title>`, and the current sitemap held 10,522 `<loc>` entries from
one GET. The core finding replicates; only the identity claim needed correcting.

**How old.** Continuous exclusion, dated from the run logs:

| Board | excluded since | consecutive runs | shielded now |
|---|---|---|---|
| `successfactors:jobs.crh.com` | ≤ 2026-08-14 | all 12 daily samples + all 16 recent | outside top-10 |
| `successfactors:careers.hcltech.com` | 2026-08-23 | every run since | 248 |
| `successfactors:careers.wipro.com` | 2026-08-23 | every run since | 95 |
| `eightfold:careers.qualcomm.com` | intermittent | 12 of 16 | 48 |

**Has it grown since the qualcomm precedent?** That measurement (2026-08-24) was 105 confirmed-dead
rows on one Board, oldest 22 days, across 21 consecutive runs of exclusion. Two days later
`careers.hcltech.com` alone shields **248** — 2.4x the qualcomm figure — and it reached that in three
days rather than twenty-two, because its shortfall reproduces on every scrape where qualcomm's does
not. The *count* has grown. The *age* has not yet: qualcomm's 22-day-old rows are still the oldest
because that Board has been excluded far longer. And the honest qualifier: 2026-08-24 also predates
the two commits that admitted the SuccessFactors cohort, so part of the growth is newly-visible, not
newly-broken. What is new is that the largest accretor is now a Board whose list was never short.

**One number that is *not* measured here.** The exact `first_seen` age of the oldest shielded row
would need `data/lancedb/` (2,883 MB); it was started and abandoned as not worth the transfer, since
`first_seen` is null on ~77% of rows anyway. Continuous-exclusion age from the logs is the better
measure of how long a Board has had no removal path, and it is what the table above reports.

## 5. What to do

Two layers, and the first is the one that makes the accretion disappear at source. **Removing these
Boards from the scrape, parking them, or demoting them in liveness is not on this list** — they are
live employers with thousands of open jobs, and the problem is bookkeeping, not the Board.

**PR #315 is already applying a stronger version of this on Workday**, and the two should be
decided together. It declines `mark_truncated` on `ngc.eightfold.ai`'s detail gap on exactly this
mechanism's account — "NGC's *listing* was complete (185/185 pages), so the Board is authoritative
and only enrichment is missing … ADR-0053's no-drain exclusion would freeze 3,691 rows against
eviction on every run forever" — which is the same diagnosis this document reaches from the
measurement side, and the same qualcomm precedent. No contradiction. But the two rules are not the
same rule, and picking one is part of the decision below:

- **#315's rule** — *a complete listing means never `mark_truncated` on a detail gap*, whatever
  the pages turned out to be. Simple, needs no control fetch, and if adopted generally it
  **subsumes Option A**.
- **Option A's rule** — a complete listing plus a *proven-closed* page means no
  `mark_truncated`; a genuinely unreadable page still excludes the Board.

A keeps a signal #315's rule discards: a Board whose detail pass is being blocked (an anti-bot
interstitial served with 200, the eightfold CAPTCHA wall #266's own investigation found) still
looks like a complete listing, and under #315's rule its unread rows would be evicted rather than
shielded. On the two tenants measured here that population is empty (**74 of 74** non-parsing
pages closed, zero unreadable), which is evidence for #315's rule being safe *on these Boards* —
not evidence that it is safe everywhere. If #315's rule is adopted repo-wide, say so explicitly
and retire Option A rather than shipping both.

### Option A — teach the scraper that a not-found page means *gone*, not *unread* (root cause)

When a SuccessFactors detail pass loses pages, fetch **one** control URL for that Board — a
requisition id that cannot exist — and score each title-less page against it. A page matching the
control is a **closed** posting: drop it from the returned list as today, but do **not** count it in
`report_detail_gaps`, so `mark_truncated` never fires and the Board stays authoritative. Only pages
that are genuinely unreadable keep the Board out of scope.

- **Fixes:** up to 23 of the 28 permanently-excluded Boards, at source, with no change to eviction
  semantics. "Up to", because two of the 23 tenants were verified live; the other 21 share the
  truncation reason and the ATS, not a checked not-found shell, and a tenant whose shell the
  control cannot match keeps today's behaviour.
  The closed rows then leave through the ordinary ADR-0083 path — still two consecutive scrapes, so a
  misclassification costs a delay, not a deletion.
- **Cost:** one extra request per SuccessFactors Board *that has any loss* (2,164 active Boards, a
  few dozen affected per run). Needs a byte tolerance, and a tenant whose shell varies per request
  would fall back to today's behaviour, which is safe.
- **Risk:** if a tenant ever serves the not-found shell for a *live* posting, we would evict it. The
  grace period is the backstop.
- **Leaves open:** the Workday 2,000-cap and RSS-30 MB Boards, whose listing genuinely is short.

### Option B — report unauthoritativeness per **Job**, not per **Board**

`mark_truncated` gains a companion that carries the *ids* the scraper listed but could not build.
`scrape_join` writes them beside `unauthoritative_boards.json`; `plan_sync` subtracts those ids from
the eviction candidates instead of dropping the whole Board.

- **Fixes:** the blast radius everywhere a scraper can enumerate its own gap — Wipro's 7 unbuilt ids
  would shield at most 7 rows instead of the whole Board's 95.
- **Does not fix HCL.** Its 1,745 unbuilt ids *are* the shielded rows, so a per-Job shield is still a
  1,745-id shield. B is the right generalisation and the wrong fix on its own; it needs A.
- **Cost:** new per-id state on the merge path, and Board-wide exclusion must remain for scrapers
  whose *listing* truncated (they cannot enumerate what they never saw).

### Option C — bound the exclusion, mirroring ADR-0055 (containment)

An excluded Board still evicts its oldest-`first_seen` missing rows up to a cap per run and withholds
the rest, exactly as ADR-0055 did to the collapse guard's hold.

- **Fixes:** every class, including the Workday and RSS caps that A and B cannot reach. One change,
  in `plan_sync`, with a precedent whose test (`test_a_persistently_truncated_board_drains_instead_of_ratcheting`)
  already encodes the invariant.
- **Concession:** ADR-0053's signal is *stronger* than the collapse guard's — the scraper proved its
  list was short — so overriding it is a bigger admission than ADR-0055 made. A Board genuinely
  unread would shed live rows.
- **Calibration warning:** ADR-0055's cap is `max(1, int(COLLAPSE_RATIO * len(ids)))` where `ids`
  is the Board's **indexed rows** (`index_plan.py:223,236`) — not its listing size, and the drain
  is taken from `eligible ⊆ absent`, so it can never evict more than the missing set. An earlier
  draft read the ratio against HCL's 10,525 *sitemap listings* and warned it would "keep going"
  past the shielded set; both halves were wrong. The real warning is milder and still real: a
  quarter of a large Board's indexed rows is far more than its ~248 shielded rows, so inheriting
  ADR-0055's 25% would clear the whole shielded set on the first run — the cap would not be
  binding, which is the opposite of a bound. A cap for this mechanism has to be fitted to the
  *missing* set, and fitted to data rather than inherited.

### Option D — drain only Boards excluded on K consecutive **scrapes of that Board**

Keep the exclusion absolute for a transient truncation and start draining only once a Board has been
unauthoritative K scrapes in a row — the unit ADR-0083 already established, and the
"consecutive-misses ledger" ADR-0055 considered and deferred.

- **Fixes:** exactly the failure mode measured here, and *only* it. A Board whose exclusion breaks
  never reaches K, so the oscillating eightfold class is untouched; hcltech, wipro, te.com, crh and
  the Workday caps ratchet on every scrape and are reached.
- **Cost:** new per-Board streak state, and another threshold to fit. Slower to act than C.
- **Note the unit.** K must count *scrapes of that Board*, as the heading says — not runs. That is
  the unit ADR-0083 already keeps per-Job state in, and it is precisely the unit §1's harness
  **cannot** see from the merge log, so the streak has to be recorded at scrape time rather than
  reconstructed from logs afterwards.

### Recommendation

**Ship A first, then D; hold B and C.**

A is the only option that makes the leak *not exist* rather than bounding it, it is confined to one
scraper, and 82% of the permanent set is inside it. D is the right containment because it is the one
that keys on the distinction the data actually shows — a Board whose shielded count *oscillates*
(a shortfall that does not reproduce) against one that *ratchets monotonically* (a shortfall that
reproduces on every scrape) — instead of taxing both. Note D needs only that distinction, which is
measured; it does not need the stronger "the oscillating class already self-drains", which §2
retracts as unresolvable from these logs. C taxes both, and its inherited 25% would not bind at all
on the Boards that matter here. B is a real improvement to the signal's shape and worth doing, but
it is a refactor of the reporting path that does not, on its own, move the largest number in this
document.

If only one thing ships, it is A.

**No ADR is filed with this document.** The call between these options is the user's; ADR-0053's own
successor should be written once one is chosen, the way ADR-0055 was written for ADR-0046.

## 6. On regression tests — where a seam exists and where it does not

**There is no unit-testable seam for the accretion itself.** "A Board's shielded row count only ever
goes up" is a property of production data across many runs and many scrapes of one Board; it has no
call site, no fixture, and no assertion that could go red on it. The feedback loop for it is the
measurement harness — `scope_exclusion_persistence.py` over ≥10 runs — and that is the honest form of
the loop, not a substitute for one. It is deterministic (the logs are immutable and cached), fast
after the first fetch, and it goes red in the sense that matters: a Board at `16/16` with a rising
trail is the failure, and the same command shows it falling once a fix lands.

**The root cause does have a seam**, and any fix should be tested at it before it ships:

- `successfactors._titled_fields` / `_page_fields` — pure functions over a page string. A saved
  not-found shell and a titled page are enough to pin Option A's classifier without a network
  call. **The capture referenced in this document's header is untracked** — `experiment/` is
  gitignored (`.gitignore:34`), which is where CLAUDE.md puts R&D captures and where
  `run_logs.py` already caches — so whoever implements A must commit the two pages as real
  fixtures under `tests/` in that PR rather than reaching for the paths named here. Anything
  cheap to re-fetch (`curl https://careers.hcltech.com/job/Totally-Fake-Role/999999-en_US/`)
  should be re-fetched rather than trusted from a stale capture.
- `index_plan.plan_sync` — already pure and already unit-tested on CI's base-deps install
  (`test_a_persistently_truncated_board_drains_instead_of_ratcheting` is the ADR-0055 analogue). Any
  drain or bound from Options C/D belongs there and is testable there.

So the absence is specific: the *trend* cannot be locked down by a test, but every mechanism that
produces it can be.
