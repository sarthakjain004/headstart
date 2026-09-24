# ADR-0048: Don't re-fetch a detail we already hold

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0021 · **Amended by:** [ADR-0211](0211-held-descriptions-are-re-fetched-on-a-seven-day-rotation.md) — five Scrapers re-fetch each held detail every 7 days, so the skip-list leaves the due ones out;
[ADR-0050](0050-persist-descriptions-across-runs.md) — the skip-list is re-keyed
onto the description store, so it means *we hold this detail* rather than *we embedded this Job*;
by the 2026-09-16 eightfold amendment below, which adds a second skip the list can never express;
by [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md), which replaces both hand-rolled
gates below with one seam and states which scrapers may take it;
and by the 2026-09-16 successfactors amendment below, which adds that same second skip on a
different, measured signal rather than an exact one

## Context

ADR-0047 narrowed Eightfold's description loss from 78.6% to ~50% by pacing and by retrying the
bot-wall 405, and said plainly that pacing could go no further: the provider's edge meters per
network origin, and a full Eightfold scrape needs more requests than any affordable speed can buy.
The remaining lever is not to make the requests politer but to **stop making most of them.**

The per-job detail fetch exists to fill one field the listing endpoint omits: `description`. In
the pipeline it has three consumers, and all three run **once per Job, at embed time**:

- `doc_prep.is_english` — the English gate, on `title + description[:500]`
- `doc_prep.build_doc` — the Doc text that gets embedded
- `experience.extract` — the ADR-0018 experience cascade, via `to_meta`

It is not in the served LanceDB schema, the tech filter reads `title` + `department`, and the feed
does not display it. And `embed_plan` skips ids already in the store, so **a Job that has been
embedded never has its description read again.** Re-fetching it every run is pure cost.

The scale of that cost, from a real `embed_plan` line — `new Docs: 703 (scanned 199336, already
186009, non-English 12624)`: **93.3% of everything scraped is already embedded.** Eightfold alone
makes ~42,600 detail fetches per run, the overwhelming majority to re-read text nothing will look
at.

## Decision

The scrape layer gains one question — *does this Job still need its detail fetch?* — and the
pipeline answers it:

1. **`embed_merge` publishes `data/state/embedded_ids.txt.gz`**, one Job id per line, gzipped. The
   store's own `meta.jsonl` answers the same question at 226 MB; this is ~3% of that.
2. **`scrape_plan` copies it into the scrape-assignments artifact**, which every shard already
   downloads. No new download path, no workflow change, no credentials in the scrape job.
3. **`scrape_run` loads it and passes it to `scrape_all(..., have_details=...)`**, which hands it
   to `get_scraper`.
4. **`BaseScraper.needs_detail(native_id)`** answers the question, composing the composite key
   with `board_key()` — `personio` and `workday` override that, so a caller building
   `{ats}:{slug}:{id}` itself would silently match nothing. `EightfoldScraper._api_records`
   fetches details only for the Jobs where it says yes.

**The seam is expressed in the scrape layer's vocabulary, not the embed layer's.** A scraper is
told *"you already hold this Job's detail"*, never *"this Job is embedded"* — it does not import
from the embedding stage, and it does not know why a Job is covered. `scrape_plan` is the one
module that knows both halves, which is what a pipeline stage module is for.

`have_details` is set on the scraper *after* construction rather than passed to `__init__`. Five
scrapers override `__init__` and exactly one consults this; widening all five signatures would be
churn for a concept they ignore. The default is `None`, meaning *fetch every detail* — which is the
pre-ADR-0048 behaviour and what every caller outside the pipeline gets, including a first run and
any run where the planner found no list to ship.

## Consequences

Eightfold's **API path** should fall by roughly 15×, not the two orders of magnitude the
"93.3% already embedded" figure suggests on its own. Two populations keep their fetches:

- the ~0.35% of Jobs that are genuinely new each run — the ones we are doing all this for, and
- the **6.3% held out as non-English**, which are *never* in the store and so are re-fetched every
  run, forever. That is correct rather than wasteful: `is_english` reads `title + description`, so
  dropping their description would leave the gate judging a bare title and could flip them into the
  index badly. Persisting the language verdict would remove them too, and is not attempted here.

~6.7% of ~42,600 is ~2,850 fetches per shard-run, which sits well under the origin budget ADR-0047
measured. That is the outcome that matters: descriptions for new Jobs should arrive rather than
being lost, since a Job embedded without one keeps a title-only vector forever.

The **sitemap fallback** is deliberately untouched, so the ~20% of tenants that 403 the API keep
their current load and the headline saving does not apply to them. There the per-job page supplies
`title`, `location` and `posted_at` rather than only the description, and `parse` drops any Job
without a title — skipping those fetches would delete Jobs, not save work.

**A shard reads the previous run's list**, because `embed_merge` runs after the scrape it would
inform. That lag is safe in the only direction that matters: the list can be *stale-old*, naming
Jobs embedded before this run, but it can never name a Job that has not been embedded yet. A Job
scraped for the first time is absent from it and gets its detail fetched, which is the whole point.

**Eviction has to rewrite the list, or it silently defeats itself.** ADR-0021's targeted
`evict_store.py --ats <list>` drops an ATS's rows from the store so the next run re-embeds them
fresh. It rewrote `meta.jsonl` only — leaving this list naming ids the store no longer has, so the
next scrape would skip exactly the descriptions the eviction just discarded, and they would
re-embed from the title alone. Permanently, since `embed_plan` then skips them. `evict_store.py`
now regenerates the list from the rewritten `meta.jsonl` in the same run. **That regenerated list
still has to reach HF**: eviction runs on a laptop while embedding runs on CI, so the runbook must
push `data/state` alongside `data/embeddings/jobs`, or the next scrape pulls the stale list from
the dataset and the same trap springs.

**The skip is by Job id, so it is only as good as the store.** A Job whose description failed to
fetch and which was then embedded title-only *is* in the store, so it is now skipped — the damage
is frozen rather than eventually self-healing. It was never going to self-heal anyway (`embed_plan`
skips embedded ids), so this changes nothing in practice, but it does mean the repair of the
~16,771 already-degraded Jobs cannot simply be "wait for a clean run". That repair needs to force
both a re-fetch and a re-embed, and is deliberately still outstanding.

The alignment trap is worth naming because it is easy to reintroduce: the detail fan-out now covers
a *subset* of a Board's positions, so its results must be paired back **by id**. The previous code
zipped them positionally against the full position list, which would have hung each description on
the wrong Job. `test_eightfold_skips_details_it_already_holds` pins it.

**`scripts/enrich/experience_coverage.py` is collateral damage.** It is a fourth reader of
`description`, outside the pipeline: CLAUDE.md mandates running it over `data/jobs/tech/{ats}.jsonl`
whenever `experience.py`'s patterns change. Eightfold rows in that file now carry `description:
null` for every already-embedded Job, so its coverage table and its `--misses eightfold` sample
collapse to near-nothing for that ATS — silently, as "no coverage" rather than "not measured".
Anyone tuning experience patterns against Eightfold must first re-scrape with the skip-list absent
(`--embedded-ids /nonexistent` on the planner, or simply no list on a local run).

The list adds a few megabytes to the dataset every run — the ids share long common prefixes and
compress well — against an HF quota the workflow already
works to stay inside; the existing `reclaim-dataset-storage.yml` is what keeps that history from
growing without bound, and this rides on it rather than needing anything new.

The list is shipped whole rather than partitioned per shard — ~6 MB gzipped, in an artifact every
shard already pulls. Partitioning by Board would shrink each shard's copy, at the cost of the
planner having to group ids by Board. Not worth it until the artifact is actually a problem.

Only Eightfold consults `needs_detail` today. SuccessFactors is the obvious second caller — 115,372
detail fetches across the same five runs, at a 2.1% loss rate — but it is not currently failing, so
wiring it is left until there is a reason. **Revisit when a second ATS starts losing details.**

## Amendment, 2026-09-16: the skip-list cannot cover a Job it never sees

The skip-list is built from the description store, and the store is reconciled against
`data/jobs/tech` — the corpus **after** `filter_tech`. A **non-tech** posting therefore never
enters the store, never reaches the list, and `needs_detail` says yes for it on every run,
forever. That is not a stale list; it is a population the list is structurally incapable of
naming.

It is most of the traffic. Across the five runs of 2026-09-16, eightfold's detail `attempted`
was 34,736 / 35,131 / 36,045 / 34,646 / 34,970 while its own non-tech count was 34,806 / 35,213 /
36,090 / 34,729 / 35,054 — tracking to within 0.3% every run, with `filled from store` tracking
the ~24,240 tech count instead. The cost lands on the provider that runs out of **Origin budget**
first: one run logged `HTTP 429 x234 on 26 Board(s)` for eightfold, and one shard spent 1,821
spare-egress rescues on it.

So `_api_records` now asks a second question before fetching: **will the tech gate keep this
posting at all?** `tech_filter.classify` reads `title` + `department`, both of which the PCSX
search already returns — probed live 2026-09-16 across 6 boards / 2,929 positions, `name` present
2,929/2,929 and `department` 2,843/2,929 — so the detail body was never an input to that
decision. Per-board skippable share ranged 16.0% (`careers.qualcomm.com`, unusually tech-heavy)
to 95.5% (`vale.eightfold.ai`); six boards is evidence, not proof, and the run-log figure above is
the population number.

The 2.9% of positions the listing gives no `department` are not a gap in the gate: `parse` emits
that same `None`, so `filter_tech` classifies them on the title alone too, and the two verdicts
stay identical.

Three things this deliberately does **not** change:

- **The non-tech Job is still scraped and still emitted**, with `description: null`. The scrape
  writes the full set to `data/jobs/{ats}.jsonl` and `filter_tech` is what drops it (ADR-0017);
  nothing about the Board's totals, truncation verdict or eviction scope moves.
- **The sitemap fallback** (`_sitemap_records`) is untouched, as in the original decision: there
  the per-job page supplies `title`, and `parse` drops a Job without one, so skipping the fetch
  would delete Jobs rather than save work. The gate is on the PCSX/SmartApply path, whose listing
  already carries every field the gate reads.
- **No other ATS gets *this* gate.** The skip needs two things at once: the listing must already
  carry `title` *and* `department`, and the detail must supply nothing but `description`.
  Eightfold's PCSX surface is the only one here where both hold, and the three other detail-pass
  scrapers fail a different half each — which is why their own `fetch_raw` comments already refuse
  the ADR-0048 skip, and none of them is touched. **oracle** fails the first: `Category`/
  `JobFunction` are 0.0% on the listing, so the gate would be classifying on a bare title and
  would drop real tech Jobs. **jazzhr** and **zoho** fail the second: their detail page is the
  only source of `employment_type`/`experience`/`posted_at`/`salary` (jazzhr) and Salary/Currency
  (zoho), none of which the description store holds. Whether a *tech* gate — as opposed to
  ADR-0048's held-detail gate — could be made to pay on those two is a separate question this does
  not open. **successfactors does get a tech gate**, on a different signal than this section's —
  see the second amendment below.

The two verdicts cannot drift: `department` goes through one expression (`_department_of`) that
`parse` also emits, and `classify` strips the title itself, so it reads `p["name"]` exactly as it
will later read `parse`'s stripped copy. If `tech_filter` widens and a previously non-tech posting
becomes tech, it is simply absent from the store and gets its detail on the next run — the same
self-healing path a brand-new Job takes.

**Both skips ride `have_details`, so the original default is honoured rather than overridden.**
`None` still means fetch everything, for every caller outside the pipeline. The tech gate does not
*need* that signal on its own terms — no pipeline reader opens a non-tech description — and the
first version of this change left it unconditional for exactly that reason. Two measurements
settled it the other way.

**Production loses nothing by gating.** `scrape_run` loads the list whenever `--assignment` is
set, which is every sharded run, and the five runs above logged `detail skip-list: 671,630 /
671,833 / 672,468 Job details already held`. There is no real run in which `have_details` is
`None`, so the gated version keeps 100% of the measured reduction.

**Not gating costs more than a confusing `description: null`.** Eight callers construct scrapers
directly, and three of them would read the hole as a defect. `scripts/validate/verify_scraper.py`
reports "jobs-with-description" as its health metric (`verify_scraper.py:46`);
`scripts/eval/audit_remote.py` live-scrapes a board (`get_scraper(...).fetch()`, line 100) and
triangulates the `remote` flag against the description text; `scripts/enrich/salary_sample.py`
drives the real scraper's own endpoint methods to measure `salary.extract` recall, and both fields
are derived *from that text*. An unconditional gate hands all three `description=None` on ~59% of
eightfold's postings, and each would report a quality collapse for eightfold specifically that is
not real. That is the same shape of cost this review was convened to remove: zwayam's 100% detail
loss hid for five runs behind a `HTTPError` label where the real answer was `HTTP 403`. A day spent
chasing a fake regression is worse than a branch.

(`scripts/eval/location_field_health.py` also builds scrapers but never reads `description`, and
`scripts/eval/measure_content_drift.py` is not on `main` — neither is affected.)

## Amendment, 2026-09-16 (later the same day): a second scraper, gated on a different signal

The held-detail skip above needs `have_details`, which needs the description store, which needs a
Job id the store already knows — none of which fires the first time a Board is ever scraped.
There is a second, cheaper reason to skip a detail fetch that doesn't: the posting a Board lists
will never be indexed at all, because the **Tech filter** (ADR-0017) will drop it downstream. A
non-tech posting is never in `data/jobs/tech`, so fetching its detail — real cost, real
per-origin budget — buys nothing that survives the run.

For a scraper whose listing already carries `title`/`department`, that gate is exact and free —
the mechanism the eightfold amendment above describes. SuccessFactors' three listing surfaces
carry neither: every field, including
`title` itself, comes from the job page (this file's own scraper module docstring), so there is
no in-listing signal to gate on at all.

There is an out-of-band one: SuccessFactors builds job URLs from the slug of the posting's own
title (`_title_from_slug`, `successfactors.py`) — `{title}/{id}/` on some tenants,
`{location}-{title}[-{state}-{zip}]/{id}/` on others. That slug is a proxy, not the real title, so
this is a measured tolerance rather than an exact gate: 403/403 verdict agreement against a
500-posting sample of careers.hcltech.com (title-only slugs) and 400/400 against a 400-posting
sample of jobs.sap.com (location-prefixed, largely German slugs), both live-verified 2026-09-16.
Neither sample produced a single disagreement between the slug-derived verdict and the real
page's — extra location/state/zip tokens landed as noise `classify`'s word-bounded signals didn't
trip on, not as a source of false negatives, in either sample.

Two things this changes, matching the eventual-consistency shape the eightfold skip already has:

- **The truncation denominator moves with it.** `mark_truncated_unless_negligible` now compares
  against the tech-gated subset actually attempted, not the full listing — a non-tech posting was
  never going to be indexed regardless of whether its detail was fetched, so it must not count as
  "lost" against how authoritative the Board's *tech* read is. Getting this backwards — counting
  every gated-out posting as a loss — would mark nearly every SuccessFactors Board Unauthoritative
  on every run, since most such boards are not majority-tech (21.3%–28.7% in the two samples
  above).
- **A non-tech posting is never scraped at all here**, not scraped-with-a-null-description as the
  PCSX gate leaves it. There is nothing to leave null: without a detail fetch, SuccessFactors has
  no title either, and `parse` already drops any Job it cannot title. If the tech filter ever
  widens to keep a slug-shape it currently misses, that posting simply starts arriving next run,
  the same self-healing path a brand-new Job takes.

This is a measured tolerance on a proxy signal, not a guarantee for every tenant's slug shape —
unlike the exact, listing-derived gate, it should be re-checked before leaning on it for a
tenant whose URLs weren't part of either sample.
