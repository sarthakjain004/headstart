# ADR-0048: Don't re-fetch a detail we already hold

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0021 · **Amended by:** [ADR-0050](0050-persist-descriptions-across-runs.md) — the skip-list is re-keyed
onto the description store, so it means *we hold this detail* rather than *we embedded this Job*;
and by the 2026-09-16 amendment below, which adds a second skip the list can never express

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
works to stay inside; the existing `squash-dataset-history.yml` is what keeps that history from
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
- **No other ATS.** The skip needs two things at once: the listing must already carry `title`
  *and* `department`, and the detail must supply nothing but `description`. Eightfold's PCSX
  surface is the only one here where both hold, and the three other detail-pass scrapers fail a
  different half each — which is why their own `fetch_raw` comments already refuse the ADR-0048
  skip, and none of them is touched. **oracle** fails the first: `Category`/`JobFunction` are
  0.0% on the listing, so the gate would be classifying on a bare title and would drop real tech
  Jobs. **jazzhr** and **zoho** fail the second: their detail page is the only source of
  `employment_type`/`experience`/`posted_at`/`salary` (jazzhr) and Salary/Currency (zoho), none of
  which the description store holds. Whether a *tech* gate — as opposed to ADR-0048's held-detail
  gate — could be made to pay on those two is a separate question this does not open.

The two verdicts cannot drift: `department` goes through one expression (`_department_of`) that
`parse` also emits, and `classify` strips the title itself, so it reads `p["name"]` exactly as it
will later read `parse`'s stripped copy. If `tech_filter` widens and a previously non-tech posting
becomes tech, it is simply absent from the store and gets its detail on the next run — the same
self-healing path a brand-new Job takes.

**Unlike the held-detail skip, this one is not conditional on `have_details`.** That default —
`None` means fetch everything, for every caller outside the pipeline — exists because only the
pipeline knows what has been embedded. The tech gate needs no such knowledge: nothing anywhere
reads a non-tech description, on CI or on a laptop. So a local `verify_scraper.py` run against an
eightfold Board now shows `description: null` on its non-tech rows, which is the new truth about
that Board's scrape rather than a defect of the run. A caller that wants every description back
should drop the gate, not pass a skip-list.
