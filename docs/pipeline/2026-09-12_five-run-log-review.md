# Five completed pipeline executions — log review, 2026-09-12

The two biggest findings are **a widespread Workday ingestion outage hidden inside successful runs** and **a concurrent compaction that prevented the last run from publishing its index**. There are also reproducible iCIMS parsing gaps, Zoho pages that explicitly say jobs are unavailable, persistent HCLTech missing pages, and expensive repeated retries.

This report explains what happened, what it means for users, what the evidence proves, and what to improve. It is an investigation, not a change to production code. No pipeline was dispatched, no data was deleted, and no scraper or workflow was modified for this review.

**Scope and completeness.** The user explicitly chose the latest five **completed executions**, excluding the active run and cancellations that never executed the pipeline. The selection was fixed when the review began; later executions do not move the window. Times below are UTC; add 5 hours 30 minutes for IST. All five had one attempt.

| Run | Created UTC, September 12 | Result | Execution wall time | Commit |
|---|---|---|---:|---|
| [34673097104](https://github.com/sarthakjain004/headstart/actions/runs/34673097104) | 04:28:39 | success | 42.6 min | `d864a8c` |
| [34674940258](https://github.com/sarthakjain004/headstart/actions/runs/34674940258) | 05:11:21 | success | 44.4 min | `d864a8c` |
| [34676823343](https://github.com/sarthakjain004/headstart/actions/runs/34676823343) | 05:55:44 | success | 47.2 min | `2c3b944` |
| [34678849986](https://github.com/sarthakjain004/headstart/actions/runs/34678849986) | 06:43:01 | success | 41.4 min | `2c3b944` |
| [34680651554](https://github.com/sarthakjain004/headstart/actions/runs/34680651554) | 07:24:28 | **failure** | 43.8 min | `2c3b944` |

There were **115 job records: 110 jobs with logs and five skipped `request-compaction` jobs**. Every available job log was downloaded successfully: **127,269 raw lines, 13,523,934 bytes**. The inventory includes SHA-256 checksums. It covers setup, dependencies, WARP, planning, all 75 scrape shards, join, all 15 embedding shards, merge, uploads, housekeeping, chaining, and post-job cleanup. Every job's step conclusions were inspected. Only one step had a failure conclusion: the last run's `Upload index state`.

Raw logs are retained. Analysis uses a second copy with GitHub's cyan workflow-source echo removed: otherwise a script that *mentions* an error would be counted as an error that happened. Application summaries were cross-checked against individual events. Specifically, **all 4,533 per-Board failure lines reconcile exactly to the five run-level error totals**. This is more complete than reading the Actions annotations, which intentionally sample repeated errors.

The complete evidence is under [`experiment/pipeline-review-2026-09-12/artifacts/`](../../experiment/pipeline-review-2026-09-12/artifacts/). Start with the [log inventory](../../experiment/pipeline-review-2026-09-12/artifacts/log-inventory.json), [complete Board error catalog](../../experiment/pipeline-review-2026-09-12/artifacts/board-error-catalog.md), and [all 4,533 Board error events](../../experiment/pipeline-review-2026-09-12/artifacts/board-errors.json). Each event names its run, job, Board, duration, exception, message, and cleaned-log line. The catalog groups every exception/message combination, rather than listing only the largest few.

Local working files were changing during the review. Source interpretation therefore uses an extracted, **pinned `2c3b944` source tree**, not the working tree. The change between the two run commits adds seven single-company scrapers and an Eightfold fallback, plus associated documentation and tests. **Workday's scraper and the HTTP transport did not change between these two commits.** The timing of the Workday incident alone therefore does not establish that this commit caused it.

**How to read the pipeline.** A Board is one company's ATS-hosted set of jobs. A scrape shard processes a subset of Boards. `join` combines the shards, keeps tech roles, reconciles stored descriptions, updates planning ledgers, and plans embedding. Embedding converts text into vectors used for semantic search. `merge` combines vectors, refreshes metadata, changes the searchable LanceDB table, and uploads the results. A local `index done` message means the runner built a table; it does **not** mean users received it. Publication is a later step.

**1. Workday suffered a major observation failure while two affected runs stayed green.**

| Run | All Board errors / 20,000 attempts | Workday errors / Workday attempts | Workday JSON decode errors | Workday raw jobs returned | Workday tech jobs returned |
|---|---:|---:|---:|---:|---:|
| 34673097104 | 52 / 20,000 | 25 / 2,649 — 0.9% | 1 | 519,330 | 82,592 |
| 34674940258 | 51 / 20,000 | 32 / 2,576 — 1.2% | 1 | 513,188 | 82,731 |
| 34676823343 | 964 / 20,000 | 951 / 2,694 — 35.3% | 502 | 470,112 | 82,075 |
| 34678849986 | 2,171 / 20,000 | 2,158 / 2,670 — **80.8%** | 2,151 | 75,234 | **10,155** |
| 34680651554 | 1,295 / 20,000 | 1,279 / 2,563 — 49.9% | 1,257 | 207,273 | 28,603 |

The Workday denominators are summed from all 15 shards' logged assignment mixes in each run. Every shard included Workday in that mix, so these are complete counts. Different runs choose different Boards, but an 80.8% failure rate, all-shard occurrence, and an approximately 87.7% fall in tech output versus the second run cannot reasonably be described as an ordinary change in the chosen slice.

A JSON decode error means the server response could not be read as JSON. JSON is the structured data format this listing endpoint promises. The stack traces show `fetch_raw → _exhaust → _post → response.json()`. In the fourth run this often fails on the very first listing request, before any job list is available. The median recorded duration of a JSON-failing Board is **one second** in each of the last two runs. These are fast failures, not successful speed improvements.

The affected data centers also change over the window. In run three the JSON errors split `wd1=192, wd3=158, wd5=152`; in run four, `wd1=996, wd3=603, wd5=552`; in run five, `wd1=927, wd5=329, wd103=1`. This is a broad incident whose shape varies with time, rather than one bad tenant slug. Run three also contains **434 Workday HTTP errors**, followed by the much larger JSON-error phase.

**What is confirmed:** the listing decode failed, affected thousands of Board attempts, and reduced fresh coverage. **What is not confirmed:** the original response body, content type, final redirect URL, or upstream reason. The logs do not preserve those fields. An outage/maintenance response, a challenge page, and an egress-dependent response remain possible explanations. A JSON error alone does not prove which one it was.

**Live verification:** at approximately 08:38–08:39 UTC, nine previously JSON-failing samples, three each from `wd1`, `wd3`, and `wd5`, all returned HTTP 200 with valid JSON using the pinned project's HTTP client and request headers. An additional Airbus sample returned valid JSON. Two invalid-site/tenant controls returned structured 404/422 JSON. One intended control used a nonexistent Genpact site spelling; its 404 is a probe-input result, not evidence that the real Genpact Board is dead. The original outage therefore did not reproduce in that later local sample. This establishes later reachability, **not recovery of every Actions runner or the complete Workday population**. See [live Workday probes](../../experiment/pipeline-review-2026-09-12/artifacts/live-workday-probes.jsonl).

**Why a green run is possible:** the pipeline isolates a failed Board and continues with the rest. That is useful: a bad employer endpoint should not stop all employers. However, there is currently no corresponding run-quality verdict that makes a provider-wide loss prominent. The normal run conclusion says that orchestration finished, not that coverage was healthy.

**Improvement:** preserve a bounded diagnostic sample for unexpected listing bodies: status, content type, final URL, byte length, response fingerprint, and a short sanitized body prefix, grouped by ATS/data center and time. Recognize a confirmed maintenance/challenge response as a specific observation failure. Do not convert it to an empty Board. Add an explicit degraded-coverage summary based on attempted/successful Boards per ATS, alongside publication status. Retry or defer a confirmed transient response within a bounded policy; do not blindly retry every JSON parsing error or quarantine these Boards as gone.

The existing safeguards prevented this from becoming a mass deletion. A Board that raises before producing a list is outside that run's fresh eviction scope. That preserves its previously indexed jobs, but also delays detecting new jobs and actual closures. The **32** scope-excluded Boards logged by the fourth merge are *not* the full **2,158** failed Workday Boards: those numbers describe different mechanisms. The first counts Boards that supplied a known partial result; a wholly absent Board need not appear in that count.

**2. The failed run was stopped by the write guard after compaction changed its base.**

The overlapping [compaction run 34681374693](https://github.com/sarthakjain004/headstart/actions/runs/34681374693) was downloaded separately to explain the failure. Its three logs are supplemental evidence, not additional pipeline executions in the five-run sample.

| UTC | Event |
|---|---|
| 07:24:28 | Pipeline 34680651554 begins. |
| 07:41:50–07:54:56 | Compaction repeatedly reports one pipeline in flight. |
| **07:55:56** | Compaction reports `no pipeline in flight — compacting`, although this pipeline has not finished. |
| 07:57:51 | Compaction fingerprints 1,918 LanceDB files. |
| 07:59:40 | Compaction opens the last published table: **415,083 rows**. |
| 08:00:45 | Pipeline's merge records its LanceDB base before fetching. |
| 08:03:09 | Compaction's guard still sees its original base and allows its upload. |
| 08:03:48 | Compaction publishes the rebuilt table. |
| 08:06:48 | Pipeline finishes a local table containing **416,088 rows**. |
| 08:08:07 | Pipeline successfully uploads its embedding store. |
| **08:08:08** | Pipeline's LanceDB guard detects the changed remote directory and refuses its index upload. |

The guard reports **4 files added, 1,913 removed, 4 modified; 1,918 files became 9**. That exactly matches a compaction replacing many small table files with a rebuilt table. The failure is intentional protection, not an embedding crash or an arbitrary upload timeout. [Failed merge log](../../experiment/pipeline-review-2026-09-12/artifacts/34680651554/103522602987.log); [compaction window](../../experiment/pipeline-review-2026-09-12/artifacts/compaction-window.log); [compaction cleanup](../../experiment/pipeline-review-2026-09-12/artifacts/compaction-cleanup.log).

The compaction's filtered Actions poll gave a false zero at the relevant moment. The logs establish that bad observation; they do not establish GitHub's internal reason for it. Replacing that poll with a slightly longer sleep would not make the write safe. The content guard did the useful work: it prevented an older-base writer from undoing another writer's table changes.

**Publication consequence:** the final run's **1,175 additions, 170 evictions, and net +1,005 rows were local only**. The LanceDB upload did not occur. The later description-store upload, `data/state` upload, and Space restart did not occur either. Embeddings had already uploaded successfully. The result is partial publication across directories, not a successfully served 416,088-row table. The chain still dispatched a successor; this review does not claim that successor recovered all the missing changes, because its outcome lies outside the selected completed window.

**Improvement:** retain the guard. Add a cheap conflict check before the large embedding upload to reduce wasted work, while retaining the check immediately before the index write. Report a separate publication receipt for each directory and a final complete-publication verdict. On a conflict, rebuild against the new base in a bounded retry or leave a clear deferred-publication outcome. Never retry the stale index upload unchanged.

There is also a remaining design limitation: the current implementation is **check, then upload**, with separate calls. It is not an atomic compare-and-swap operation despite informal terminology in its documentation. A writer could change the remote data between those calls. That race is a source-level limitation, **not a second observed overwrite in this window**. A durable solution needs serialized writers or a server-enforced conditional commit that binds validation to publication. Those are architectural choices to evaluate before implementation, not a reason to remove the existing protection.

**3. HCLTech combines the main time bottleneck with persistent ambiguous missing pages.**

The scrape stage owns the largest part of total wall time in all five runs. Its slowest Board is Oracle `ejwl` in the first run and HCLTech in the other four. HCLTech takes **839, 1,140, 1,091, 1,053, and 1,126 seconds** across the five runs. In runs where it is the straggler, one Board occupies approximately **93–96% of its shard's wall time**. Moving smaller Boards between shards cannot make that one Board finish sooner.

HCLTech's sitemap advertises about 11,160–11,170 job pages. Each run records **1,332 missing-title detail pages**, except the fourth with 1,335. The cause is specifically **HTTP 200 without a parseable title**. It is not logged as a connection timeout. Because that is a substantial missing portion of the listing, the scraper marks the Board unauthoritative. Its missing indexed rows are then protected from eviction.

HCLTech alone accounts for **1,729 → 1,730 → 1,731 → 1,734 → 1,736** protected eviction-candidate rows. Across all Boards, the corresponding totals are **2,309 / 2,226 / 2,364 / 2,162 / 2,243**. HCLTech therefore explains most of this persistent protection. Protected does not mean confirmed live, and these numbers are not counts of proven dead jobs.

**Live verification:** a deterministic random sample of 30 HCLTech sitemap entries produced 29 parseable job titles and one HTTP 200 page with no title. The failing page explicitly says: **“You can't view this job because it's not available at this time.”** The sitemap still links it. This proves that *at least one* missing-title case is an unavailable-job page, rather than a parser failing to find a real title. It does **not** prove that all 1,332 failures have the same cause, or that the sampled job is currently in the served index. [Sample results](../../experiment/pipeline-review-2026-09-12/artifacts/live-hcl-probes.jsonl); [captured unavailable page](../../experiment/pipeline-review-2026-09-12/artifacts/hcl-missing-title-1364236555.html).

**Improvement:** separate explicit unavailable-job pages from transport failures and genuinely unknown HTML. Preserve the listed identity and a per-job detail outcome so repeated unavailable pages can be evaluated without making the entire Board permanently exempt from eviction. Use repeated observations and a representative sample before changing closure semantics. For speed, profile the detail pass and consider caching stable fields or safely splitting this large Board into resumable units. Do not park HCLTech merely because it is slow: it contributes roughly 2,622 tech jobs in the last run, so coverage is valuable.

**4. iCIMS has a reproducible HTML-without-JSON-LD parsing gap.**

The five runs log **11,501 missing iCIMS detail-field events**. This is a count across executions, including repeated failures, not 11,501 distinct jobs. Large examples include `teamwork-ovg` and `teamwork-spectra`, each losing **1,527/1,527** fields when selected, and `careers-comcastspectacor`/`careers-ovg`, losing **1,385/1,385**. These whole-Board content losses do not raise a Board exception; they are visible in the detail-loss logs and can be missed by reading only the error totals.

The known `in_iframe=1` requirement is already present in the executed code. **It is not the missing fix here.** Live tests requested the correct iframe URL. Six pages from three affected hosts returned HTTP 200 with no parseable JSON-LD. Two pages from the `careers-empowerai` control returned parseable JSON-LD.

Crucially, the affected pages contain real HTML job content. An OVG sample has an `h1.iCIMS_Header` title and `iCIMS_InfoMsg_Job` sections for Overview and Responsibilities. The current parser only reads JSON-LD, so it discards content that is actually present. That is a verified parser coverage gap. The sampled roles were not all tech roles; the recoverable tech count still needs measurement. [Live detail probes](../../experiment/pipeline-review-2026-09-12/artifacts/live-detail-probes.jsonl); [example OVG HTML](../../experiment/pipeline-review-2026-09-12/artifacts/icims-teamwork-ovg.icims.com-sample-0.html).

A related duplication clue is also verified: `careers-comcastspectacor.icims.com/sitemap.xml` redirects to `careers-ovg.icims.com/sitemap.xml`. This is a concrete alias candidate. Similar counts on other OVG/Spectra hosts alone are not sufficient to declare them identical Boards; compare canonical destinations and complete job identity sets first.

**Improvement:** add a narrowly scoped HTML fallback for title, description and other explicitly present fields when JSON-LD is absent. Test it against the captured failing pages and working JSON-LD controls. Measure tech yield before estimating benefit. Separately validate the observed redirect alias to avoid fetching the same Board twice.

**5. Zoho's missing-detail bucket includes explicit unavailable-job pages.**

There are **13,821 missing Zoho detail-page events** across the five runs. `westlakesrecruit` loses **348/348** on every run; `resourceit` loses **407/407** when selected in run three. Most losses say `no jobs blob on the page`, which means the page lacks the embedded job-data field the parser expects.

Live testing checked two detail URLs from each affected Board and two from AgileEngine as controls. All four affected requests returned HTTP 200, but only about 2.2 KB of HTML. Westlakes says **“This job posting is no longer available.”** ResourceIT says the equivalent in Portuguese. Both listings still advertise those identities. The two AgileEngine detail pages, around 1.7 MB each, parsed successfully. These four samples therefore demonstrate a mismatch between listing presence and detail availability, not a generic JSON/HTML decoder failure.

The scraper currently falls back to the listing record when a detail is absent. That preserves useful records during temporary detail failures, but it also means an explicit unavailable-job page is treated like an ordinary missing description. The review has **not** checked whether these exact sampled identities are in the live English search index; the source listings and their detail responses are the verified evidence.

**Improvement:** distinguish an explicit unavailable-page outcome from a transient fetch loss and an unfamiliar template. Track it per job, and decide how repeated evidence should affect retention and retry frequency. Do not drop all 348 or 407 jobs based on four samples. Preserve transient failures as unknown so an origin outage cannot become a mass delisting. [Captured Westlakes page](../../experiment/pipeline-review-2026-09-12/artifacts/zoho-westlakesrecruit.zohorecruit.com-sample-0.html); [ResourceIT page](../../experiment/pipeline-review-2026-09-12/artifacts/zoho-resourceit.zohorecruit.com-sample-0.html).

**6. Oracle's large Boards hit a real offset boundary and also lose details.**

The last merge reports `ejwl` reading **9,926 of 13,651** requisitions and `eluq` **9,975 of 11,807**, with the remaining portion unreachable past offset 10,000. The difference between 10,000 raw slots and the unique returned counts is why these numbers should not be rounded to “exactly 10,000 jobs scraped.”

Live probes issued nine requests: offsets 0, 9,800, and 10,000 on `ejwl`, `eluq`, and a smaller `hcbt` control. At offset 9,800, the two large Boards still returned 200 records with totals above 10,000. At offset 10,000 they returned **HTTP 200 with zero records and `TotalJobsCount=0`**. The smaller control was already exhausted at 9,800. This verifies a silent boundary in this sample; it is not a normal empty remainder for the two large Boards. [Live Oracle probes](../../experiment/pipeline-review-2026-09-12/artifacts/live-oracle-probes.jsonl).

Oracle also logged **9,517 missing detail-payload events**. These are separate from the listing boundary. `ejwl` loses 685–1,567 details per execution; `ejwl-dev7` loses 409–1,868. Most reasons are logged only as `HTTPError`, losing the actual HTTP status. That is too coarse to distinguish an absent requisition from server failure or throttling.

**Improvement:** investigate server-supported query subdivision and verify that the union covers the full Board without omissions. Preserve the current unauthoritative marking until that is demonstrated. Record HTTP status in Oracle's per-detail loss classification, as Workday already does. Do not exclude `-dev` or `-test` hosts merely because of their names: prior investigation already withdrew that generalization, and these names alone prove nothing about content validity.

**7. Retries remain expensive, and the egress heuristic misreads this outage.**

| Run | Logged retry events | Network | 429 | 5xx | 403 | 405 |
|---|---:|---:|---:|---:|---:|---:|
| 34673097104 | 128,044 | 55,006 | 30,598 | 19,570 | 22,632 | 238 |
| 34674940258 | 129,562 | 57,690 | 30,704 | 17,589 | 23,430 | 149 |
| 34676823343 | 124,962 | 47,273 | 33,433 | 21,523 | 22,578 | 155 |
| 34678849986 | 56,577 | 4,908 | 24,215 | 5,349 | 21,932 | 173 |
| 34680651554 | 82,270 | 21,302 | 29,230 | 8,995 | 22,604 | 139 |

Total: **521,415 retry events**. These are retry counters, not distinct failed jobs or Boards. A request can retry and then succeed. Avoid adding this number to the 4,533 terminal Board failures.

The retry analyzer labels 10 of 15 shards in run four as looking like direct egress because its `429/network` ratio is high. None logged startup degradation to direct access. Actual application logs show substantial spare-egress traffic: in that run, Workday reports **68,487/68,503 walled requests rescued** across 14 reporting shards, and Eightfold **18,334/18,362** across all 15. A high ratio during a collapse in Workday work is not proof that WARP vanished. The status-based rescue counter also does not guarantee valid JSON; a non-wall response can still fail parsing.

The five runs log **978 / 1,157 / 894 / 280 / 597 successful egress rotations**. There are five daemon-restart-unavailable messages in the first run, across two shards, with two full tracebacks/annotations and subsequent repeated messages logged more quietly. Those runs continued. All 75 scrape setups also print a `warp-taskbar.service` startup failure, but the scrape work and spare-egress usage proceed. The taskbar is the desktop integration, not evidence by itself that the network daemon is unusable.

**Improvement:** correlate transport retries with actual successful parsed responses and terminal failures, by ATS and data center. Treat the ratio-based egress verdict as a heuristic. Profile a small, controlled sample before changing concurrency: this window contains server errors, HTML decode failures, and rate limits, which require different remedies. Reduce unhelpful taskbar setup noise if it can be done without affecting the daemon.

Eightfold deserves a separate detail-rate experiment. Its detail-loss logs record **32,098 missing-description events** across the five runs, beyond the very small number of whole-Board exceptions. These are requests for descriptions still needed; the code excludes already-held descriptions before this pass. Citi alone misses **910/2,298** requested descriptions in the final run, with **907 HTTP 429** and three HTTP 405 results. The stored description layer restores many tech descriptions at join, so this is not a claim that all those jobs reached search without descriptions. It is evidence of repeated ineffective fetching. Run a per-host width/backoff comparison and measure successfully recovered *new* descriptions, not just request throughput.

**8. Partial listing and detail losses need separate accounting.**

| Run | Workday failed listing pages | Workday missing details | iCIMS missing fields | Zoho missing details | SuccessFactors missing fields | Oracle missing payloads |
|---|---:|---:|---:|---:|---:|---:|
| 34673097104 | 53 | 5,468 | 3,160 | 2,314 | 1,451 | 3,019 |
| 34674940258 | 25 | 1,460 | 4,579 | 1,967 | 1,936 | 1,661 |
| 34676823343 | 37 | 19,422 | 1,729 | 2,547 | 1,832 | 1,950 |
| 34678849986 | 0 logged | 56 | 1,938 | 4,673 | 1,574 | 908 |
| 34680651554 | 13 | 1,022 | 95 | 2,320 | 1,520 | 1,979 |

These totals are emitted loss events, not unique jobs or additional Board errors. Workday's low fourth-run detail loss is misleading if read alone: most affected Boards failed before reaching details. A detail not attempted because the listing failed cannot appear as a failed detail.

Workday's 128 failed listing pages across 60 Board/run occurrences are attributed to server errors, connection errors, and one 429. In contrast, run three's 19,422 missing details include **16,810 skipped after the sustained-5xx break-off**. A skipped detail is deliberately unattempted after the circuit breaker activates; it is not 16,810 separate HTTP responses. That break-off bounds wasted effort during an origin failure. The useful next signal is how many tech descriptions the store could restore and how many remain unknown.

SuccessFactors `jobs.nutrien.com` shows **363/541** and **378/541** missing fields in runs two and three, overwhelmingly HTTP 429. That points to a host-specific throttling experiment; it is a different failure from HCLTech's HTTP-200 unavailable pages.

The [detail-loss event file](../../experiment/pipeline-review-2026-09-12/artifacts/detail-loss-events.json) preserves every matched Board, count and cause. The [secondary event file](../../experiment/pipeline-review-2026-09-12/artifacts/secondary-events.json) preserves fallback, cap, and partial-crawl evidence. Neither should be treated as the full Board-error list; that is maintained separately.

**9. Recurring fallback and capped-surface issues.**

| Signal | Counts in the five runs, chronological | Interpretation |
|---|---|---|
| Freshteam HTML soft-404 listing instead of widget JSON | 10 / 14 / 13 / 13 / 11 | A well-formed HTTP response can still be the wrong page. Current logs classify it separately from an exception. |
| Freshteam at the 1,000-job widget cap | 2 / 2 / 2 / 2 / 3 | Known surface ceiling; reaching it is evidence of possible omission, not proof of the exact missing count. |
| Zoho at the roughly 750-record ceiling | 5 / 5 / 6 / 7 / 6 | Same limitation. AgileEngine's live listing sample also returned 750 records. |
| Trakstar capped HTML recovered by RSS | 26 / 29 / 31 / 25 / 30 | **Successful recovery**, not a failure. |
| Trakstar RSS unreachable, keeping capped HTML | 2 / 2 / 3 / 2 / 2 | The fallback did not recover the full surface; examples include `tenmilestech`, `bracusa`, and `fdhconsulting`. |
| Zwayam homepage unread, assumed job-link route | 8 / 8 / 11 / 6 / 9 | Jobs may be obtained while routing detection fails. The assumed link generation needs validation. |
| Eightfold falls back to sitemap | 16 / 17 / 0 / 0 / 0 | The new commit changes its available fallback paths. Absence of this line after deployment is not proof that all upstream refusals disappeared. |
| Eightfold converged only after another sweep | 2 / 1 / 2 / 0 / 3 | Extra listing passes recovered data; one final-run Citi crawl needed sweep three. |

**Improvement:** distinguish “recovered,” “partial,” and “unavailable” in the run summary. For fixed ceilings, first investigate a complete supported surface. For Trakstar, retain a partial-list outcome when RSS fails. For Zwayam, retain/cache a previously verified route and validate fallback-generated links against real routing; do not silently assume every frontend uses the same spelling. Re-probe persistent Freshteam soft-404 Boards before treating them as genuinely empty or gone. These are source observations; the review did not independently verify every capped Board's true total or every Zwayam generated URL.

**10. Complete terminal Board-error accounting.**

Across 100,000 Board attempts there are **4,533 failure events on 3,506 distinct raw Board keys**, grouped into six exception classes and 37 exact ATS/exception/message combinations. Raw keys may contain aliases; this is not a claim of 3,506 canonical distinct Boards. **Workday contributes 4,445 events, approximately 98.1% of the total.**

| Exception | Events | Main meaning in this window |
|---|---:|---|
| JSONDecodeError | 3,912 | All Workday; non-JSON listing responses. |
| HTTPError | 541 | Returned failure statuses; Workday dominates. One Zwayam event carries an HTTP/2 stream-reset message instead of an ordinary status. |
| ConnectionError | 36 | 34 Workday and two Eightfold; mostly abruptly closed connections, plus several local-proxy connection failures. |
| CertificateVerifyError | 22 | All SuccessFactors; expired certificates or hostname mismatch. |
| Timeout | 21 | 14 SuccessFactors and seven Workday. |
| RequestException | 1 | Lever `zuru`, explicitly reporting no Board/404. |

The non-Workday ATS totals are: SuccessFactors 36; Freshteam 11; Oracle eight; Greenhouse seven; SmartRecruiters five; Ashby four; Darwinbox four; iCIMS three; Workable two; Recruitee two; Eightfold two; Teamtailor two; Lever one; Zwayam one. These sum to the remaining 88 events.

For HTTP-status detail, Workday contributes 445 HTTP 500s, 22 HTTP 422s, 16 HTTP 403s, eight HTTP 502s, and one HTTP 504. Other recurring classes include Freshteam 500, Oracle 503, SmartRecruiters 401, iCIMS/Recruitee 403, and Greenhouse/Ashby/Workable/Teamtailor 404. The full catalog includes their exact Boards and messages. Do not label a 401/403/422 Board “dead” merely because it failed; absence, access denial, tenant migration, and server failure are different observations.

The 14 SuccessFactors timeouts all occur in the first run's scrape shard 10. They are approximately 30-second connection timeouts across different hosts. That concentration is consistent with a shared runner/network incident, but the logs do not prove which network component caused it. Later local checks of three examples found Idemia and Swiss Re returning valid sitemaps; EssilorLuxottica delivered 4.37 MB before the probe's shorter 12-second deadline. That last result is not a reproduction of the original no-connection timeout.

The 22 certificate errors involve 15 hosts. Live rechecks reproduced certificate verification errors on **14 of those 15**; the remaining `catl-career.com` probe timed out. This is strong evidence that these are real host certificate problems, rather than a pipeline-wide CA installation failure. **Do not disable TLS verification.** Revalidate the canonical careers hostname, record the reason, and avoid aggressive repeated retries of deterministic certificate errors. [Certificate and timeout probes](../../experiment/pipeline-review-2026-09-12/artifacts/live-secondary-probes.jsonl).

The gone-strike ledger recognized **2 / 4 / 2 / 3 / 5** errors as 404/410-shaped gone responses. The standing quarantined total was **704 / 706 / 706 / 706 / 706**. These are stock counts, not hundreds of new quarantines each run. The Workday outage correctly did not quarantine thousands of Boards. The final run's newly calculated ledger was not published because publication aborted.

**11. The cost model can learn that an outage is cheap.**

`harvest.run_one` measures elapsed time in `finally`, including when fetching raises. Later `writer.record_cost` writes that time with zero returned jobs for a failed Board. `board_cost.update` blends the measurement into the previous estimate with weight 0.5. Only an unfinished/budget-killed Board receives special lower-bound treatment; an ordinary failed fetch has no separate outcome in the cost-row schema.

In plain terms, a Board that normally takes 120 seconds but immediately fails in one second can get an estimated cost of **60.5 seconds** after one observation, then **30.75 seconds** after another. That is an arithmetic illustration of the executed code, not a measurement of a particular Board's stored cost. It is accurate as a record of the failed attempt's duration, but potentially misleading as a forecast of the next healthy crawl.

The planner's serial-work estimate falls from **2,455.4 minutes in run four to 1,998.9 in run five**. Workload mix and the fan-out speedup estimate also change, so the entire reduction cannot be attributed to failed observations alone. Nevertheless, the code has a confirmed path by which fast failures lower the estimate. The fourth run's median actual/predicted scrape ratio is 0.67; celebrating that as a speedup would reward missing work.

**Improvement:** distinguish successful-crawl cost from failed-attempt cost. Preserve both if both are useful, and use a forecast appropriate to a healthy attempt. Assess this using the same Board identities before, during, and after an incident, rather than changing the EWMA weight from aggregate timing alone. Keep the existing unfinished-Board bound.

**12. Most remaining wall time is join and data transfer, not embedding.**

| Run | Scrape maximum | Scrape summed work | Join | Embed maximum | Merge |
|---|---:|---:|---:|---:|---:|
| 34673097104 | 17.2 min | 194.7 min | 13.7 min | 2.4 min | 8.6 min |
| 34674940258 | 19.8 min | 198.2 min | 13.9 min | 2.3 min | 7.0 min |
| 34676823343 | 18.9 min | 204.4 min | 13.8 min | 4.4 min | 9.2 min |
| 34678849986 | 18.9 min | 143.9 min | 11.2 min | 3.0 min | 7.3 min |
| 34680651554 | 19.5 min | 165.9 min | 11.2 min | 4.1 min | 8.1 min |

The maximum is how long the parallel stage makes the next stage wait. The sum is how much runner time all shards consume together. They answer different questions. Scrape max/mean is approximately **1.32 / 1.50 / 1.39 / 1.97 / 1.76**. The widening in the last two runs is partly many Workday Boards failing quickly while HCLTech remains slow.

The planner already predicts a large single-Board floor on every run, at roughly 16.6–17.5 minutes. This is not a newly discovered packing failure. Across successful early runs, median scrape actual/predicted is 0.94–1.02; the final run is 1.07. The queue/setup remainder between stage maxima and execution wall time is small, about 0.2–0.8 minutes.

Inside join, **uploading corpus/state costs 156–224 seconds**, the tech filter **122–159 seconds**, description reconciliation **62–81 seconds**, and embed planning **89–117 seconds**. Several ledger stages each scan data again. In merge, fetching prior state/LanceDB costs **101–202 seconds**, downloading the corpus/state artifact **49–83 seconds**, metadata refresh **31–43 seconds**, and index sync **63–96 seconds**. There is no derivation-version sweep in any run: all say version 9 stored and version 9 in code.

**Improvement:** first measure artifact bytes and compression time separately from upload time; avoid attributing the entire transfer step to the network. Profile repeated corpus/metadata scans to find shared work worth eliminating. Consider an earlier, bounded state prefetch where consistency can be preserved. Do not add more embedding shards as the first response: embedding is already only 2.3–4.4 minutes on the critical path.

All **1,973 planned Docs were embedded**, across 15 CPU shards, with **zero failed embeddings** and all expected fragments arriving. Counts per run are 41 / 46 / 870 / 93 / 923. The third and fifth runs process newly introduced company content; their larger embedding workloads are not evidence of an embedding regression. Individual embed job actual/predicted ratios range from 0.62 to 1.17, and no allocator-wedge or failed-batch incident appears.

**13. Corpus, metadata, churn and storage need careful interpretation.**

| Run | Local index add / evict | Net rows | Local final table | Was that index published? |
|---|---:|---:|---:|---|
| 34673097104 | 45 / 65 | −20 | 414,498 | yes |
| 34674940258 | 61 / 261 | −200 | 414,298 | yes |
| 34676823343 | 884 / 139 | +745 | 415,043 | yes |
| 34678849986 | 111 / 63 | +40 | 415,083 | yes; eight additions were re-embeddings |
| 34680651554 | 1,175 / 170 | +1,005 | 416,088 | **no** |

All prune stages found zero off-Board and zero duplicate rows. The published row chain reconciles across the first four runs. Compaction also opens 415,083, confirming the last successfully published base before the conflict.

Flapping means an identity was evicted and later added again. The audit was run over the **four published executions**, with identity batches split using the pinned ATS registry and checked against their own batch counts. The unpublished fifth run was not allowed to manufacture user-visible churn. There are **25 re-added identities out of 465 earlier evictions**, and **33/1,056 already-known additions, 3.1%**. The aggregate passes the existing 10% threshold, but **24 of 25 flapped identities belong to `oracle:ejwl-dev7.fa.us2.oraclecloud.com`**. That concentrated Board deserves investigation even with a green aggregate. This is evidence of observed delete/re-add behavior, not proof that every evicted identity was live at its eviction time. [Published flap audit](../../experiment/pipeline-review-2026-09-12/artifacts/published-flap-audit.txt).

The description-gap count rises **42,777 → 107,078** during the fourth run. That does not mean 64,301 stored descriptions were deleted. The gap calculation stops counting old identities as unreachable when their Board was not authoritatively observed. In the same transition, the excluded-as-gone count falls **132,573 → 68,273**, almost exactly the opposite movement. Most of this is a change in what the run could establish about reachability. The final locally calculated gap falls to 93,337, but that state was not published.

Metadata changes include experience values lost **12 / 2 / 92 / 87 / 1** and salary values lost **22 / 2 / 10 / 30 / 12**. These are derivation outcomes, not exceptions. There was no full version sweep. The logs do not identify enough of the before/after input fields to assign a cause to every loss. The first four published runs lose 193 experience derivations and 64 salary derivations; the fifth's 119 experience gains are local and must not be claimed as a published recovery. Add bounded before/after examples to diagnose whether source facts changed, a detail was missing, or extraction changed. Blanket preservation of old values would also be wrong when the employer genuinely changes a requirement.

The role-family model labels about **23.8%** of the table non-tech in every run. That is a model-based classification signal, not a hand-audited false-positive rate. It remains worth sampling, but tightening the recall-biased tech gate solely to improve this percentage could hide valid tech jobs.

Storage reports **79.27 / 82.10 / 82.13 / 82.19 / 82.38 GB used**, while live files are **6.79 / 6.82 / 6.86 / 6.89 / 5.93 GB**. Every run performs a history squash. Used storage includes retained objects whose collection timing is controlled by HF, so repeated high used storage is not proof that squash failed. The final live-size reduction occurs alongside compaction, not a logged loss of table rows. Monitor collection lag, live bytes, and retained bytes separately. The failed run uploaded a roughly **2.31 GB vector file** before detecting the conflict, making earlier conflict detection materially useful.

**14. Logging improvements that would shorten the next investigation.**

The existing first-occurrence traceback suppression is working: 3,912 JSON errors produce 47 full JSON tracebacks rather than thousands. All subsequent failure events remain in the logs. Keep that balance. Warning/notice totals are **26 / 27 / 52 / 37 / 44**, with two additional error lines on the last run. The third run reaches 50 warning lines before its notices; relying on the limited Actions annotation surface would be risky. The complete logs and structured summary should remain authoritative.

The generic tools also need interpretation. The initial merge analyzer used the local registry, which did not know the new ATS names and correctly warned that it could not account for some identity batches. Rerunning with the **executed commit's registry** fixed those dropped batches. A local analyzer's missing provider is not a pipeline data loss. The existing flap tool ordinarily selects successful runs and splits identities on whitespace; this review instead supplies the fixed publication window and a provider-boundary parser with batch-count assertions. These changes were confined to the review harness, not production tooling.

Recommended summary fields are: attempted/successful/failed Boards per ATS; listing versus detail losses; non-JSON response samples; number of missing artifacts versus expected; count and age of rows outside eviction scope; publication status for each directory; and explicit “fresh data degraded” versus “publication failed.” Keep a small standalone telemetry artifact so these fields do not require downloading full scrape fragments.

**Recommended order of work.**

1. **Workday outage diagnosis and degraded-coverage reporting.** Highest measured coverage impact: 80.8% of its selected Boards fail in a green run. First deliverable: body-aware failure evidence and a coverage verdict that catches this exact run shape.
2. **Publication conflict handling.** Preserve the guard; make partial publication explicit; avoid uploading gigabytes before a known conflict; design bounded rebase/recompute behavior. Verify with two writers deliberately sharing a base.
3. **iCIMS HTML fallback.** A concrete, reproducible parser gap with captured positive and negative controls. Verify real title/description recovery and measure tech yield.
4. **Explicit unavailable-page outcomes for Zoho and SuccessFactors.** Separate source-declared unavailability from unknown fetch failure. Verify repeated samples before changing eviction semantics.
5. **HCLTech and Oracle large-Board work.** Address the measured single-Board time floor, permanent scope exclusion, and Oracle query boundary; preserve coverage while experimenting.
6. **Host-specific detail throttling and retry cost.** Start with Eightfold Citi and SuccessFactors Nutrien. Measure recovered descriptions per unit time and request, including failures.
7. **Outcome-aware planning costs and clearer metadata evidence.** Prevent fast failures from masquerading as healthy cost improvements; add before/after examples for derived-field loss.
8. **Persistent certificate/alias/cap maintenance and transfer profiling.** Repair verified host mappings, keep TLS validation, investigate complete source surfaces, and target measured join/merge costs.

Acceptance should be based on preserved or improved coverage, correct publication and bounded cost—not merely a faster green workflow. The unresolved upstream cause of the historical Workday body change, the full population of unavailable HCL/Zoho pages, and the exact served status of those sampled identities remain explicitly open. The evidence above is sufficient to prioritize and reproduce the confirmed gaps without pretending those remaining questions have already been answered.
