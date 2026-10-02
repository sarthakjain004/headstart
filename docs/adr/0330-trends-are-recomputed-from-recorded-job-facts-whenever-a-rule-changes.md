# ADR-0330: Trends are recomputed from recorded Job facts whenever a rule changes

**Status:** accepted; steps 1–2 built · **Date:** 2026-09-29 · **Amends:**
[ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (it
rejected storing each Job's history), [ADR-0292](0292-the-description-store-is-not-reaped-until-a-last-listed-signal-exists.md)
(the description store's future reaper) · **Relates to:**
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (counting changes),
[ADR-0190](0190-the-embedding-store-keeps-only-served-and-scraped-jobs.md) (the store prune),
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) (Opened,
Closed and Recounted) · **Issue:** #904 (raw ATS payloads, deferred)

## Context

The owner changes the tech filter, the derivations, the classifier, the dedup rules and the
scrapers often, and the Trends tab has become unusable for analysis. Four measurements on
2026-09-29 (captures in `experiment/trends-restatement-2026-09-29/`, not committed):

- **Most of every line is not hiring.** Of the index line's absolute movement over the 397 ticks
  since 2026-09-13 (317,470 openings gross):
  - 42% is coverage no stamp marks: Boards landed (94,514 openings on 18,941 Boards), Boards
    removed, scraper fixes, unstamped dedup;
  - 28% is marked counting changes;
  - 15% is the 2026-09-28 Dormant-Board eviction;
  - at most 15% is hiring.

  A Methodology stamp moved on 16 of the 397 ticks, 14 times in the last 173 hours.
- **The live tab contradicts itself.** Of 57 sampled views, 47 had a headline dominated by steps
  that are not hiring. The index's last 7 days read +41,808 under All sources, −6,707 under
  Tracked from start, and −872 opened less closed.
- **Stamps name the wrong cause.** Five of the eight derivations stamps sit on steps the
  derivations did not make: SmartRecruiters' department fallback feeding the tech filter
  (+14,896), 154 Oracle test pods removed (−15,855), and the Dormant eviction (−41,524). Scraper
  changes that feed the tech filter a new field bump nothing (#564, #561, #770, #798).
- **Nothing can be recomputed, because a rule's inputs are thrown away.** Once a Job leaves the
  served table only its id and its description text survive; its title, department, company,
  requisition and listing dates are gone. The tech filter's rejects are never kept. LanceDB on
  HF time-travels less than a day back, and the dataset's history is squashed.

ADR-0230 rejected storing each Job's history ("growth nobody asked for"). Even the design it
rejected (its Design B) would have stored the rules' outputs, each Job's family and band, not
the inputs the rules read, so no design considered could have replayed a new rule over the past.

## Decision

**Trends separates facts from rules.** Facts are what the scrape saw, recorded once and never
rewritten. Rules are the tech filter, the English gate, the derivations, the dedup rules, the
classifier and which Boards count. Past Trends are recomputed from the facts under today's rules
whenever a rule changes, so a rule change moves the whole history and draws no step.

1. **Record Job facts** (step 1, built here). `scrape_join` sees every scraped line, tech or not,
   and `job_facts` records under `data/facts/`:
   - **Job facts** (`job_facts/{stamp}.parquet`): one row when a Job is first listed, when its
     raw fields change, when an authoritative read of its Board no longer lists it (`unlisted`),
     and when its Board leaves the keep-set `index prune` sweeps against (`off_board`: the
     Scrapable Boards less those whose gone-verdict parole re-confirmed, and never from a keep-set
     under the 1,000 Boards prune refuses as broken). A row holds the raw fields every rule reads, which are every `Job` field but
     its id, ATS, fetch time and description (company, title, location, remote, department, url,
     posted_at, experience, employment type, salary, requisition), and whether the scrape carried
     a description.
   - **Board reads** (`board_reads/{stamp}.parquet`): every Board the run read, whether the read
     was authoritative, truncated or an error and why, whether its absences counted, the lines it
     returned, the total it stated where its scraper reports one, and its seconds.
   - **The Listed set** (`listed_jobs.parquet`): each currently listed id with its Board and a
     hash of its raw fields. It is state, rewritten each run, and exists only so the next run can
     tell what changed without storing every id on every run.

   Every file names the run, its commit (`code_sha`) and the scope rule it used. Non-tech Jobs are
   included, and so are Jobs later removed as duplicates. A run that cannot record its facts still
   publishes its scrape: the facts are written all or nothing, and the next run records the
   changes.
2. **Archive closed Jobs' description vectors** before `embed_prune` drops them, at half
   precision (the owner's choice, 2026-09-29), so a classifier change can re-sort the past
   (built: `data/facts/job_vectors/{tick}.parquet`, each file naming its embedder). Measured before
   it merged on 40,000 rows of the 2026-09-23 served table (37,515 with cached title logits): the
   head (v3) decided the same family for all 40,000 at either precision, and no component moved by
   more than 1.0e-4. An archive that cannot be written stops the prune, so no vector is dropped
   unarchived.
   The description store keeps every Job's text: ADR-0292's reaper, if ever built, archives
   rather than deletes.
3. **Restate.** A pure function of (facts, rules) rebuilds each tick's per-Board counts. It is
   validated first by reproducing today's `role_trends` history under today's rules on the ticks
   both cover.
4. **Serve the restated history.** A workflow reruns the restatement whenever the rules'
   fingerprint moves, and the Space reads its output. The fingerprint is a hash of the rule code,
   config, classifier weights and alias ledgers, computed rather than bumped by hand. Netting and
   counting-change markers remain only where a restatement cannot reach.
5. **Coverage.** A restatement cannot see Jobs no scrape saw. A found Board's backlog is placed at
   each Job's `posted_at` on the ATSes where that date is reliable (measured 2026-09-29 on the
   served table: 90% or more of postings within 2 days of first sight on Workday, SuccessFactors,
   Oracle, Ashby, SmartRecruiters, iCIMS and Workable). Elsewhere it is left out as it is today.
   A removed Board is removed from the past too. A percentage or a direction defaults to Boards
   tracked from the start.
6. **History before step 1** cannot be fully recomputed. It is spliced onto the restated series at
   their overlap and labelled approximate, and may be seeded from the full-row snapshots kept on
   the owner's machine.

The owner's other decisions (2026-09-29):
- **No description text is kept for non-tech Jobs.** A past Job a widened filter admits is
  classified from its title alone, or lands in unclassified tech.
- **Raw ATS payloads are deferred** to #904. Until then a scraper parsing change is a dated
  coverage step per ATS, located through `code_sha`.

## Consequences

### Validation repair, 2026-10-02

The Sept 30 comparison mixed an old Board ledger with newer production coverage,
and lacked the Jobs the index already served before fact capture. Exact comparison
starts from a Reference baseline containing every served Job. Earlier observations
remain available but have incomplete starting coverage.

`trend_reference` captures served source fields, description, half-precision vector
and independent placement once, then writes changed and removed ids per Tick.
Historical edits keep their own input versions. This supplements pre-filter Job
facts; it does not invent raw fields absent from the served table. Half precision
remains approximate near classifier decision boundaries.

The checkpoint index commits with live tick state; input fragments name their parent
and run identity. Orphan fragments from failed state publication cannot advance
the next checkpoint. Code, model configuration and Board ledgers are preserved by
content fingerprint. Capture failure remains non-fatal to Search but must block
validation of the affected window.

Median total gaps cannot prove correctness. Compare per-id membership and placement,
then each Board/family/band at every tick under identical preserved rules. Separately
measure intentional differences caused by restating with changed rules.

The first validator certifies observed **tech stock placements** only. It uses
preserved full-precision title/row logits to avoid float16 boundary drift, resolves
Board identities from frozen ledgers, and reconciles stock with independently
published deltas. It does not certify raw-scrape admission replay, watched roles,
`new`, or turnover; those remain requirements of the draft restatement engine.
Reports preserve partial results with `complete:false` until the whole window passes.

Ids inherited from the baseline but absent from the scrape's Listed set are added
to absence tracking once. Existing scraped hashes remain untouched. This permits a
future authoritative read to record their first absence, rather than leaving them
immortal in the replay. The baseline, not a fabricated fresh listing fact, supplies
their starting provenance. Failed seeding must not advance the reference parent.

- **A rule change stops breaking lines** once steps 3 and 4 land. Until then the facts accumulate
  and nothing reads them.
- **The facts can only start now.** Every day not recorded is a day no later rule can restate.
- **Cost, measured 2026-09-29.** A Job-fact row is 38 B and a Listed-set row 17 B (zstd, sorted,
  on 332,383 scraped lines). The first run records every Job its Slice lists (about 3.65M rows,
  about half the Scrapable Boards); later runs record only what changed.
- **A Board's first read lists its whole backlog.** Every Board outside the first Slice, and every
  Board landed later, arrives as `listed` on its first read. That is coverage, not openings, and
  a restatement reads a Board's first Board read that way. At the current pace the facts, the vector archive and the
  description store reach about 35 GB of HF storage in a year, 35% of the quota. The Space
  downloads none of it.
- **The join job gains a download and a write.** The Listed set (about 100 MB at 5–7M listed Jobs)
  is fetched and rewritten each run, and rides the corpus-state artifact to `merge`, which
  uploads it with the run's facts in one commit. A run whose upload fails loses both together,
  and the next run diffs against the older Listed set, so no change is lost or counted twice.
- **"Not listed" depends on a scope rule.** A Job is recorded as no longer listed only when an
  authoritative read of its Board missed it, or its Board left the keep-set `index prune` sweeps
  against. The join reads the board-failures ledger the previous run left, so a Board whose
  parole re-confirms turns `off_board` one run after prune evicts it. That is the
  eviction scope `index sync` uses (ADR-0053, ADR-0161) and `index prune`'s off-Board sweep,
  both built from the helpers those stages use (`index_plan.unauthoritative_among`,
  `board_failures.reconfirmed_among`, `index_plan.MIN_KEEP_BOARDS`) in one place,
  `job_facts.RunScope`. The facts record its version (`scope_version`), because it is the one
  decision a fact carries.
- **A description's presence does not make a Job `changed`.** A Job whose text the store holds is
  scraped without it (ADR-0048) and with it again on a re-fetch (ADR-0211), so it would churn.
- **The stated total is sparse.** Only Taleo Enterprise reports its Board's own total today. The
  shared shortfall check (`mark_truncated_unless_negligible`) cannot stand in for it: detail-pass
  callers pass the length of our own listing, and most call it only on a shortfall. Each scraper
  reporting its listing's total is a follow-up.
- **Fragments accumulate** at two files a run per directory. HF's 10,000-files-per-directory
  limit is years away. A monthly fold, like the description store's, comes before it.

## Alternatives

- **Keep netting counting changes on read (ADR-0230, ADR-0233).** It cannot restate anything,
  and a counting change it does not know about reads as hiring. Measured above.
- **Store each Job's family and band (ADR-0230's Design B).** Those are rule outputs. A new rule
  cannot be run on them.
- **Store every listed id on every run** instead of changes against a Listed set. That is about
  36 MB a run and 53 GB a year, against a Listed set the next run rewrites.
- **Record raw ATS payloads now.** Deferred to #904: their size is unmeasured and the hook
  touches every scraper.
