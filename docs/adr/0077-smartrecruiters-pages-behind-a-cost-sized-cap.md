# ADR-0077: SmartRecruiters pages behind a cost-sized cap

**Status:** accepted · **Date:** 2026-08-20 · **Resolves:**
[ADR-0070](0070-smartrecruiters-does-not-cap-a-board-at-100-postings.md) (which marked the
truncation and deliberately left the cost half open) · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md),
[ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md),
[ADR-0073](0073-narrow-six-retail-workday-boards-at-the-source.md)

## Context

ADR-0070 established that `?limit=100` is *our* page size, not the provider's ceiling, and made the
scraper mark itself truncated so unread postings stop reading as delistings. It explicitly did not
decide whether to paginate:

> Picking the cap *is* the cost decision, and a cap chosen to feel safe would set the corpus's
> SmartRecruiters share by accident. It is raised as its own issue with these numbers attached.

That is issue #202, whose recommendation was a low cap on the hypothesis that "if page 3+ of
`dominos` is overwhelmingly non-tech retail, a low cap costs almost no tech recall."

**Measured, that hypothesis is false**, and this ADR records the measurement that killed it as much
as the decision it produced.

### The tail is not junk

The first pass sampled the three boards #202 named — `dominos`, `crossmark1`,
`groupementmousquetaires` — at offsets 200–7,000 and found **0 tech in 500 postings**. That
reproduces, but it is a biased sample: those three were selected *because* they were flagged as
large retail boards, and they are not the population a cap truncates.

Re-sampled on the right population — 40 boards drawn at random from the 218 live boards over 500
postings, each probed at **offset 500, the first page a 5-page cap would drop**, classified with
`headstart.tech_filter.is_tech` (the pipeline's own gate):

> **457 tech / 3,233 postings = 14.1%.** Tail-weighted across those boards: **10.4%.**

Density does not fall off with depth. Every board over 3,000 postings (15 of them, after
case-deduping the ledger), probed at offset 1,000, at half the board, and 150 from the end:

| board | postings | @1,000 | @half | @end | pooled |
|---|---:|---:|---:|---:|---:|
| `EndeavorITSolution` | 8,478 | 56/100 | 56/100 | 75/100 | **62%** |
| `WebleeTechnologies` | 3,861 | 48/100 | 36/100 | 0/100 | **28%** |
| `prosidianconsulting` | 4,005 | 3/100 | 22/100 | 41/100 | **22%** |
| `SonsoftInc` | 6,519 | 5/100 | 11/100 | 32/100 | **16%** |
| `AECOM2` | 4,913 | 21/100 | 15/100 | 10/100 | **15%** |
| `BoschGroup` | 4,764 | 8/100 | 13/100 | 21/100 | **14%** |
| `JobsForHumanity` | 3,112 | 6/100 | 13/100 | 3/100 | 7% |
| `Dominos`, `AdeebaEServicesPvtLtd`, `CROSSMARK1`, `GroupementMousquetaires`, `GreeneKing`, `AccorHotel`, `Dev2`, `SGS` | 3.1k–24.5k | — | — | — | **0–1%** |

The giants split in two, and #202's sample happened to draw only from one side. The retail and
hospitality boards really are ~0% tech at every depth. The other half are **IT staffing and
engineering firms** — and on several of them density *rises* toward the end of the board, because
the newest postings sort first and the deep tail is backlog.

The classifier is not simply silent on retail: the control, `Zomato1` on the same code path,
returns `totalFound=3` and classifies 1/3 tech (`Software Engineer – Back End`).

### The cost is smaller than assumed

ADR-0070 priced full pagination as "24,556 detail fetches for one board," a request count. Measured
in the currency that actually binds — shard wall-clock — the detail pass is HTTP/2 multiplexed
(ADR-0015/0016) and runs far faster than that count suggests:

* `AccorCorpo`, 290 postings, detail pass on: **10.6 s — 27.4 postings/s.**
* `EndeavorITSolution`, 5,000 postings read under this ADR's cap, detail pass on: **166 s — 30.2
  postings/s.**

So 5,000 postings is **~2.8 minutes**, against ADR-0064's **15-minute** floor below which "a Board
cannot threaten a 60 min makespan." Even at the slow end of fleet throughput ADR-0064 measured for
other ATSes (~6 jobs/s), 5,000 postings lands at ~14 minutes — still under the floor.

### ADR-0064's cost gate cannot substitute for a cap

Checked in `ingest/scrape_plan.py`. `_gated_boards` opens:

```python
row = cost_rows.get(cost_key)
if row is None or row.seconds <= _GATE_FLOOR_S:
    continue
```

An unmeasured board has no cost row and is **never gated** — deliberately, per that ADR's property
1 ("it judges a Board only on its own measurement"), so no board is dropped for its ATS's
reputation. The gate is therefore *reactive*: it can only drop a board after a run has measured it,
and ADR-0064's own worked example is a giant that burned ~52 minutes and was killed before it could
write the row that would have gated it. A cap is the *preventive* bound the gate cannot be. The two
compose: the cap bounds the first, uncosted run; the gate then drops boards whose measured hour
buys too little tech — which is exactly the ~0%-tech retail half of the table above.

## Decision

**The SmartRecruiters scraper pages the listing by `offset` behind `_MAX_PAGES = 50`** — 5,000
postings per board — mirroring the `_MAX_PAGES` shape `scrapers/darwinbox.py` already uses, with
`_PAGE_SIZE = 100` unchanged.

**The cap is sized by cost, not by a claim about what the tail contains** — because the measurement
above withdraws any basis for the latter. 5,000 is the most this scraper can read while staying
under ADR-0064's 15-minute gate floor at the slow end of fleet throughput; at measured
SmartRecruiters throughput it is ~2.8 minutes, five times under.

What that buys, against the committed liveness ledger (`jobs` there is `totalFound` — see
`check_liveness.p_smartrecruiters` — so it is the board's true size):

| | one page (today) | cap 50 | uncapped |
|---|---:|---:|---:|
| postings read on the 807 over-page boards | 80,700 | **453,198** | 559,230 |
| boards read whole | 0/807 | **796/807** | 807/807 |

The single board that most decides this, measured end to end: `EndeavorITSolution` yields **2,762
tech postings at this cap against 326 at a 5-page cap** — an 8.5× recall difference on one board,
for 2.8 minutes.

Truncation marking stays as ADR-0070 left it: one `totalFound > len(postings)` check after the
loop, covering both ways the read falls short, because `totalFound` is exact rather than a
full-page guess. The reason names the cap only when the cap actually stopped the loop — that needs
both `page == _MAX_PAGES` *and* a full final page, since `totalFound` is read off page 1 and a
board that loses a posting mid-read otherwise gets a reason blaming a cap that never fired.

Rejected: **`_MAX_PAGES = 5`**, #202's own recommendation. Its stated justification — that the tail
costs almost no tech recall — is contradicted by the 14.1% measurement, and it would discard ~2,400
tech postings on `EndeavorITSolution` alone to save 2.5 minutes.

Rejected: **paginate fully.** It buys 106,032 further postings concentrated in four ~0–1%-tech
mega-boards, and hands them an ungated first run at 15+ minutes each — the exposure a cap exists to
prevent.

Rejected: **leave it at one page** (#202 option 1). Concedes 796 boards that a cap reads whole.

Deferred: **source-side narrowing**, the ADR-0073 move. If the posting API exposes a department or
category filter, the ~0%-tech giants are better solved by never fetching 24,000 retail postings
than by capping the read. Per-ATS work, carries ADR-0017's recall caveat, needs its own live probe.

## Consequences

SmartRecruiters coverage on over-page boards rises 5.6× (80,700 → 453,198 postings read), and 796
boards leave the truncated set entirely — read whole, so they return to the ADR-0053 eviction scope
and their stale rows get pruned again. That shrinks the "population only grows" cost ADR-0070
flagged as unbounded, rather than adding to it.

Worst-case per-board detail cost rises from 100 fetches to 5,000 — ~2.8 minutes measured, ~14
minutes at pessimistic throughput. This is a real increase in scrape time and in what reaches the
embedding stage, and CLAUDE.md names storage as this workflow's binding constraint: only the tech
subset is embedded, but at ~10–14% tail density the added tech volume is the point, not a side
effect. **If a run's wall-clock or LFS growth moves unacceptably, `_MAX_PAGES` is the one constant
to turn, and this ADR's table is the curve to turn it against.**

Eleven boards stay above the cap and stay truncated, so they remain outside the eviction scope.
Four are ~0–1% tech and ADR-0064's gate is expected to drop them once measured; two
(`EndeavorITSolution` at 62%, `SonsoftInc` at 16%) are tech-dense and stay partially unread — the
known, priced-in gap this cap accepts.

Noted, not fixed: **the liveness ledger holds case-duplicate slugs** — `dominos`/`Dominos`,
`crossmark1`/`CROSSMARK1`, `aecom2`/`AECOM2` and at least eight more pairs among boards over 3,000
postings alone. The 807/796 counts above are un-deduped and therefore overstate board *counts*
(though not the per-board cap decision, which is per slug). Pre-existing, out of scope here, worth
its own issue.
