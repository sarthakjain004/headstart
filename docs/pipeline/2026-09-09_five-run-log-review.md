# Five-run pipeline log review — 2026-09-09

Runs analysed (the five most recent finished `pipeline.yml` runs as of 2026-09-09 10:00 UTC), plus
the one cancelled scheduled run that sits inside the window:

| run | created (UTC) | event | result | wall | note |
| --- | --- | --- | --- | --- | --- |
| 34312743097 | 09-09 04:53 | dispatch | success | 61.9 | full run |
| 34313429608 | 09-09 05:04 | schedule | cancelled | — | no job ever started (concurrency, see §9) |
| 34316866965 | 09-09 05:55 | dispatch | success | 56.7 | full run |
| 34321068300 | 09-09 06:52 | dispatch | success | 64.9 | full run |
| 34326521465 | 09-09 07:57 | dispatch | success | 1.0 | **stand-down**, `cleanup-index` active |
| 34327339789 | 09-09 08:06 | dispatch | success | 59.1 | full run |

All six sit on the **same SHA, `fd15455`**, so there is no code confound anywhere in this window and
every cross-run comparison below is like-for-like. `fd15455` includes #385 and #387 (the observability
work), so every new diagnostic line this review leans on genuinely exists in all of these logs.

Median wall across the four full runs is **60.5 min**. Nothing failed: every run finished `success`,
the board-error rate is 0.1–0.2%, no shard hit its time budget, no embed Doc failed, and `index prune`
found essentially nothing to prune. The findings below are therefore about **cost and latent
correctness**, not breakage.

## 1. Critical path: the serial tail now costs more than the fan-out

| run | scrape-plan | scrape (max) | join | embed | merge | wall | owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 34312743097 | 1.0 | 25.6 | 14.0 | 10.2 | 10.7 | 61.9 | scrape (41%) |
| 34316866965 | 1.5 | 25.4 | 15.2 | 6.2 | 8.0 | 56.7 | scrape (45%) |
| 34321068300 | 1.2 | 28.6 | 15.0 | 11.8 | 7.9 | 64.9 | scrape (44%) |
| 34327339789 | 1.3 | 20.6 | 13.8 | 13.5 | 9.4 | 59.1 | scrape (35%) |

`scrape` still owns the single largest stage in 4 of 4. But it is the only stage that fans out, and
the three single-job stages after it — `join` + `embed` + `merge` — sum to **29.4–36.7 min, a median
58% of wall clock**, all strictly serial. Any further work on the packer is bounded by the smaller
half of the run.

## 2. `scrape` is floor-bound on one board in 4 of 4 runs

`successfactors:careers.hcltech.com` is the slowest board of the slowest shard in every run, at
**94–95% of that shard's wall**:

| run | scrape max | hcltech floor | floor% | next-slowest shard | saving if removed |
| --- | --- | --- | --- | --- | --- |
| 34312743097 | 1536 s | 1437 s | 94% | 1134 s | 6.7 min |
| 34316866965 | 1525 s | 1432 s | 94% | 1210 s | 5.3 min |
| 34321068300 | 1717 s | 1614 s | 94% | 1262 s | 7.6 min |
| 34327339789 | 1234 s | 1169 s | 95% | 1193 s | 0.7 min |

The packer is not at fault and cannot help. `scrape_plan` reports a predicted spread of **1.02x mean**
in all four runs, and it names the floor in advance every time (`one board costs 23.1–25.3 min, above
the 13.5–14.4 min even share`). Actual/predicted per shard has a median of 0.84–1.01 — the cost model
is sound. The board is also genuinely worth scraping: 24.9 min for 10,280 postings / 2,724 tech jobs
is ~109 tech/min, so the ADR-0064 value gate correctly leaves it alone.

The only levers are intra-board parallelism, or scraping it on alternate runs. Note the last row: the
saving is a projection from re-ranking observed shard walls, and it collapses to 0.7 min in one of the
four runs when another shard runs long anyway.

**I checked whether re-tuning the value gate helps and it does not.** Lowering the ADR-0064 threshold
from ≥15 min to ≥5 min (holding the <2 tech-jobs/min rate) moves the gated set from 73 to 86 boards
and 1,619 to 1,718 board-minutes — 99 extra board-minutes against a Σ of ~7,200 board-minutes per run,
i.e. **1.4%**, at the cost of 43 more tech jobs. Not worth it. Computed from `data/state/board_cost.csv`
joined to `board_priority.csv`, both pulled fresh from HF on 2026-09-09.

## 3. `embed` spends 6–13.5 min of critical path on 257–450 Docs, always on one shard

This is the clearest cheap win in the window.

| run | new Docs | shards | predicted | actual | a/p | device |
| --- | --- | --- | --- | --- | --- | --- |
| 34312743097 | 257 | 1 | 13.2 min | 10.2 min | 0.78 | cpu |
| 34316866965 | 287 | 1 | 11.9 min | 6.2 min | 0.52 | cpu |
| 34321068300 | 369 | 1 | 14.6 min | 11.8 min | 0.81 | cpu |
| 34327339789 | 450 | 1 | 19.1 min | 13.5 min | 0.71 | cpu |

`embed_plan` picks the shard count as `ceil(total_cost / _TARGET_SECONDS)` with
`_TARGET_SECONDS = 20*60` (`embed_plan.py:63`, via `binpack.shard_count`). Steady-state cost is
714–1,146 s, so the ceiling is **always exactly 1**, and the matrix collapses to a single lane while
14 are available.

Model load is not the cost: `loading nomic-ai/nomic-embed-text-v1.5 on cpu` to the first bucket takes
**4 s** (08:45:19 → 08:45:23 in run 34327339789). The remaining 10.8 min is pure CPU encode at
~0.7 docs/s. So fan-out is nearly free here, and splitting 450 Docs across 3 lanes would cut the stage
to roughly 4 min plus the ~2.4 min fixed job setup that runs in parallel.

`_TARGET_SECONDS` is documented as "sized so a big backlog saturates the lanes" — correct for the
backlog era, wrong for a steady state that is now three orders of magnitude smaller. Dropping it to
~300 s would restore fan-out without changing behaviour under a large backlog.

Estimated saving: **6–9 min per run, ~10–15% of wall.** This is a projection from the measured
per-doc rate and the 4 s load cost, not a measurement of a run with more shards.

## 4. `pip install` runs three times serially, for ~6.8 min per run

The three post-scrape jobs each rebuild the environment from scratch:

| job | checkout → first stage | of which pip install |
| --- | --- | --- |
| join | 2.3 min | 1.3 min |
| embed | 2.4 min | 1.3 min |
| merge | 2.1 min | 1.2 min |

That is **6.8 min, ~11% of wall clock**, spent three times on the same `pip install -e ".[embed]"`.
An `actions/cache` step already exists but the install still costs 1.2–1.3 min each. A prebuilt
container image, or caching the built venv rather than the pip download cache, would recover most of
it. Artifact I/O adds another 3.2 min in `join` alone (the ~70 MB fragment upload).

## 5. 21,122 spurious `board_key() failed` INFO lines per run — noise, verified harmless

Both `scrape-plan` and `join` emit **10,561 lines each**, identical in all four runs:

```
[config] workday:sysco/syscocareers: board_key() failed (ValueError: Workday slug must be a
careers URL like https://{co}.wdN.myworkdayjobs.com/{site} — got 'sysco/syscocareers')
— falling back to the plain ats:slug
```

The count matches `data/state/board_cost.csv`'s Workday key count **exactly** (10,561). The cause is
double-canonicalisation: `board_key()` normalises a URL-form slug *down* to `{co}/{site}`, and these
stages then feed that already-canonical key back through `board_identity()`, which demands a URL and
raises.

**The fallback is nevertheless correct.** I round-tripped every Workday board through both paths —
`board_identity(CompanyRef('workday', canonical_key))` against the canonical key derived from the
ledger's URL form — over all 10,447 Workday Scrapable Boards: **0 diverge**. So no duplicate identity
is created and no data is affected. `merge` emits zero such lines, confirming the keep-set path is
clean.

Two things are still worth fixing. The volume buries real cases, which is the opposite of what #385
and #387 set out to achieve. And `config.py`'s own docstring is now factually wrong:

> Measured 2026-09-08: `load_active_companies(...)` yields 91,325 Scrapable Boards and reaches this
> path zero times, so the blast radius today is nil. Latent, not live.

It is live, 10,561 times per stage. The measurement was taken over the liveness ledger, which is the
one population that does *not* reach it; the ledgers in `data/state/` do.

## 6. ADR-0053 scope exclusion: the gate fires on a single unreadable page

2,881–3,055 eviction-candidate rows are withheld across 52–66 Boards each run, and **29 Boards were
excluded on all four**. The accretion CLAUDE.md warns about is now visible as a clean monotone trend
on the worst offender:

| board | 04:53 | 05:55 | 06:52 | 08:06 |
| --- | --- | --- | --- | --- |
| `successfactors:careers.hcltech.com` | 1518 | 1525 | 1530 | 1533 |
| `successfactors:careers.wipro.com` | 776 | 778 | 778 | 777 |
| `oracle:ejwl.fa.us2.oraclecloud.com` | 124 | 112 | 105 | 187 |
| `successfactors:basf.jobs` | 24 | 24 | 24 | 24 |

The gate has **no tolerance threshold**. These are all real exclusion reasons from the logs:

| board | reason | ratio |
| --- | --- | --- |
| `successfactors:careers.te.com` | 1/2135 job pages unreadable | 0.05% |
| `successfactors:careers.bureauveritas.com` | 1/2019 job pages unreadable | 0.05% |
| `successfactors:jobs.crh.com` | 1/1833 job pages unreadable | 0.05% |
| `successfactors:bertrandt.jobs.hr.cloud.sap` | 1/1412 job pages unreadable | 0.07% |
| `eightfold:appliedmaterials.eightfold.ai` | got 1931 of 1932 postings | 0.05% |
| `workday:cnx/external_global` | 1 of 79 pages failed (HTTP 500 x1) | 1.3% |

One unreadable detail page out of two thousand takes an entire board out of the eviction scope
permanently, because ADR-0053 has no drain. A tolerance — treat ≥99% read as authoritative — would
return most of these Boards to scope while still catching genuine truncation.

The Oracle entries are a different problem and need a different fix: `read 9926 of 13402 requisitions
— the API serves no offset past 10,000`. That is a real hard cap, not a flaky page, and no tolerance
threshold reaches it; it needs facet subdivision the way Workday's `jobFamilyGroup` works.

## 7. ADR-0083 grace period returns 1.6–3.5% of what it withholds

| run | unconfirmed this run | carried in | reappeared | reappear rate |
| --- | --- | --- | --- | --- |
| 34312743097 | 752 | 640 | 10 | 1.6% |
| 34316866965 | 773 | 752 | 26 | 3.5% |
| 34321068300 | 739 | 773 | 17 | 2.2% |
| 34327339789 | 823 | 739 | 27 | 3.7% |

The queue is stable (not growing), so the mechanism is not the ADR-0055 ratchet shape. But a healthy
grace period is supposed to return most of what it withholds, and this returns roughly one in forty.
Worth asking whether one run of delay is buying enough to justify itself — though note ~56% of each
cohort stays `unconfirmed again` simply because that Board was not in the next run's slice, so the
denominator is not clean and this is a "go look", not a verdict.

## 8. Egress: the spare is now the normal path, not a rescue

| run | shards triggering on workday | on eightfold | spare-egress connects |
| --- | --- | --- | --- |
| 34312743097 | 15/15 | 15/15 | 15 |
| 34316866965 | 15/15 | 14/15 | 15 |
| 34321068300 | 15/15 | 15/15 | 15 |
| 34327339789 | 15/15 | 14/15 | 15 |

Trigger statuses across the window: **workday 429 ×60, eightfold 405 ×54, eightfold 403 ×4**. Every
shard of every run exhausts its primary origin budget on both of the two largest ATSes and spends its
spare. There is no headroom left in the mechanism: if the spare egress is ever walled too, workday
(500k postings/run) and eightfold (45.7 s median board cost, the most expensive ATS) degrade
simultaneously. This is a standing condition, not an incident, and it is invisible in the run's
success status.

Retry volume is correspondingly large and very stable: **115k–123k retries per run** against 20,000
attempted boards, of which `403-wall` alone is ~22,200 every single run.

## 9. Errors: everything that actually failed

Board errors are 21–37 per run out of 20,000 attempted — **0.1–0.2%**, and 0 shards hit their time
budget. The classes, summed across all four runs:

| class | count | concentration |
| --- | --- | --- |
| HTTPError | 67 | workday 33; teamtailor ≥9, freshteam ≥7, greenhouse ≥5 (the emitter truncates each run's list with `+N more`, so the non-workday figures are lower bounds) |
| CertificateVerifyError | 24 | **successfactors 24 — every run, 5–8 each** |
| ConnectionError | 7 | workday 6, eightfold 1 |
| Timeout | 4 | successfactors 4 |
| JSONDecodeError | 1 | workday (`generalmotors`, the window's only traceback) |
| DNSError | 1 | successfactors |

The successfactors `CertificateVerifyError` is the only recurring, single-ATS, fully-deterministic
class — 24 occurrences with no run free of it. It is a fixable TLS-chain issue rather than host
flakiness, and it is worth a look because successfactors is also the ATS behind the §2 floor board
and most of §6's exclusions.

`N of M page(s) failed mid-crawl` fires **866 times** across the four runs — it is a Workday-only line (807 carry the `[workday]` tag; no other scraper emits it). These are counts of log
*lines*, not of individual pages — each line reports its own `xN` multiplicities — so read them as a
distribution over incidents. Two of the classes are parser-shaped rather than network-shaped:
**`no externalPath` (139 lines)** and **`unparseable` (41 lines)**. Neither is covered by any retry or
egress mechanism, and both would repay a direct look. The rest is HTTP 500 (381), ConnectionError
(201), HTTP 429 (48), HTTP 403 (31), and a long tail of 404/520/522/SSLError.

Only **60 of those 866 lines** end in a truncation verdict (`— Board unauthoritative this run` ×59,
`— too little of T listed read to keep` ×1) — but that is two different failure kinds sharing one log
shape, not a mis-calibrated threshold. Both scrapers follow the **same rule: `mark_truncated` iff the
returned id set is short**, and both are correct. Workday's **listing** pass (`workday.py:1060-1080`)
records every shortfall it sees — those are the 60. The other ~806 come from the **detail** pass
(`workday.py:947-955`), whose line says so in as many words: `— not a truncation (the listing pass
reports its own)`. Not marking is right there, because `ats_id = _posting_key(item)` is read from the
*listing* item and `detail = item.get("_detail") or {}`, so a failed detail yields an empty dict and the
Job is still emitted with null fields (ADR-0021; identity stopped depending on the detail at ADR-0097).
The id set is complete, so `index sync` has nothing to misread.

successfactors marks truncated under that same rule for the opposite reason
(`successfactors.py:315-320`): there **every** field comes from the job page, so `parse` drops a Job
whose page did not arrive, the returned list is genuinely short, and an unmarked short list is exactly
what `index sync` reads as a delisting — `docs/pipeline/2026-08-23_false-board-eviction-root-cause.md`
records the incident that guard exists to prevent. So the cost of the ~806 Workday detail losses is
**ADR-0021 null fields and ADR-0050 description-store gaps, not evictions** — a data-completeness
problem, not a scope-exclusion one. §6's case for giving the authoritative gate a tolerance stands on
its own; it is not a Workday comparison.

The one traceback in the window is `workday:https://generalmotors.wd5.myworkdayjobs.com/Careers_GM`
raising `JSONDecodeError` inside `workday.py:1046 _paginate` — the host returned non-JSON mid-crawl.
It is caught and logged, but it is classified as "unexpected", which suggests the scraper does not yet
treat a non-JSON pagination response as a known shape.

**Two things I expected to find and did not.** The 648-board quarantine reports `0 cleared` in every
run, which looks like a sink with no drain. I probed 24 quarantined boards live across eight ATSes:
**21 returned 404**, one DNS-failed, and the personio ones return the ATS's own "no personio board for
{slug}" 404 through its resolver (my probe hit a 429 on the wrong endpoint). `workday:comcast/Comcast_Careers`
is on an explicit **410 Gone**. The quarantine is correct and `0 cleared` is by design. Likewise the
state ledgers are healthy: only 6.8% of `board_priority.csv` rows have no live Board, and 2,039 of
those 2,224 are `join`, the deliberately-disabled ATS — so ~185 real orphans out of 32,911.

The cancelled run `34313429608` started no jobs at all: the 05:04 cron fired while the 04:53 dispatch
was still in flight and the concurrency group cancelled it. The 1-minute run `34326521465` logged
`cleanup-index is active (1) - standing this run down; the hand-off starts a successor` — ADR-0093
working as designed. Neither is a defect.

## 10. `role_trends.csv` is re-uploaded whole, 172 MB per run, to append ~470 KB

The file is now **172,537,804 bytes / 2,468,570 rows / 510 distinct timestamps**, and the merge job
uploads all of it every run (`role_trends.csv: 0%| | 0.00/172M` in the upload log) to append 6,767–6,777
rows. That is roughly a **366:1 write amplification**, on the workflow's own documented binding cost
constraint. At the current cadence it is ~1 GB/day of upload for ~3 MB/day of new data. An append-only
shard-per-day layout, or Parquet partitioned by date, would remove it.

Related: `usedStorage` sits at 65.6–68.6 GB against a `live` of 5.52–5.92 GB, and the dataset squashes
every run.

## 11. Smaller observations worth a look

**`role_trends` reassignments have been exactly 0 for four consecutive runs** — `0 of 295,090–295,423
rows changed family (0.00%), 0 transition rows`. `data/state/role_reassignments.csv` shows the last
non-zero entry at `2026-09-08T15:43:44Z`, ~13 h before the first run in this window, after being
active all that day. A hard zero four times running is either genuine convergence or a path that
stopped firing; the log cannot tell them apart.

**23.3% of served rows are non-tech** (89,680–89,779 of ~385,000), stable to three significant figures
across all four runs. That is ADR-0017's recall-biased filter creeping, and it is a user-visible search
quality number, not a pipeline one.

**The vector store is 1.8x the served table**: 696,064–697,418 vectors against 384,778–385,202 index
rows. `embed_merge` only drops vectors on upgrade (1–5 per run), so ~312,000 vectors belong to Jobs no
longer served. Whether that is deliberate (cheap re-add for a returning Job) or accretion is worth
deciding explicitly, since it sits inside the storage budget above. Of those, **77,671–77,678 were
embedded without a description** — title-only vectors, ~11% of the store, a direct search-quality
ceiling.

**`update_meta` refreshes all ~697,000 rows every run** on a no-sweep run (`derivations v8 stored, v8
in code — no sweep`) to change 1,490–3,857 facts. It is only 0.7 min today, so this is a note rather
than a finding.

**Description-store fill is ~92% eightfold in every run** (20,086–20,142 of ~21,800). That is expected —
the other ATSes' scrapes carry no descriptions — but the backlog it leaves is concentrated on
**vendor sandbox tenants**: `eightfold:amdocs-sandbox`, `eightfold:microsoft-tm2-dev-sandbox`,
`eightfold:citigroup-qa-sandbox`, `eightfold:nvidia-sandbox` occupy 4 of the top 10 backlog slots with
4,524 unsettled descriptions between them, and eightfold is the most expensive ATS per board. These
look like QA/test tenants rather than real employer boards and are candidates for `EXCLUDED_BOARDS`.

## Ranked scope for improvement

| # | change | est. saving / effect | confidence |
| --- | --- | --- | --- |
| 1 | Lower `embed_plan._TARGET_SECONDS` from 1200 s so the embed matrix fans out again (§3) | 6–9 min/run, 10–15% of wall | high — model load measured at 4 s |
| 2 | Give ADR-0053's authoritative gate a tolerance (≥99% read) (§6) | returns most of ~2,900 permanently-shielded rows | high |
| 3 | Cache the built venv / prebuilt image for join+embed+merge (§4) | up to ~5 min/run | medium |
| 4 | Stop re-uploading `role_trends.csv` whole (§10) | ~1 GB/day of the binding storage cost | high |
| 5 | Skip re-canonicalising already-canonical ledger keys (§5) | removes 21,122 noise lines/run | high — verified harmless, so noise only |
| 6 | Fix the successfactors `CertificateVerifyError` (§9) | 24 board failures across 4 runs | medium |
| 7 | Exclude the eightfold sandbox tenants (§11) | frees the most expensive ATS's budget | medium |
| 8 | Subdivide Oracle boards past the 10,000-offset cap (§6) | 4 boards permanently unauthoritative | medium |
| 9 | Investigate `no externalPath` ×139 / `unparseable` ×41 (§9) | parser-shaped page losses | low — cause not yet known |
| 10 | Confirm `role_trends` reassignments are converged, not broken (§11) | correctness of the trends chart | low |

Explicitly **not** worth doing, both checked and rejected against measurement: re-tuning the ADR-0064
value gate (§2, 1.4% of board-minutes), and adding a quarantine drain (§9, the quarantine is correct —
verified live against 24 boards).
