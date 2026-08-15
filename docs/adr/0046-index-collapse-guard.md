# ADR-0046: A collapse guard on index eviction

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0014 · **Amended by:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) — the deferred fix landed: a Board whose
scrape errored now leaves the eviction scope outright, and this ratio guard becomes the
backstop for truncations that report no error at all · **Amended by:**
[ADR-0055](0055-bound-the-collapse-guards-hold.md) — refusing a tripped Board's evictions made
the guard a ratchet no other path could reach, so the hold is now capped at this ADR's own
ratio and the remainder drains over later runs

## Context

The served index flaps. Whole Eightfold Boards are evicted on one run and re-added on the next,
forever, so NVIDIA, Qualcomm, Micron and Citi jobs disappear from search for roughly two hours at a
stretch and then return. Across five consecutive runs (2026-08-12/13) the table oscillates between
280,595 and 283,185 rows and never converges.

The eviction scope is defined in `index.py`'s `_scraped_boards` as the Boards that emitted at least
one job line this run, and `plan_sync` then deletes every indexed id in that scope missing from the
fresh output. That rule is all-or-nothing at the *line* level: a Board that returns **one page of a
paginated Board** is fully in scope, and every row the scrape failed to reach is read as a closed
posting.

Note what the scope is *not*. It is not a "Board returned nothing is safe" rule: `_scraped_boards`
reads the **full** scrape (`data/jobs/*.jsonl`) while `fresh` is the embedded **tech** id set, so a
Board that emitted plenty of jobs of which none are tech is in scope with *zero* fresh ids — which
ADR-0014 relies on, since that is how a Board that stopped hiring engineers drops its stale tech
rows. Only a Board emitting zero job lines of any kind stays out of scope, and that case is the
ADR-0023 prune sweep's.

Eightfold's per-origin rate limit is what makes the scrape come back short, and it was reproduced
directly. Holding one board at the pipeline's own fan-out width for 1,500 detail fetches from a
GitHub Actions runner:

```
width  100: 1179/1500 missing (78.6%) | settled={200: 321, 429: 204, 405: 975}
```

against 75.8% missing in production. After roughly 320 successes the edge returns 429 and then
405 — and 405 is not in `http._TRANSIENT`, so it is never retried, settling instantly as a non-200.
A follow-up arm showed the block applies across *all* of `*.eightfold.ai`, not per tenant, which is
why five Boards flap together in the same run. `scrapers/eightfold.py:130-139` breaks out of
pagination on any non-200 and returns what it has, even though page 1 carried `data.count` and the
truncation was therefore exactly detectable; the surviving position counts in the logs are clean
multiples of the page size (1380, 2580, 1480, 2080) whenever a Board flapped, and arbitrary
(2609, 1905, 1914, 1230) whenever it did not.

No Board-outcome signal reaches sync at all: `harvest.scrape_all` records failures in
`RunResult.errors`, but `pipeline.yml` ships only `data/jobs` + `data/state` to the merge job.

## Decision

`plan_sync` withholds a Board's evictions entirely when that Board would lose more than
**`COLLAPSE_RATIO` = 25%** of its indexed rows in a single run, and reports the held Boards in
`SyncPlan.held` so `index.py` can name each one at WARNING (an `::warning::` annotation under
Actions) and carry the totals into the run summary. Boards holding fewer than
**`COLLAPSE_FLOOR` = 20** rows are exempt, because there a large ratio is a handful of rows.

The threshold is calibrated, not chosen for roundness. Per-Board eviction ratios over the five runs,
against each Board's `last_tech_jobs` in the priority ledger, for Boards of at least 20 rows:

| ratio | board-runs |
|---|---|
| 0–10% | 754 |
| 10–25% | 34 |
| 25–50% | 21 |
| 50–75% | 5 |
| 75–90% | 3 |
| 90–100% | 0 |

Ordinary turnover is under 10% in 754 of 817 board-runs (92%). The 29 board-runs above the
threshold — the population this guard now holds, ~6 per run — are 3.5% of the total, and all but
five of them are Eightfold. Every Board observed flapping sits between 35% and 82%. The line at 25%
separates the two populations with room on both sides.

One caveat on that calibration: the table's denominator is `last_tech_jobs` from the priority
ledger, while the code divides by the Board's current *indexed* row count. The two track each other
on a healthy Board, which is the case the threshold was fitted to, so the calibration governs the
decision to *start* holding a Board. They stop tracking once a Board is held — its indexed count is
frozen high by the guard itself while the ledger follows the truncated scrape down — so the ratio
computed on a Board already being held is not the quantity the table measured, and no claim is made
here about how it moves run over run. Per-Board outcomes remove the question entirely.

## Consequences

The user-visible symptom stops immediately, with no new artifact plumbing and no scraper change: a
truncated Board now loses nothing rather than most of itself.

**This treats the blast radius, not the cause.** The scrape is still being rate-limited, still
discarding a known `count`, and still dropping 75.8% of Eightfold descriptions — which is a
separate and arguably worse defect, since those jobs are embedded from their title alone and are
served with an empty description. The guard does nothing for any of that.

The guard is deliberately blunt, and the cost is real: a Board that genuinely sheds more than a
quarter of its postings in one run trips the guard on every subsequent run too, because the ratio
does not improve on its own, so those rows persist until the Board falls below the threshold or
leaves the ledger and the ADR-0023 prune sweep reaches it. A capped drain (evict at most 25% per
run) was considered and rejected: it bounds staleness but leaves the flap running at reduced
amplitude, and stopping the user-visible symptom outright is the whole point of the change.

**This knowingly weakens one ADR-0014 property.** A live Board that stops hiring engineers
altogether presents as 100% missing, which is indistinguishable here from a scrape truncated to
nothing, so above the floor its stale tech rows are now held rather than dropped — where ADR-0014
had them "fall out for free". Boards under `COLLAPSE_FLOOR` still drain, which covers the common
small-Board case, and the WARNING names every held Board so the condition is visible rather than
silent. This is the price of a guard that cannot tell a truncated scrape from a real collapse, and
it is precisely what per-Board outcomes remove.

The honest fix is for the scrape to report per-Board outcomes and for the sync to scope on
`status == ok` instead of on "emitted a line", which makes the guard redundant. **Revisit when
per-Board outcomes reach the merge job** — at that point this becomes a backstop rather than the
mechanism.
