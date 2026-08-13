# ADR-0050: Persist descriptions across runs, and key the detail skip-list on holding them

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0048, ADR-0021

## Context

A `description` was read exactly once — at embed time — and then discarded. Nothing in the pipeline
kept it: it is not in the served schema, the tech filter reads `title` + `department`, and the feed
does not display it. So a Job whose detail fetch failed was embedded from its **title alone**, and
because `embed_plan` skips ids already in the store, that vector was never revisited. ADR-0047
measured the scale: Eightfold lost **161,649 of 213,199** descriptions across five runs (75.8%),
leaving roughly **16,771** Jobs in the index as title-only vectors. Users meet this as "search is
bad for NVIDIA and Qualcomm", never as a failure.

ADR-0048 then made the damage permanent by design rather than by accident. Its skip-list is built
from `meta.jsonl`, so it means *"we have embedded this Job"* — and a title-only Job **is** embedded.
It was therefore skipped on every subsequent scrape, so the description it was missing could never
arrive. ADR-0048 says as much: the repair "cannot simply be 'wait for a clean run'."

The skip-list's own documentation had already named the honest rule. CONTEXT.md's **Detail pass**
entry says *"A Job whose detail we already hold is skipped"* — but we held no detail. We held a
vector derived from one.

Two further facts shaped the design. Descriptions gzip to 17%, so a tech-wide store is ~174 MB —
affordable for one CI job, prohibitive to ship to fifteen scrape shards. And description absence is
**normal**, not an error: in a July snapshot of 75,166 tech Jobs, 14.4% had none at all (ripplehire
100%, zoho 53.8%), so "empty" cannot be treated as a failure signal on its own.

## Decision

**Persist every tech Job's description in a description store** at `data/descriptions/{ats}/`,
written and read on CI by `headstart.ingest.update_descriptions`, which runs in the join stage
directly after `filter_tech` and moves text in both directions: what this run fetched goes into the
store, and where the scrape left a Job empty the stored text is written back into
`data/jobs/tech/{ats}.jsonl`.

Repairing the corpus in place is what keeps this change small. `embed_plan`, `doc_prep` and
`experience.extract` are untouched — they go on reading the corpus, and the corpus is correct
again. It also repairs `scripts/enrich/experience_coverage.py`, which ADR-0048 left reading
`description: null` for every embedded Eightfold Job and reporting it as *no coverage* rather than
*not measured*.

**The skip-list is re-keyed onto the store**: a Job is skipped when we hold its detail, which is
what CONTEXT.md always claimed. `data/state/embedded_ids.txt.gz` becomes
`data/state/held_details.txt.gz`, and `update_descriptions` publishes it instead of `embed_merge`.
This is the change that makes the degraded population reachable at all — they hold no description,
so they leave the list and are fetched again.

**The store records two kinds of entry**, because "empty" has two causes that are otherwise
identical in the corpus:

| store entry | means | skip-list | on the next run |
|---|---|---|---|
| text | we hold the description | yes | nothing to do |
| `null` | the detail answered; this posting has none | yes | nothing to do |
| absent | never settled — the fetch failed or never ran | no | fetch it again |

The distinction rides in on `Job.detail_fetched`, set by the scraper, which is the only layer that
knows whether a fetch completed. Without it, a genuinely description-less posting is re-fetched
every run for the rest of its life.

**Only `eightfold` sets it today**, because only `eightfold` consults the skip-list — for every
other scraper the detail pass runs unconditionally, so the flag would change nothing and setting it
would be speculative. The obligation transfers with the skip-list: **a scraper that starts
consulting `have_details` must also set `detail_fetched`**, or its description-less postings are
retried forever. Zoho would be the worst of them at 53.8% description-less.

**`meta.jsonl` gains `has_description`** — whether the Doc we embedded actually carried one — and
`embed_plan` re-embeds a Job whose stored vector lacks a description once the corpus has one. The
stale row is evicted first via `evict_store.py --ids`, in the merge job **before `embed_merge`**.
Two constraints pin it there, and only that slot satisfies both. It must precede `index sync`,
because `plan_sync` computes `add = fresh - index` and an id still in the table is never re-added.
And it must precede `embed_merge`, because that appends without deduping by id: run afterwards, the
eviction would drop *every* row for the id — including the vector just merged — taking the Job out
of the store, out of the table, and out of `fresh`, so it would vanish from the index for a run and
re-embed from scratch on the next one.

**Writes are append-only.** Each run adds a small `{seq}.jsonl.gz` per ATS holding only what
changed (~430 KB); readers take base-then-fragments with last-write-wins, and `--compact` folds
them into `base.jsonl.gz`. Rewriting the store every run would mint ~174 MB of fresh blobs per run
— at ~10 runs/day that is the 100 GB quota in ~57 days, the exact mistake `data/lancedb` was moved
away from. Compaction rides `cleanup-index`, whose cron moves from every 2 days to **daily**;
history is squashed weekly, so the doubled rebuild churn stays ~5.3 GB/week.

### Rejected alternatives

- **Ship the store to scrape shards** — ADR-0047's literal wording ("new persisted storage plus a
  per-shard fetch"). ADR-0048 has since delivered the fetch-avoidance far more cheaply, and knowing
  *whether* we hold a description takes one bit, not 3.6 KB. ~1.3 GB of transport for nothing.
- **Store only detail-pass ATSes** — covers 100% of the risk at a tenth the size, since a
  listing-only ATS re-supplies its description every scrape. Rejected for uniformity: it needs a
  marker that, if forgotten on a new detail pass, silently withdraws the protection. Note the
  marker (`has_detail_pass`) turned out to be needed anyway, for the migration rule below — and it
  was indeed missed on two scrapers in first review, which is the failure this reasoning predicted.
  The consolation is that a missing marker now costs a Board's degraded vectors going unrepaired,
  not its descriptions going unstored.
- **Never embed a Job with no description** — no store, no trigger, no upgrade path, and a
  title-only vector becomes impossible. Rejected on coverage: 14.4% of tech Jobs would leave the
  index entirely.
- **Hash the embedded Doc instead of a boolean** — ADR-0021's recorded end-state, and it would
  catch organic edits too. Deferred for the reason ADR-0021 gave: the nightly edit churn has still
  never been measured against a CPU budget sized for new ids.
- **Grandfather today's embedded ids at cutover** — no day-0 re-fetch at all, but the ~16,771 stay
  broken pending a separate repair. Rejected in favour of self-healing (see below).

## Consequences

**Day 0 is expensive, deliberately.** The store starts empty, so the skip-list starts empty, so
Eightfold re-fetches every detail — the full pre-ADR-0048 load against the origin budget ADR-0047
identified as binding. Whatever succeeds also re-embeds. It converges geometrically over several
runs and repairs the ~16,771 without a separate exercise, but during that window new Jobs compete
with re-fetches for one origin budget, so the change makes title-only vectors *more* likely before
it makes them impossible. Authoritative absence speeds convergence: a posting with genuinely no
description leaves the retry set after one successful fetch.

**A pre-ADR-0050 `meta.jsonl` row carries no flag**, and is read as degraded only on an ATS with a
detail pass (`BaseScraper.has_detail_pass`). Reading absence as degraded everywhere would re-embed
~186k Docs — roughly 78 CPU-hours, ~8 runs of saturated embedding — to repair ~16,771. The
carve-out bounds it to ~22k, about one saturated run, at the cost of re-embedding the ~18% of
Eightfold rows that were already fine. The rule retires itself as rows turn over.

**ADR-0048's eviction trap is gone.** That ADR had to make `evict_store.py` rewrite the skip-list,
because an eviction otherwise made the next scrape skip exactly the descriptions it had just
discarded. With the list keyed on the store, dropping a vector no longer discards the text behind
it — the re-embed reads the stored description and needs no fetch at all. `evict_store.py` no
longer touches the list.

**`report_detail_gaps` now counts fetch failures, not absences.** `_description_of` returns `""`
for a 200 carrying no description and `None` only when the response could not be read, so the
`descriptions missing` line ADR-0047 monitors changes meaning — it is a cleaner signal, but not
comparable across this change.

**The store grows without a reaper.** Nothing removes a description when its Job leaves the index.
At ~430 KB/run of new text that is slow, and compaction only dedupes rather than prunes, but a
future cleanup should drop entries for ids no longer in any corpus.

**Not attempted here.** Organic-edit detection (ADR-0021's hash) — the store now makes it possible
to compare without a re-scrape, and the last-write-wins fragment order is already the update path
it would need.
