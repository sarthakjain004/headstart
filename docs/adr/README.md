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
| [0011](0011-retrieval-eval-harness.md) | Retrieval-eval harness: a validated LLM judge and graded nDCG (withdrawn 2026-09-24) | 2026-07-01 |
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
| [0038](0038-telegram-alerts-and-pluggable-transports.md) | Telegram alerts — one Digest, pluggable transports, enrolment by approval | 2026-08-06 |
| [0039](0039-pipeline-logging.md) | Pipeline logging through one stdlib seam (`headstart.log`) | 2026-08-10 |
| [0040](0040-role-trend-ledger.md) | Role-trend ledger: frozen embedding centroids × experience bands | 2026-08-10 |
| [0041](0041-profile-stored-extraction.md) | Profile: store the LLM extraction, discard the Résumé | 2026-08-11 |
| [0042](0042-signed-in-ui-saved-sets.md) | Sign-in-required UI: Accounts, Saved sets, Saved jobs on the per-record store | 2026-08-11 |
| [0043](0043-saved-sets-subscription-projection.md) | Saved sets as per-record files; the Subscription is the emailing set's projection | 2026-08-12 |
| [0044](0044-saved-jobs-display-copies.md) | Saved jobs as per-record display copies, keyed by the job id | 2026-08-12 |
| [0045](0045-per-shard-run-reports.md) | Per-shard run reports, and a run-level summary | 2026-08-12 |
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
| [0064](0064-a-boards-hour-must-buy-tech-jobs.md) | A Board's hour must buy tech jobs | 2026-08-18 |
| [0065](0065-wait-for-the-fresh-ip-rather-than-riding-the-spent-one.md) | Wait for the fresh IP rather than riding the spent one | 2026-08-19 |
| [0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) | Widen experience recall under a rule that no existing Tier-2 answer may change | 2026-08-19 |
| [0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) | The spare egress buys a different IP, not a fresh budget | 2026-08-19 |
| [0068](0068-a-department-names-the-org-not-the-role.md) | The tech gate's disqualifier reads the title; a department vetoes only through a discipline that names the role | 2026-08-19 |
| [0069](0069-sets-own-their-projection-against-the-allowlist.md) | Sets own their projection; the alerts run yields to the Space's sets endpoints | 2026-08-19 |
| [0070](0070-smartrecruiters-does-not-cap-a-board-at-100-postings.md) | SmartRecruiters does not cap a board at 100 postings; the scraper marks its truncation | 2026-08-20 |
| [0071](0071-back-to-back-runs-instead-of-a-fixed-cadence.md) | Back-to-back runs instead of a fixed cadence | 2026-08-20 |
| [0072](0072-a-three-digit-number-condemns-the-whole-span.md) | A 3-digit number condemns the whole span, floor included | 2026-08-20 |
| [0073](0073-narrow-six-retail-workday-boards-at-the-source.md) | Narrow six retail-dominated Workday boards at the source | 2026-08-20 |
| [0074](0074-browse-and-paginate-the-search-index.md) | An empty Query browses; every result set paginates | 2026-08-20 |
| [0075](0075-ats-becomes-a-trends-ledger-dimension.md) | `ats` becomes a trends-ledger dimension, filtered by exclusion at read time | 2026-08-20 |
| [0076](0076-a-lost-page-is-a-truncation-until-most-of-them-are.md) | A page lost mid-crawl truncates the list; losing most of them fails the crawl | 2026-08-20 |
| [0077](0077-smartrecruiters-pages-behind-a-cost-sized-cap.md) | SmartRecruiters pages behind a cost-sized cap | 2026-08-20 |
| [0078](0078-width-narrows-once-the-origin-has-walled.md) | Fan-out width narrows once the origin has walled, and only once it has | 2026-08-20 |
| [0079](0079-smallest-stated-experience-requirement-wins.md) | The smallest stated experience requirement wins | 2026-08-20 |
| [0080](0080-trends-chart-redesign.md) | Trends chart redesign — validated palette, Other bucket, hover layer, radiogroup ARIA | 2026-08-20 |
| [0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md) | The spare-egress pool is deep, not 1–3 addresses | 2026-08-21 |
| [0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) | Salary extraction — a two-tier cascade, period-normalized, no estimate tier (period-less figures amended by 0293) | 2026-08-21 |
| [0083](0083-evict-only-on-a-second-consecutive-absence.md) | Evict only on a second consecutive absence | 2026-08-23 |
| [0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md) | Facet counts are filter-shaped, not query-shaped | 2026-08-25 |
| [0085](0085-pull-hf-data-over-raw-ranged-http.md) | Pull HF data over raw, ranged HTTP — not snapshot_download | 2026-08-25 |
| [0086](0086-country-tag-signals-in-the-india-gazetteer.md) | The India filter matches country tags, not just place names | 2026-08-25 |
| [0087](0087-a-hiring-department-is-not-a-tech-department.md) | A hiring department is not a tech department | 2026-08-25 |
| [0088](0088-a-lost-detail-is-not-a-truncation.md) | A lost detail is not a truncation — classify it, don't scope-exclude on it | 2026-08-26 |
| [0089](0089-the-description-store-holds-text-not-verdicts.md) | The description store holds text, not verdicts — drop `detail_fetched` | 2026-08-26 |
| [0090](0090-rotate-the-egress-before-banning-a-liveness-host.md) | Rotate the egress before banning a liveness host | 2026-08-27 |
| [0091](0091-compaction-outranks-the-pipeline.md) | Compaction outranks the pipeline | 2026-08-27 |
| [0092](0092-resolve-through-warp-not-before-it.md) | Resolve through WARP, not before it | 2026-08-27 |
| [0093](0093-chain-the-successor-the-cron-is-only-a-seed.md) | Chain the successor; the cron is only a seed | 2026-08-28 |
| [0094](0094-ask-for-compaction-on-a-threshold-not-a-clock.md) | Ask for compaction on a threshold, not a clock | 2026-08-28 |
| [0095](0095-a-published-witness-for-unfetched-state.md) | A published witness for unfetched state | 2026-08-28 |
| [0096](0096-one-key-for-both-board-ledgers.md) | One key for both Board ledgers | 2026-08-28 |
| [0097](0097-a-postings-id-comes-from-the-listing-never-the-detail.md) | A posting's id comes from the listing, never from the detail | 2026-08-30 |
| [0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md) | Workday's 400 is a throttle — extend the retry set for it, don't widen it | 2026-08-31 |
| [0099](0099-a-404d-workday-detail-falls-back-to-the-public-pages-json-ld.md) | A 404'd Workday detail falls back to the public page's JSON-LD | 2026-09-01 |
| [0100](0100-break-off-a-detail-pass-the-origin-is-refusing.md) | Break off a detail pass the origin is refusing | 2026-09-01 |
| [0101](0101-remove-the-collapse-guard-the-grace-period-is-the-line.md) | Remove the collapse guard; the grace period is the line | 2026-09-01 |
| [0102](0102-a-400-walls-the-origin-too-not-just-a-429.md) | A 400 walls the origin too, not just a 429 | 2026-09-02 |
| [0103](0103-workdays-400-is-an-invalid-session-cookie-clear-it.md) | Workday's 400 is an invalid session cookie — clear it, don't retry or reroute | 2026-09-02 |
| [0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) | A Keyword filter with a scope map, backed by a stored `description` column | 2026-09-02 |
| 0105–0109 | **Reserved — auto-apply.** Written here 2026-09-03, moved to the private `headstart-apply` repo 2026-09-15 and renumbered there as its ADR-0001–0005. Never reuse these five numbers: the same integers now mean different decisions in the two repos. | 2026-09-03 |
| [0110](0110-record-fan-out-throughput-against-the-width-in-force.md) | Record fan-out throughput against the width in force | 2026-09-05 |
| [0111](0111-duplicate-boards-resolve-the-board-surface.md) | A duplicate Board is found by resolving its Board surface, not by comparing its key | 2026-09-07 |
| [0112](0112-the-door-earns-the-sign-in-before-it-asks.md) | The door earns the sign-in before it asks for it | 2026-09-07 |
| [0113](0113-publish-the-indexs-own-limits-in-the-product.md) | Publish the index's own limits in the product, measured live | 2026-09-07 |
| [0114](0114-a-board-states-its-company-name-in-its-page-title.md) | A Board states its company name in its page title — read it, don't infer it | 2026-09-07 |
| [0115](0115-one-user-agent-identifies-and-hosts-constrain-its-shape.md) | One User-Agent, chosen to identify — and its shape is set by hosts, not by taste | 2026-09-07 |
| [0116](0116-a-quiet-palette-and-a-scanning-layout.md) | A quiet palette, and a layout built for scanning | 2026-09-07 |
| [0117](0117-the-salary-bracket-compares-across-currencies.md) | The salary bracket compares across currencies, at a dated rate | 2026-09-07 |
| [0118](0118-a-fact-can-wear-a-derivations-column.md) | A fact can wear a derivation's column — but then it isn't a fact anymore | 2026-09-07 |
| [0119](0119-the-trends-chart-plots-change-not-level.md) | The trends chart plots change, not level | 2026-09-07 |
| [0120](0120-the-trends-ledger-is-parquet-not-csv.md) | The trends ledger is Parquet, not CSV | 2026-09-09 |
| [0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) | A negligible shortfall is still an authoritative list — let the per-Job grace period have it | 2026-09-09 |
| [0122](0122-the-pipeline-installs-with-uv-and-caches-nothing.md) | The pipeline installs with uv, and caches nothing to do it | 2026-09-09 |
| [0123](0123-a-resume-is-three-layers-structure-layout-and-words.md) | A résumé is three layers — structure, layout, and words | 2026-09-09 |
| [0124](0124-a-resume-document-syncs-to-the-hf-dataset-as-one-file.md) | A Résumé document syncs to the HF dataset, as one file, and only when asked | 2026-09-10 |
| [0125](0125-the-resume-rail-is-the-document-and-the-paper-is-its-preview.md) | The résumé rail is the document; the paper is its preview | 2026-09-10 |
| [0126](0126-the-picker-carries-the-layouts-people-actually-use.md) | The picker carries the layouts people actually use, and says where each one is a bad idea | 2026-09-10 |
| [0127](0127-hygiene-is-a-shared-baseline-opinion-stays-on-the-layout.md) | Hygiene is a shared baseline; opinion stays on the Layout | 2026-09-10 |
| [0128](0128-the-resume-tab-is-two-segments-edit-and-preview.md) | The Résumé tab is two segments — Edit and Preview | 2026-09-10 |
| [0129](0129-the-lancedb-write-checks-its-own-base-instead-of-asking-first.md) | The LanceDB write checks its own base instead of asking first | 2026-09-10 |
| [0130](0130-two-more-arrangements-and-the-blocks-a-resume-carries-that-a-cv-does-not.md) | Two more arrangements, and the blocks a résumé carries that a CV does not | 2026-09-11 |
| [0131](0131-forgetting-a-resume-costs-the-subscriptions-dataset-its-history.md) | Forgetting a résumé costs the Subscriptions dataset its whole history | 2026-09-11 |
| [0132](0132-a-date-rule-may-refuse-to-read-a-month-it-may-not-guess-one.md) | A date rule may refuse to read a month; it may not guess one | 2026-09-11 |
| [0133](0133-the-resume-tab-reads-the-saved-jobs-the-page-already-fetched.md) | The Résumé tab reads the Saved jobs the page already fetched | 2026-09-11 |
| [0134](0134-the-shape-says-which-blocks-the-printer-may-not-split.md) | The shape says which blocks the printer may not split | 2026-09-11 |
| [0135](0135-two-baseline-rules-for-the-blocks-that-shipped-without-any.md) | Two baseline Rules for the blocks that shipped without any | 2026-09-11 |
| [0136](0136-the-value-gate-keeps-one-dimension-because-the-data-has-one.md) | The value gate keeps one dimension, because the data has one | 2026-09-11 |
| [0137](0137-an-agent-reads-a-resume-by-running-the-resume-tabs-own-javascript.md) | An agent reads a Résumé document by running the Résumé tab's own JavaScript | 2026-09-11 |
| [0138](0138-a-materialized-country-column-serves-the-india-filter.md) | A materialized `country` column serves the India filter's country-level case | 2026-09-11 |
| [0139](0139-a-single-source-board-is-its-own-ats.md) | A Single source scraper is its own `ats`, not a slug under one | 2026-09-11 |
| [0140](0140-a-workday-html-listing-response-retries-over-direct-egress.md) | A Workday HTML listing response retries once over direct egress | 2026-09-12 |
| [0141](0141-scrape-health-travels-to-the-publication-receipt.md) | Scrape health travels to the publication receipt | 2026-09-12 |
| [0142](0142-subscription-opt-outs-survive-record-replacement.md) | Subscription opt-outs survive record replacement | 2026-09-12 |
| [0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md) | Trends retain Board deltas for arbitrary comparable cohorts | 2026-09-12 |
| [0144](0144-oracle-taleo-was-a-dead-end-only-for-india.md) | Oracle Taleo was a dead-end only for India | 2026-09-15 |
| [0145](0145-the-value-gate-reads-the-measurement-that-kept-up.md) | The value gate reads the measurement that kept up, not the one that froze | 2026-09-07 |
| [0146](0146-the-derivation-cascade-is-composed-once-in-derived-meta.md) | The derivation cascade is composed once, in `derived_meta` | 2026-09-15 |
| [0149](0149-search-filters-and-index-capabilities-are-two-objects.md) | `build_filter` takes `SearchFilters` + `IndexCapabilities`, not 27 kwargs | 2026-09-15 |
| [0152](0152-tech-filter-owns-its-own-run-report.md) | `tech_filter` owns its own run report | 2026-09-15 |
| [0153](0153-a-fetcher-seam-replaces-the-module-global-http-import.md) | A `Fetcher` seam replaces the module-global `http` import | 2026-09-15 |
| [0154](0154-typed-shard-report-and-plan-records.md) | Typed `ShardReport` and `plan.json` records | 2026-09-15 |
| [0155](0155-one-module-for-board-identity-two-failure-policies-by-name.md) | One module for Board identity, two failure policies by name | 2026-09-15 |
| [0156](0156-the-space-installs-headstart-as-a-real-package.md) | The Space installs `headstart` as a real package | 2026-09-15 |
| [0157](0157-a-scrapers-job-url-is-declared-once-not-authored-three-times.md) | A scraper's job URL is declared once, not authored three times | 2026-09-15 |
| [0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) | JazzHR and Jobvite are worth their storage, measured at full pool | 2026-09-16 |
| [0159](0159-a-hard-cap-marks-truncated-an-approximate-ceiling-does-not.md) | A hard cap marks the Board truncated; an approximate ceiling does not | 2026-09-16 |
| [0160](0160-the-coverage-verdict-is-graded-on-a-share.md) | The coverage verdict is graded on a share, not on any failure at all | 2026-09-16 |
| [0161](0161-the-eviction-scope-travels-as-board-keys.md) | The eviction scope travels between stages as Board keys, not as the corpus it was derived from | 2026-09-16 |
| [0162](0162-a-gone-verdict-expires-quarantine-parole.md) | A gone-verdict expires — quarantined Boards go on parole, not away | 2026-09-16 |
| [0163](0163-report-the-description-gap-as-a-drain.md) | Report the description gap as a drain, and reclassify the Boards that are not Scrapable | 2026-09-16 |
| [0164](0164-mark-when-the-definition-changed-not-just-the-data.md) | Mark when the definition changed, not just when the data did | 2026-09-16 |
| [0166](0166-gate-the-detail-pass-on-the-tech-filter.md) | Gate the detail pass on the tech filter, behind one seam | 2026-09-17 |
| [0167](0167-a-scraper-may-decline-the-multiplexed-path.md) | A scraper may decline the multiplexed path, on a measurement | 2026-09-17 |
| [0168](0168-delete-the-orphaned-blobs-dont-ask-for-them-to-be-collected.md) | Delete the orphaned blobs, don't ask for them to be collected | 2026-09-18 |
| [0169](0169-an-empty-page-ends-an-oracle-board-totaljobscount-does-not.md) | An empty page ends an Oracle Board; `TotalJobsCount` does not | 2026-09-21 |
| [0170](0170-a-provider-outage-is-not-a-gone-verdict.md) | A provider outage is not a gone-verdict, so the quarantine ledger gets no terminal drain yet | 2026-09-21 |
| [0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) | The Hot tab curates what it shows, not the whole index | 2026-09-21 |
| [0172](0172-a-single-source-scraper-declares-its-company.md) | A Single source scraper declares its company; the ledger cannot | 2026-09-21 |
| [0173](0173-rebuild-the-search-indexes-with-the-table.md) | Rebuild the Search indexes with the table | 2026-09-21 |
| [0174](0174-every-pipeline-publishes-current-search-indexes.md) | Every pipeline publishes current Search indexes | 2026-09-21 |
| [0175](0175-a-pyjamahr-board-is-its-company-slug-discovered-from-the-vendors-sitemap.md) | A PyjamaHR Board is its company slug, discovered from the vendor's own jobs sitemap | 2026-09-22 |
| [0176](0176-resume-derivation-sweeps-across-pipeline-runs.md) | Resume derivation sweeps across pipeline runs | 2026-09-22 |
| [0177](0177-an-unknown-reprobe-keeps-a-live-verdict.md) | An `unknown` re-probe keeps a `live` verdict | 2026-09-23 |
| [0178](0178-salary-sort-is-stated-in-one-currency.md) | The salary sort is stated in one currency | 2026-09-23 |
| [0179](0179-the-tech-filter-stays-english-and-its-trade-vetoes-stand-down-for-infrastructure.md) | The tech filter stays English, and its trade vetoes stand down for infrastructure | 2026-09-23 |
| [0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) | An ADP Board is a career center, read in every language it posts in, at one paced budget | 2026-09-23 |
| [0181](0181-a-breezy-board-is-one-verbose-json-listing.md) | A Breezy HR Board is one verbose JSON listing, and a bare `$` is named by country | 2026-09-23 |
| [0182](0182-a-clearcompany-board-is-its-hrm-direct-feed.md) | A ClearCompany Board is its HRM Direct feed, not a clearcompany.com surface | 2026-09-23 |
| [0183](0183-a-cornerstone-board-is-the-tenant-read-across-every-career-site.md) | A Cornerstone Board is the tenant, read across every career site | 2026-09-23 |
| [0184](0184-a-pinpoint-board-is-read-from-its-listing-and-dated-from-its-page.md) | A Pinpoint Board is read from its listing and dated from its posting pages | 2026-09-23 |
| [0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) | Trends narrow to companies picked from a directory of Boards | 2026-09-24 |
| [0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) | A Taleo Enterprise section that another section already lists is an alias | 2026-09-24 |
| [0187](0187-a-workday-requisition-is-served-once-per-tenant.md) | A Workday requisition is served once per tenant, not once per site | 2026-09-24 |
| [0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) | A change to which rows count as duplicates is a Trends epoch | 2026-09-24 |
| [0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) | A Jibe Board is a client, read under its own robots.txt, minus what iCIMS already serves | 2026-09-24 |
| [0190](0190-the-embedding-store-keeps-only-served-and-scraped-jobs.md) | The embedding store keeps only served and just-scraped Jobs | 2026-09-24 |
| [0191](0191-one-module-answers-whether-a-board-is-scraped.md) | One module answers whether a Board is scraped | 2026-09-24 |
| [0192](0192-each-board-ledger-owns-its-key-form.md) | Each per-Board ledger owns the form its Boards are looked up in | 2026-09-24 |
| [0193](0193-one-module-per-materialized-search-filter.md) | One module per materialized Search filter | 2026-09-24 |
| [0194](0194-job-search-absorbs-what-its-adapters-copy.md) | JobSearch absorbs what its adapters copy | 2026-09-24 |
| [0195](0195-one-retry-policy-drives-both-fetch-paths-and-the-warp-daemon-sits-behind-a-port.md) | One retry policy drives both fetch paths, and the WARP daemon sits behind a port | 2026-09-24 |
| [0196](0196-a-job-pages-json-ld-job-posting-is-read-by-one-reader.md) | A job page's JSON-LD JobPosting is read by one reader | 2026-09-24 |
| [0197](0197-salary-owns-the-field-codec-and-one-currency-symbol-resolver.md) | `salary.py` owns the salary field codec and one currency-symbol resolver | 2026-09-24 |
| [0198](0198-tiktok-and-bytedance-share-one-scraper-and-keep-two-ats-values.md) | TikTok and ByteDance share one scraper and keep two `ats` values | 2026-09-24 |
| [0199](0199-the-fetcher-seam-reaches-every-scraper.md) | The Fetcher seam reaches every Scraper | 2026-09-24 |
| [0200](0200-a-board-scraped-empty-is-in-the-eviction-scope.md) | A Board scraped empty is in the eviction scope | 2026-09-24 |
| [0201](0201-a-scraper-states-its-detail-request-once-and-the-base-runs-the-pass.md) | A Scraper states its detail request once, and the base runs the Detail pass | 2026-09-24 |
| [0202](0202-an-adp-recruiting-board-is-a-career-site-read-through-its-token.md) | An ADP Recruiting Management Board is a career site, read through its token in the default language | 2026-09-24 |
| [0203](0203-a-row-becomes-a-board-only-through-its-scraper.md) | A ledger row becomes a Board only through its Scraper | 2026-09-24 |
| [0204](0204-a-scrapers-fetcher-is-bound-to-its-board.md) | A scraper's fetcher is bound to its Board | 2026-09-24 |
| [0205](0205-an-eightfold-site-its-backing-board-already-serves-is-an-alias.md) | An Eightfold career site whose backing ATS Board already serves it is an alias | 2026-09-24 |
| [0206](0206-prune-evicts-a-board-parole-reconfirmed-gone.md) | Prune evicts a Board parole re-confirmed gone, and a replaced scraper voids its verdicts | 2026-09-24 |
| [0207](0207-the-served-description-follows-the-posting.md) | The served description follows the posting | 2026-09-24 |
| [0208](0208-a-failed-zoho-detail-keeps-the-held-description.md) | A failed Zoho detail keeps the held description | 2026-09-24 |
| [0209](0209-a-detail-pass-that-lands-nothing-stops.md) | A Detail pass that lands nothing stops, and no one detail can hold it open | 2026-09-25 |
| [0210](0210-an-eightfold-posting-its-backing-board-serves-is-served-once.md) | An Eightfold posting its backing Board serves is served once, matched on the requisition | 2026-09-25 |
| [0211](0211-held-descriptions-are-re-fetched-on-a-seven-day-rotation.md) | Held descriptions are re-fetched on a seven-day rotation | 2026-09-25 |
| [0212](0212-a-board-is-named-by-a-curated-stated-or-humanised-name-never-its-slug.md) | A Board is named by a curated, stated or humanised name, never by its slug | 2026-09-25 |
| [0215](0215-a-title-rule-decides-a-role-family-before-the-centroid.md) | A title rule decides a role family before the centroid does | 2026-09-25 |
| [0216](0216-a-workday-board-is-named-by-its-postings-legal-entities.md) | A Workday Board is named by its postings' legal entities, checked against its own page | 2026-09-25 |
| [0217](0217-a-board-is-named-by-what-its-postings-agree-on.md) | A Board is named by what its pages or postings agree on, where no Board page names it | 2026-09-25 |
| [0218](0218-an-inactive-trakstar-account-is-gone.md) | An inactive Trakstar account is gone | 2026-09-25 |
| [0219](0219-a-boards-row-is-elected-on-evidence-its-key-is-kept.md) | A Board's row is elected on evidence, and its key is kept | 2026-09-25 |
| [0220](0220-a-trained-title-classifier-decides-a-role-family.md) | A trained title classifier decides a role family, over a one-axis family list | 2026-09-25 |
| [0221](0221-a-refit-is-a-step-in-one-trends-history.md) | A refit is a step in one Trends history, not its end | 2026-09-25 |
| [0222](0222-an-icims-portal-that-redirects-to-another-is-an-alias.md) | An iCIMS portal that redirects to another is an alias | 2026-09-25 |
| [0223](0223-a-taleo-or-adp-requisition-is-served-once-per-tenant.md) | A Taleo or ADP requisition is served once per tenant, not once per Board | 2026-09-25 |
| [0224](0224-a-rows-description-vector-joins-its-title-in-deciding-its-role-family.md) | A row's description vector joins its title in deciding its role family | 2026-09-25 |
| [0226](0226-zoho-throttle-redirect-walls-the-spare-egress.md) | Zoho's throttle redirect walls its egress group | 2026-09-25 |
| [0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) | Trends record each Board's opened and closed jobs, not only its net | 2026-09-25 |
| [0228](0228-a-detail-pass-may-send-its-requests-in-batches-from-one-warmed-tab.md) | A Detail pass may send its requests in batches from one warmed tab | 2026-09-25 |
| [0229](0229-the-slice-reads-every-tech-yielding-board-and-rotates-the-rest.md) | The Slice reads every tech-yielding Board and rotates the rest oldest-first | 2026-09-25 |
| [0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) | Trends keeps one Board-delta history, and decides its rules when reading it | 2026-09-25 |
| [0231](0231-a-jobvite-job-is-read-from-its-detail-page-or-not-at-all.md) | A Jobvite Job is read from its detail page or not at all | 2026-09-25 |
| [0232](0232-the-shared-library-is-grouped-into-packages-by-the-question-each-module-answers.md) | The shared library is grouped into packages by the question each module answers | 2026-09-25 |
| [0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) | Trends serves reconciled line readings, and the page only formats | 2026-09-25 |
| [0234](0234-a-peoplestrong-board-is-its-portal-label-read-through-one-paced-budget.md) | A PeopleStrong Board is its portal label, read through one paced budget | 2026-09-25 |
| [0235](0235-where-the-package-layout-keeps-a-name-and-what-its-rewrites-leave-alone.md) | Where the package layout keeps a name, and what its rewrites leave alone | 2026-09-26 |
| [0236](0236-board-identity-and-scrapable-boards-keep-their-names-inside-boards.md) | `board_identity` and `scrapable_boards` keep their names inside `boards/` | 2026-09-26 |
| [0237](0237-trend-history-keeps-its-name-inside-trends.md) | `trend_history` keeps its name inside `trends/`, and `trend_reading` becomes `line_reading` | 2026-09-26 |
| [0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md) | A mostly re-counted category gives no percentage, and Hot hides only staffing firms and job boards | 2026-09-26 |
| [0239](0239-an-oracle-board-past-the-offset-ceiling-is-read-from-both-ends.md) | An Oracle Board past the offset ceiling is read from both ends | 2026-09-26 |
| [0240](0240-jibe-drops-a-posting-only-when-its-icims-tenant-is-a-board-we-scrape.md) | Jibe drops a posting only when its iCIMS tenant is a Board we scrape | 2026-09-26 |
| [0241](0241-adp-reads-client-names-from-a-committed-cache-and-excludes-its-own-test-clients.md) | ADP reads client names from a committed cache, and excludes ADP's own test clients | 2026-09-26 |
| [0242](0242-empty-tail-boards-back-off-and-slow-boards-start-first.md) | Empty Tail Boards back off, and slow Boards start first in their shard | 2026-09-26 |
| [0243](0243-a-row-the-scrape-saw-is-in-scope-whatever-its-boards-scope.md) | A row the scrape saw is in scope, whatever its Board's scope | 2026-09-26 |
| [0244](0244-publication-deletes-the-search-indexes-the-table-no-longer-reads.md) | Publication deletes the Search indexes the table no longer reads | 2026-09-26 |
| [0245](0245-an-avature-board-is-its-tenant-host-read-through-its-portal-sitemaps.md) | An Avature Board is its tenant host, read through its portals' sitemaps | 2026-09-26 |
| [0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) | A Radancy career front is a Board keyed by its host, scraped in full | 2026-09-26 |
| [0247](0247-search-filters-are-always-open-beside-the-results.md) | Search filters are always open, beside the results on a wide screen | 2026-09-28 |
| [0248](0248-the-trends-tab-speaks-to-a-job-seeker.md) | The Trends tab speaks to a job seeker: plain words, no ATS names, one caption, a folded note | 2026-09-28 |
| [0249](0249-a-home-tab-replaces-the-data-tab-and-navigation-moves-to-a-sidebar.md) | A Home tab replaces the Data tab, and navigation moves to a sidebar | 2026-09-28 |
| [0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md) | A Board silent for two years is Dormant, and its Jobs leave the Tech subset | 2026-09-28 |
| [0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md) | Trends answers are worked out once a boot and kept by the browser | 2026-09-28 |
| [0252](0252-a-workday-department-is-read-off-the-family-slice-that-listed-it.md) | A Workday department is read off the family slice that listed it | 2026-09-28 |
| [0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) | An agent reads the Space's read routes through a read-scoped token (credential superseded by 0258; Space-route rejection by 0267) | 2026-09-28 |
| [0254](0254-an-icims-portal-listing-only-what-a-sibling-lists-is-an-alias.md) | An iCIMS portal listing only what a sibling portal lists is an alias | 2026-09-28 |
| [0255](0255-every-tab-speaks-to-a-job-seeker.md) | Every tab speaks to a job seeker: no pipeline words, short sentences, short table headers | 2026-09-28 |
| [0256](0256-recruitee-reads-a-real-english-description-and-keeps-the-primary-title.md) | Recruitee reads a real English description and keeps the primary title | 2026-09-28 |
| [0257](0257-jobvite-walks-a-short-board-again-and-gates-on-the-listing-title.md) | Jobvite walks a short Board again and gates its detail pages on the listing title | 2026-09-28 |
| [0258](0258-the-spaces-read-routes-answer-anyone.md) | The Space's read routes answer anyone | 2026-09-28 |
| [0259](0259-sensehq-boards-land-from-a-probe-that-reads-the-listings-own-error.md) | SenseHQ Boards land from a probe that reads the listing's own error | 2026-09-28 |
| [0260](0260-a-counting-change-says-what-we-did-and-what-it-did-to-the-counts.md) | A Trends counting change says what we did and what it did to the counts, in one sentence | 2026-09-28 |
| [0261](0261-a-trends-view-nobody-has-asked-for-is-cheap-to-work-out-and-asked-for-ahead.md) | A Trends view nobody has asked for is cheap to work out, and asked for ahead | 2026-09-28 |
| [0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md) | A caller with no session reads the public routes sixty times a minute | 2026-09-28 |
| [0263](0263-the-search-bar-can-match-words-in-the-job-title.md) | The search bar can match words in the job title: a By meaning / Words in the job title switch | 2026-09-28 |
| [0264](0264-a-happydance-career-front-is-a-board-keyed-by-its-host-paced-across-fronts.md) | A Happydance career front is a Board keyed by its host, paced across fronts | 2026-09-28 |
| [0265](0265-a-radancy-front-listing-only-what-another-front-lists-is-an-alias.md) | A Radancy front listing only what another front lists is an alias | 2026-09-28 |
| [0266](0266-a-wp-job-openings-site-is-an-ats-board-read-through-its-rest-route.md) | A WP Job Openings site is an ATS Board, read through its own REST route | 2026-09-28 |
| [0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) | The Space hosts the MCP server at a URL anyone can add (budgets and refusals amended by 0276) | 2026-09-28 |
| [0268](0268-a-served-posting-date-is-never-later-than-first-seen.md) | A served posting date is never later than the day we first saw the Job | 2026-09-29 |
| [0269](0269-every-trends-control-is-answered-from-the-browser.md) | Every Trends control is answered from the browser | 2026-09-29 |
| [0270](0270-the-index-view-takes-a-counting-change-out-too.md) | The index view takes a counting change out too | 2026-09-29 |
| [0271](0271-a-scraper-declares-whether-discovery-keeps-its-slugs-casing.md) | A scraper declares whether discovery keeps its slug's casing | 2026-09-29 |
| [0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md) | An agent reads hiring as postings opened and closed, not the change in openings listed | 2026-09-29 |
| [0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) | A country filter matches every way a location names a country | 2026-09-29 |
| [0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md) | An agent asks `/facets` for the total alone, and names a category in its own words | 2026-09-29 |
| [0275](0275-an-agent-looks-a-company-up-and-reads-its-hiring-profile.md) | An agent looks a company up and reads its hiring profile | 2026-09-29 |
| [0276](0276-a-hosted-mcp-call-ends-at-its-deadline-and-no-caller-holds-every-place.md) | A hosted MCP call ends at its deadline, and no caller holds every place (places amended by 0325, its "still finishing" sentence by 0320) | 2026-09-29 |
| [0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md) | An agent reads a posting by id, and finds jobs like one | 2026-09-29 |
| [0279](0279-the-space-serves-through-waitress-one-process-sixteen-threads.md) | The Space serves through waitress, in one process with sixteen threads | 2026-09-29 |
| [0280](0280-ashby-declares-how-a-link-writes-its-slug.md) | Ashby declares how a link writes its slug | 2026-09-29 |
| [0281](0281-a-lever-board-whose-hosted-pages-are-off-serves-nothing.md) | A Lever Board whose hosted pages are off serves nothing | 2026-09-29 |
| [0285](0285-a-vector-is-rebuilt-when-its-postings-text-changes.md) | A vector is rebuilt when its posting's text changes | 2026-09-29 |
| [0286](0286-a-held-job-whose-text-fails-the-english-gate-leaves-the-index.md) | A held Job whose text fails the English gate leaves the index | 2026-09-29 |
| [0290](0290-a-merge-deploys-the-space-only-when-it-changes-what-the-space-loads.md) | A merge deploys the Space only when it changes what the Space loads | 2026-09-29 |
| [0292](0292-the-description-store-is-not-reaped-until-a-last-listed-signal-exists.md) | The description store is not reaped until a "last listed" signal exists | 2026-09-29 |
| [0293](0293-a-period-less-salary-figure-is-read-by-its-size-only-as-far-as-the-evidence-goes.md) | A period-less salary figure is read by its size only as far as the evidence goes | 2026-09-29 |
| [0298](0298-the-space-runs-only-its-own-scripts-and-google-sign-in.md) | The Space runs only its own scripts and Google's sign-in | 2026-09-29 |
| [0299](0299-a-keyword-word-matches-where-a-word-starts-and-quotes-keep-a-phrase.md) | A keyword word matches where a word starts, and quotes keep a phrase together | 2026-09-29 |
| [0301](0301-a-recruitee-label-that-redirects-to-another-is-an-alias.md) | A Recruitee label that redirects to another is an alias | 2026-09-29 |
| [0302](0302-an-oracle-board-whose-title-names-no-one-is-named-by-its-sites-seo-name.md) | An Oracle Board whose title names no one is named by its site's SEO name | 2026-09-29 |
| [0303](0303-a-zwayam-board-is-read-from-the-api-cluster-that-holds-it.md) | A Zwayam Board is read from the API cluster that holds it | 2026-09-29 |
| [0304](0304-the-index-view-takes-boards-found-out-of-each-line-by-its-own-openings.md) | The index view takes Boards found out of each line by its own openings | 2026-09-29 |
| [0305](0305-an-abstained-row-whose-title-names-a-developer-is-software-engineering.md) | An abstained row whose title names a developer is software engineering | 2026-09-29 |
| [0306](0306-a-hidden-family-is-counted-and-folded-into-other-never-listed.md) | A hidden family is counted and folded into Other, never listed | 2026-09-29 |
| [0307](0307-a-taleo-section-its-twin-host-mirrors-is-buried-onto-the-linked-host.md) | A Taleo section its twin host mirrors is buried onto the linked host | 2026-09-29 |
| [0308](0308-facet-counts-under-a-keyword-run-over-its-rows-read-once-into-memory.md) | Facet counts under a keyword run over its rows, read once into memory | 2026-09-29 |
| [0309](0309-hiring-nows-rate-ranks-only-companies-that-grew.md) | Hiring now's Rate ranks only companies that grew | 2026-09-29 |
| [0320](0320-a-description-keywords-rows-are-found-once-literal-first-and-named-by-row-id.md) | A description keyword's rows are found once, literal first, and named by row id | 2026-09-29 |
| [0321](0321-an-agent-reads-hiring-now-by-opened-less-closed-and-every-trend-says-its-turnover-span.md) | An agent reads Hiring now by opened less closed, and every trend view states its turnover span first | 2026-09-29 |
| [0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md) | A category spans the whole index, and an agent filters by age, required experience and employer | 2026-09-29 |
| [0323](0323-an-agent-sees-one-posting-once-under-a-company-name.md) | An agent sees one posting once, under a company's name, and a company's places by country (amended by 0331) | 2026-09-29 |
| [0324](0324-an-agent-reads-what-a-roles-postings-ask-for-counted-over-a-sample.md) | An agent reads what a role's postings ask for, counted over a sample | 2026-09-29 |
| [0325](0325-the-model-retries-an-edge-failure-a-scan-runs-alone-and-the-eval-waits-for-its-server.md) | The model retries an edge failure, a scan runs alone, and the eval waits for its server | 2026-09-29 |
| [0330](0330-trends-are-recomputed-from-recorded-job-facts-whenever-a-rule-changes.md) | Trends are recomputed from recorded Job facts whenever a rule changes | 2026-09-29 |
| [0331](0331-a-copy-needs-one-companys-words-and-a-missing-id-gets-one-account.md) | A copy needs one company's words, and a missing id gets one account of why (amends 0323) | 2026-09-29 |
| [0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md) | A requirements sample counts each requisition once, under its directory name | 2026-09-29 |
| [0333](0333-visa-sponsorship-and-relocation-are-read-from-descriptions-by-rules-at-query-time.md) | Visa sponsorship and relocation are read from descriptions by rules, at query time | 2026-09-29 |
| [0334](0334-connecting-is-counted-apart-and-the-eval-judges-truth-not-one-path.md) | Connecting is counted apart, and the eval judges truth, not one path | 2026-09-29 |
| [0335](0335-an-agent-leaves-out-staffing-firms-and-job-boards-and-an-unchecked-agency-name-is-flagged.md) | An agent leaves out staffing firms and job boards, and an unchecked agency name is flagged | 2026-09-29 |
| [0336](0336-a-categorys-turnover-is-one-figure-in-every-view.md) | A category's turnover is one figure in every view (amends 0227) | 2026-09-29 |
| [0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md) | A derived field reads no company history, and says what it annualised | 2026-09-29 |
| [0338](0338-a-sorted-query-orders-only-close-matches-and-the-tools-agree-on-age-and-employer.md) | A sorted query orders only close matches, and the tools agree on age and employer (amends 0074, 0331) | 2026-09-29 |
| [0340](0340-the-employment-type-flags-read-the-title-and-more-raw-values.md) | The employment-type flags read the title and more raw values | 2026-09-29 |
| [0341](0341-a-job-that-states-no-employment-type-is-full-time-to-the-filter.md) | A job that states no employment type is full-time to the filter | 2026-09-29 |
| [0342](0342-the-sponsorship-eval-judges-apart-from-the-spaces-rules.md) | The sponsorship eval judges apart from the Space's rules (amends 0334) | 2026-09-29 |

*ADR-0037 was removed from the repository on 2026-09-24 by the owner's decision; its number is not
reused.*
