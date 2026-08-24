# `workday:pwc/nonpublic_postings` — why the worst-flapping board flaps

**Date:** 2026-08-24 · **Follow-up to:** `2026-08-24_ten-run-eviction-flap-review.md` §2, where
this board accounted for **68% of all flapped rows** (243 of 357) across the 10-run window ·
**Method:** the scrape-shard logs for all 10 runs (not the merge logs — mid-crawl failures and
spare-egress activity are only recorded shard-side), read through the step-0 ANSI filter

## Summary — the hypothesis was wrong, and backwards

The natural hypothesis was **spare egress**: this board fails mid-crawl constantly, spare egress
is the mechanism that handles throttling, so maybe spare egress is broken or making things worse.

**Measured across all 10 runs, the correlation runs the opposite way.** Spare egress isn't the
cause — it's the *cure*, and the catastrophic run is precisely the one where it hadn't engaged yet.

| run | pages failed | spare egress at crawl time | rotations in shard |
|---|---:|---|---:|
| 32719831948 | **108 / 219** | **not yet engaged** — board hit on the raw runner IP | 54 |
| 32671773723 | 1 / 218 | not yet engaged | 66 |
| 32711292938 | 1 / 219 | already engaged | 56 |
| 32674630735 | **0** / 219 | already engaged | 89 |
| 32682029119 | **0** / 219 | already engaged | 91 |
| 32686765048 | **0** / 219 | already engaged | 94 |
| 32692683330 | **0** / 219 | already engaged | 79 |
| 32695790165 | **0** / 219 | already engaged | 43 |
| 32702364262 | **0** / 219 | already engaged | 97 |
| 32715491955 | **0** / 219 | already engaged | 78 |

**8 of 8 runs where spare egress was already active when the board was crawled: zero page
failures.** Both runs with failures are the two where it wasn't yet active. And the catastrophic
run had among the *fewest* rotations (54), not the most — so "egress thrashing" doesn't explain
it either.

## The mechanism, isolated to the second

The board is crawled very early in its shard, and the failure is a race against spare egress
engaging:

```text
run 32719831948  (108/219 pages failed)
  11:40:10.73   shard starts — harvest: 1267 boards
  11:40:25.27   pwc/nonpublic_postings: 108 of 219 pages failed   <- 14.5s into the shard
  11:40:25.79   workday: origin returned 429 — spending spare egress   <- 0.5s LATER

run 32711292938  (1/219 pages failed)
  09:26:19.61   workday: origin returned 429 — spending spare egress   <- 18.5s into the shard
  09:26:25.83   pwc/nonpublic_postings: 1 of 219 pages failed      <- 24.7s, egress already up
```

Verified directly: **zero** spare-egress log lines exist before `11:40:25.3` in the catastrophic
shard. The board was hit cold, on the raw GitHub Actions runner IP, and Workday's origin 429'd
it — and it was the pwc board's own 429s that then *triggered* spare egress for the rest of that
shard.

The 0.5-second gap is the whole story: on the bad run the board lost the race by half a second;
on every run it won, it lost 0 pages.

## Why this board specifically, and not the other 222 workday boards in the shard

Three properties combine, and it's the combination that matters:

1. **It's huge.** 4,356–4,371 listed postings — ~219 listing pages.
2. **`_paginate_async` fans out all 219 pages at once** (`workday.py:544-551`), by design: async
   fan-out is the documented behaviour (ADR-0016) and is fine for a normal board. On a 219-page
   board it's a 219-request burst at a single origin in one moment.
3. **It's crawled ~15 seconds into the shard**, before the shard has generated enough traffic
   for anything to have tripped the 429 that turns spare egress on.

So the very first substantial thing this shard does to Workday's origin is a 219-request
simultaneous burst from an unprotected datacenter IP. Sometimes it survives; on
`32719831948` it didn't, and half the pages came back 429.

Note the shard that failed was also the *smallest* of the window (1,267 boards vs ~1,340
elsewhere) — fewer boards ahead of pwc in the queue means less warm-up traffic, so less chance
that something else trips the 429 first and gets egress up before pwc's burst. That is
consistent with the race explanation, though with n=1 it's suggestive rather than proven.

## How a crawl failure becomes a flap

The failure itself is handled correctly — `workday.py:570` marks the Board unauthoritative, and
ADR-0053 duly excludes it from `index sync`'s eviction scope that run. The flapping comes from
the runs in between:

- On a run where the crawl **fails badly**, the board is excluded from eviction — no harm.
- On a run where it **succeeds**, the full 4,371 postings are seen — no harm.
- The damage is done on runs where the crawl comes back **partially short but not short enough
  to trip either guard** (ADR-0053's own report, or ADR-0046's collapse ratio). Postings on the
  unread pages look delisted, get evicted, and reappear as "new" one or two runs later when
  those pages are read again.

That's why the merge-log picture in the parent review showed both guards firing on some runs
(`withheld 139 evictions`, `withheld 112 evictions`) and *no* guard firing on others that still
churned hundreds of rows.

**Real data loss in the window was near-zero** — only 1 of this board's ids was net-evicted and
never re-added. The cost is churn: wasted index writes, and `first_seen` re-stamped on any
posting caught in the cycle, which corrupts "posted recently" filters and trend counts for those
rows (the same downstream damage `pcsx-replica-instability.md` documents for eightfold).

## What would fix it

Ordered by how directly each addresses the measured cause. None of these are implemented here —
this document is the diagnosis.

1. **Cap the pagination fan-out width for very large boards.** The cause is a 219-request
   simultaneous burst at one origin. A per-origin concurrency cap on the listing pagination —
   the detail pass already has one — would remove the burst without changing anything else. Most
   targeted fix.
2. **Warm up spare egress before the first large-board crawl, or engage it pre-emptively for
   boards over some page threshold.** The data says a protected crawl of this board fails 0/219
   in 8 of 8 runs; the whole problem is that protection arrives 0.5s too late. Note this trades
   against ADR-0047's deliberate reluctance to dial spare egress unnecessarily, so it needs its
   own cost measurement.
3. **Order the shard so the largest boards aren't crawled first**, giving ordinary traffic a
   chance to trip the 429 and raise egress before the expensive board runs. Cheapest to try,
   weakest guarantee — it makes the race less likely to be lost, without removing the race.
4. **Tighten the guard gap** so a partially-short crawl can't quietly evict. This treats the
   flap symptom rather than the crawl failure, but it's the same bounded/proportional
   scope-exclusion idea already argued for in
   `docs/eightfold/no-client-side-fix-for-replica-instability.md` — and it would cover every
   board with this shape, not just this one.

## Caveats

- **n=10 runs, one board.** The 8-of-8 "protected crawls fail 0 pages" result is a strong
  signal, but it's one board over ~13 hours. The three PwC tenants in the parent review's
  worst-flapping list (`nonpublic_postings`, `crm_experienced_careers_site`,
  `Global_Experienced_Careers`) suggest a possible tenant-wide rate-limiting posture at PwC
  rather than a purely size-driven effect — untested here.
- **The catastrophic run is a single observation.** The mechanism (crawl beats egress by 0.5s)
  is directly evidenced in the logs, but only one run in this window shows the severe form.
- **Not tested:** whether re-running the same board with an artificially delayed egress
  reproduces the failure on demand. That would upgrade this from a well-evidenced correlation
  with a clear mechanism to a proven cause, and is the obvious next step before shipping any fix.

## Reproduction

```bash
# The catastrophic run's shard log — note zero spare-egress lines before the pwc failure
gh api "repos/sarthakjain004/headstart/actions/jobs/97417700912/logs" \
  | grep -vF $'\033[36;1m' \
  | grep -E "harvest:|pwc/nonpublic_postings|origin returned 429 — spending"

# The contrast run, where egress engaged first and only 1 page failed
gh api "repos/sarthakjain004/headstart/actions/jobs/97383212754/logs" \
  | grep -vF $'\033[36;1m' \
  | grep -E "pwc/nonpublic_postings|origin returned 429 — spending"
```

The per-run table at the top was produced by walking every shard of all 10 runs to find the one
carrying this board (it moves between shards run to run, since the planner re-packs each time),
then extracting the failure count, the egress-engagement timestamp, and the rotation count from
each.
