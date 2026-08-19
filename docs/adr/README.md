# Architecture Decision Records (ADRs)

Non-obvious design decisions for HeadStart — the option picked, the ones rejected, and why — so the
reasoning survives past the commit that made it.

One file per decision, numbered in the order made (`NNNN-short-title.md`). Record a new decision as
the next number; don't edit a past ADR's decision — if something changes, add a new ADR that
supersedes it and note the supersession in both.

| ADR | Decision | Date |
| --- | --- | --- |
| [0001](0001-per-ats-slug-derivation.md) | Per-ATS slug derivation via `slug_from` on the scraper | 2026-06-22 |
| [0002](0002-pooled-thread-local-http.md) | One pooled, thread-local curl_cffi HTTP session | 2026-06-22 |
| [0003](0003-fan-out-detail-fetch.md) | Concurrent detail fetch via `BaseScraper.fan_out` | 2026-06-23 |
| [0004](0004-memory-safe-parallel-resumable-scrape.md) | Memory-safe, parallel, resumable full-board scrape | 2026-06-24 |
| [0005](0005-embedding-model.md) | Embedding model: local `nomic-embed-text-v1.5` for English semantic search | 2026-06-25 |
| [0006](0006-what-we-embed.md) | What we embed: title + cleaned description; structured fields stay as filter metadata | 2026-06-28 |
| [0007](0007-search-metadata-canonical-typed.md) | Search metadata is a typed, canonical `Job`-shaped projection | 2026-06-28 |
| [0008](0008-local-lancedb-vector-store.md) | Local LanceDB for the query-time vector store (cloud later) | 2026-06-28 |
| [0009](0009-experience-extraction.md) | Years-of-experience extraction: a tiered deterministic cascade | 2026-06-29 |
| [0010](0010-feed-from-jsonl.md) | Dashboard feed built from the per-board `.jsonl`, not an in-memory copy | 2026-06-29 |
| [0011](0011-retrieval-eval-harness.md) | Retrieval-eval harness: a validated LLM judge and graded nDCG | 2026-07-01 |
| [0012](0012-liveness-ledger.md) | Liveness state as a TTL'd ledger keyed by `(ats, tenant)` | 2026-07-02 |
| [0013](0013-experience-plausibility-guards.md) | Experience plausibility guards: fix Tier 1, defer the Tier 2 anchor | 2026-07-03 |
| [0014](0014-search-index-ingestion-and-freshness.md) | Search-index ingestion: real corpus, scrape-diff eviction, incremental LanceDB | 2026-07-03 |
| [0015](0015-async-multiplexed-fan-out.md) | Async HTTP/2-multiplexed detail fan-out, opt-in per scraper | 2026-07-03 |
| [0016](0016-async-fan-out-default.md) | Async multiplexed fan-out on by default, width 100 | 2026-07-03 |
| [0017](0017-tech-role-filter.md) | Post-hoc recall-biased tech-role filter as the authoritative tech gate | 2026-07-03 |
| [0018](0018-experience-seniority-fallback.md) | Experience: widened description patterns + a data-calibrated seniority fallback | 2026-07-03 |
| [0019](0019-tech-corpus-search-index.md) | Tech-corpus search index (thin slice): embed `data/jobs/tech`, seniority-estimated experience filterable | 2026-07-03 |
| [0020](0020-free-tier-deployment.md) | Free-tier deployment: GitHub Actions ingest, private HF dataset state, HF Space serving | 2026-07-04 |
| [0021](0021-re-embed-on-content-change.md) | Re-embedding changed content: targeted eviction now, content-hash later | 2026-07-04 |
| [0022](0022-tech-priority-board-ordering.md) | Tech-priority board ordering: EWMA ledger, priority-first scrape + embed slices | 2026-07-06 |
| [0023](0023-prune-stale-and-duplicate-index-rows.md) | Prune stale/duplicate index rows: dead-Board sweep + case-variant dedup | 2026-07-17 |
| [0024](0024-india-location-gazetteer-filter.md) | India location filter: query-time gazetteer expansion (vetted alias substrings) | 2026-07-20 |
| [0025](0025-parallelize-nightly-pipeline.md) | Parallelize the nightly pipeline across GitHub Actions runners: plan → fan-out → merge | 2026-07-24 |
| [0026](0026-parallelize-nightly-scrape.md) | Parallelize the nightly scrape (ADR-0025 Phase 2): scrape-plan → scrape-fan → join | 2026-07-25 |
| [0027](0027-measured-scrape-cost-ledger.md) | Bin-pack the scrape fan-out on measured per-board seconds | 2026-07-25 |
| [0028](0028-ingest-package.md) | The scheduled ingest run lives in `src/headstart/ingest/` | 2026-07-25 |
| [0029](0029-embedding-cost-model.md) | Embedding cost is linear in tokens — length-sort batches | 2026-07-25 |
| [0030](0030-fail-closed-on-unfetched-state.md) | Fail closed when the prior state was not fetched | 2026-07-28 |
| [0031](0031-first-seen-index-stamp.md) | Stamp `first_seen` when a Job enters the index | 2026-07-28 |
| [0032](0032-llm-access-via-router-behind-a-password.md) | LLM access goes through the router, behind a password | 2026-07-28 |
| [0033](0033-state-fetch-retry-budget.md) | Size the state-fetch retry budget to the outage it actually faces | 2026-08-02 |
| [0034](0034-nonprod-boards-dead-by-convention.md) | Non-production boards are dead by convention | 2026-08-02 |
| [0035](0035-email-job-alerts.md) | Email job alerts — invite-only, Google-verified, one Digest per run | 2026-08-05 |
| [0036](0036-fetch-hf-state-without-xet.md) | Fetch HF state over the plain path, not Xet | 2026-08-05 |
| [0037](0037-wellfound-three-surface-scrape.md) | Wellfound is scraped through three surfaces, behind a real browser | 2026-08-05 |
| [0038](0038-telegram-alerts-and-pluggable-transports.md) | Telegram alerts — one Digest, pluggable transports, enrolment by approval | 2026-08-06 |
| [0039](0039-pipeline-logging.md) | Pipeline logging through one stdlib seam (`headstart.log`) | 2026-08-10 |
| [0040](0040-role-trend-ledger.md) | Role-trend ledger: frozen embedding centroids × experience bands | 2026-08-10 |
| [0041](0041-profile-stored-extraction.md) | Profile: store the LLM extraction, discard the Résumé | 2026-08-11 |
| [0042](0042-signed-in-ui-saved-sets.md) | Sign-in-required UI: Accounts, Saved sets, Saved jobs on the per-record store | 2026-08-11 |
| [0043](0043-saved-sets-subscription-projection.md) | Saved sets as per-record files; the Subscription is the emailing set's projection | 2026-08-12 |
| [0044](0044-saved-jobs-display-copies.md) | Saved jobs as per-record display copies, keyed by the job id | 2026-08-12 |
| [0046](0046-index-collapse-guard.md) | A collapse guard on index eviction | 2026-08-13 |
| [0047](0047-pace-against-the-origin.md) | Retry the wall, spread the load — and why pacing cannot fix it | 2026-08-13 |
| [0048](0048-skip-details-we-already-hold.md) | Do not re-fetch a detail we already hold | 2026-08-13 |
| [0049](0049-match-boards-by-prefix-not-by-parsing.md) | Match a Job id to its Board by prefix, not by parsing | 2026-08-13 |
| [0050](0050-persist-descriptions-across-runs.md) | Persist descriptions across runs; key the detail skip-list on holding them | 2026-08-13 |
| [0051](0051-trends-as-share-flow-and-watched-roles.md) | Trends measure share and flow, and can watch a named role | 2026-08-13 |
| [0052](0052-watch-the-large-domain-roles-too.md) | Watch the large domain roles too, not only the small ones | 2026-08-13 |
| [0053](0053-scope-eviction-on-scrape-outcome.md) | Scope eviction on a Board's scrape outcome, not on whether it emitted a line | 2026-08-13 |
| [0054](0054-learned-fan-out-speedup.md) | Predict the scrape makespan from a learned fan-out speedup | 2026-08-14 |
| [0055](0055-bound-the-collapse-guards-hold.md) | Bound the collapse guard's hold so held rows drain instead of ratcheting | 2026-08-14 |
| [0056](0056-darwinbox-browser-escalation.md) | Escalate walled darwinbox boards to a real browser | 2026-08-15 |
| [0057](0057-record-family-assignments-and-report-reassignment.md) | Record each row's family assignment, and report the rows that moved | 2026-08-16 |
| [0058](0058-consecutive-gone-quarantine.md) | Confirmed-dead boards quarantine via a consecutive-gone ledger in `data/state/` | 2026-08-18 |
| [0059](0059-two-board-keyspaces.md) | The priority ledger keys on `board_key`, the cost ledger on `{ats}:{slug}` | 2026-08-18 |
| [0060](0060-narrative-guards-for-the-work-word-patterns.md) | The work-word patterns carry narrative guards and their own requirement ceiling | 2026-08-18 |
| [0061](0061-refreshable-metadata.md) | Stored metadata is refreshable — facts reconcile, derivations re-derive on a version bump | 2026-08-18 |
| [0062](0062-drain-the-description-gap.md) | Drain the description gap by aiming the slice, and record what each vector actually saw | 2026-08-18 |
| [0063](0063-spare-egress-for-a-spent-origin-budget.md) | A shard that spends an origin's budget picks up a spare egress IP | 2026-08-18 |
| [0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) | Widen experience recall under a rule that no existing Tier-2 answer may change | 2026-08-19 |
| [0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) | The spare egress buys a different IP, not a fresh budget | 2026-08-19 |
| [0068](0068-a-department-names-the-org-not-the-role.md) | The tech gate's disqualifier reads the title; a department vetoes only through a discipline that names the role | 2026-08-19 |
| [0069](0069-sets-own-their-projection-against-the-allowlist.md) | Sets own their projection; the alerts run yields to the Space's sets endpoints | 2026-08-19 |
