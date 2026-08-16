# Eightfold PCSX: replica-unstable pagination, and how it flapped the index

**Date:** 2026-08-16 · **Issue:** [#142](https://github.com/sarthakjain004/headstart/issues/142) ·
**Fix:** dedupe-by-id + bounded re-sweeps in `EightfoldScraper._api_search`
· **Harness:** `scripts/eval/flap_audit.py`

## The finding

Eightfold's `/api/pcsx/search` paginates with `start=` offsets, 10 positions per page, and reports
an exact board total in `data.count`. The listing behind those offsets, however, is **not a stable
sequence**. The default ordering sorts by `postedTs` — which has *day* resolution, so on a large
board hundreds of postings tie — and the tie-break order differs between the load-balanced
replicas answering the requests. Measured directly on `ngc.eightfold.ai` (3,685 postings): the
same offset fetched twice, seconds apart, returned 8/10 different ids on one probe and 18/20 on
another, while other offsets matched exactly. Which offsets disagree changes from minute to
minute. No sort parameter makes the order deterministic: `sort_by=timestamp` is the (tied)
default, and `sort_by=relevance` merely picks a different unstable order.

A sequential offset-crawl therefore samples a *mixture* of orderings. A posting that sits at
offset 120 on replica A and offset 3,140 on replica B can be returned **twice** (both offsets
fetched from the "wrong" replica) — and its mirror image is a posting returned **never**. One
full crawl of `ngc.eightfold.ai` fetched 3,685 rows of which only **3,460 were unique**: 225
duplicate rows, therefore 225 distinct postings silently missed — **6.1% of the board, a
different 6.1% each crawl**.

## How a 6% sampling artifact became a 64% eviction error

The scraper's completeness check was `while len(positions) < total` — raw rows against
`data.count`. Duplicates count toward the total, so a crawl carrying 225 dupes *believes itself
complete*: it never calls `mark_truncated`, so the ADR-0053 outcome report says the board is
authoritative, so `index sync` keeps it in the eviction scope. The 225 missed postings are then
indistinguishable from delistings and are evicted. The miss is ~6%, far below the 25% collapse
guard (ADR-0046), so no backstop trips. Next run misses a *different* 6%, so last run's evictions
reappear as adds — each one freshly stamped `first_seen`, i.e. falsely "newly seen".

Audited over 8 production runs (2026-08-15/16, `flap_audit.py`):

- 64% of all evicted ids were re-added within the window; 32% of all adds were already known.
- 96% of flapped rows were eightfold; **93% of every eightfold eviction was spurious**.
  Workday's 475 evictions flapped 0% — that is what real churn looks like.
- Every flapped id left via sync's evict path; prune and the liveness ledger were exonerated.

Downstream, the flap corrupted every delta signal (role-trends read each flap as a closure plus a
new posting — it materially confused the software-engineering-decline investigation), churned
LanceDB versions, and re-stamped `first_seen` on rows that had never left the market.

## The fix

`_api_search` now keys positions by id as pages arrive and judges completeness on **distinct**
postings. A sweep that ends short of `data.count` is re-crawled from offset 0 — the point being
that a differently-ordered replica deals the missed postings to offsets we will actually fetch —
up to 3 sweeps, stopping early when complete or when a sweep finds nothing new. Only a list still
short after that is marked truncated, which keeps the board out of the eviction scope for that
run (ADR-0053) instead of letting the gap read as delistings. Extra sweeps cost only list pages
(~370 cheap GETs on the largest boards), never detail fetches.

## The system-design lessons

**1. Offset pagination is only as good as its sort key.** `start=N` is a promise that the list is
the same list between requests. That requires a *total* order — a sort key unique per item, or a
unique tie-breaker (id) appended to it. `postedTs` at day resolution over thousands of rows is a
*partial* order, and everything below the tie is left to whatever the serving node's index
happens to emit. Any horizontally-scaled backend (search clusters, replicated DBs) will break
those ties differently per replica unless the query pins the order. This is why cursor/keyset
pagination (`created_at + id > last_seen`) exists: the cursor carries the total order with it.
When you consume someone else's API you don't get to choose — but you *can* detect the symptom,
because it has a signature: **duplicates across pages**. A dupe across pages proves the ordering
moved under you, and every dupe implies (count staying fixed) a corresponding miss.

**2. Completeness must be measured in the unit you care about.** The crawl counted *rows
fetched*; the system cares about *distinct postings*. Any accumulator that can double-count will
eventually launder a gap into a "complete" result. The one-line invariant that would have caught
this on day one: `len(unique_ids) == data.count` — the server was even kind enough to hand over
the exact expected cardinality, and the old check compared the wrong quantity against it.

**3. Layered guards each had a hole, and the failure threaded all of them.** This pipeline
already had *two* defenses against exactly this class of bug: per-board outcome reporting
(ADR-0053, catches a scraper that *knows* it came up short) and the collapse guard (ADR-0046,
catches a board losing >25% at once). The failure mode that survived was the intersection: a
scraper that *didn't know* it was short, losing *under* 25%. Defense-in-depth reasoning has to
enumerate what each layer cannot see, not just count layers — the gap is always the conjunction
of the blind spots.

**4. Reconciliation loops amplify input noise into state churn.** `sync` is a
desired-state-vs-actual-state reconciler: it trusts its input as truth and diffs. Feed a
reconciler an unstable observation and it doesn't average the noise away — it *executes* it, in
both directions, every cycle (54–73% of evictions re-added within hours). Anything that
reconciles against a sampled view of the world needs either a stable sample or damping
(hysteresis, two-strike eviction, quorum-of-N-observations). We fixed the sample; damping remains
available if another provider turns out unstable (successfactors flaps at 21% and is worth a
look — tracked in #142).

**5. Derived signals inherit upstream lies transitively.** `first_seen` feeds `seen_within`
filters and alert watermarks; role-trends feed product decisions. None of those layers had any
way to know an "add" was a re-add. When a boundary observation is wrong, everything downstream is
wrong *in a way that looks internally consistent* — the trends math reconciled perfectly with the
index while both described a market that didn't exist. Data-quality invariants earn the most at
the boundary where observations enter the system.

**6. Instrument deltas, not just totals.** The table's row count barely moved (~+570/run) while
1,300+ rows churned beneath it. The flap was invisible until the merge stage logged the *ids*
behind each add/evict (`_log_ids`, added during the earlier churn investigation) — which is what
made `flap_audit.py` possible at all, purely from logs, with no table time-travel. Cheap,
greppable id-level deltas at every mutation point are what turn "the number looks fine" into
"54% of these are the same ids oscillating".

## Reproduction artifacts

- `experiment/index-flapping/probe_pcsx_stability.py` — two-pass crawl diff (old-code behaviour:
  3,685 rows / 3,460 unique).
- Same-offset double-fetch probes: inline, recorded in the #142 fix PR.
- `scripts/eval/flap_audit.py --runs 8` — the goal metric: already-known adds must sit **< 10%**
  overall (was 32% at diagnosis; the eightfold share of flapped rows was 96%).
- Regression tests: `tests/test_scrapers.py::test_eightfold_resweeps_an_unstable_list_to_completeness`,
  `::test_eightfold_marks_a_persistently_short_list_truncated`.
