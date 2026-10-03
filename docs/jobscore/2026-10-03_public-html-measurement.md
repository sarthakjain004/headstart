# JobScore public HTML measurement — 2026-10-03

## Surface and identity

Use the public board `/careers/{label}` and its posting links. Robots permits those paths
and forbids `/apply_flow/`, which is never requested. The
[published feed guide](https://support.jobscore.com/hc/en-us/articles/202001320-Developers-Guide-to-Job-Feed-APIs)
asks for at most hourly polling. A planner-only delay cannot cover direct scrapes or a failed
telemetry write, so production makes **no feed requests** and needs no new polling state.
The page's Atom link is parsed only for its canonical label.

The sitemap advertised 498 labels; four Common Crawl labels added three aliases and a retired
Board. Public HTML confirmed the same initial 498 live / four dead results in 84.9 seconds at
two workers. Predactiv subsequently returned 404 twice and is now dead. The recovered older
archive added 2,822 labels; their four-worker incremental pass took 222 seconds and settled
every one dead, with no unknowns. Final ledger: **3,324 rows, 497 live, 2,827 dead**. Three aliases advertise another canonical label: challengepost → devpost,
citylightandpower → clpinc, oaklandshelter → lighthousemi. Their old labels are not eligible.
A named page with its explicit empty message is live-empty; a malformed page is never empty.

All 501 captured HTTP-200 board pages (including three aliases) parsed successfully. Cards
carry title, location and department, with no observed pagination. The largest, facefoundri,
advertises 197 cards. Native posting ids are the final 22 characters of the title slug:
1,833/1,833 feed-era ids had that length. Job ids retain that stable native id, while links
retain the complete public card path. Public lowercase labels are canonical; casing behavior
outside the measured label set is not claimed.

## Advertised cards versus reachable jobs

A public card does not prove that its posting page remains available. The exact Pricefx
Integrated Campaign Manager link, copied from the rendered board's anchor/data-url, failed
in the browser and returned HTTP 404 under both `Accept: text/html` and the default Accept.
Pricefx's Solution Strategist link on the same board opened the full posting in the browser.
The scraper therefore omits **confirmed 404/410 postings**, while transient request failures
preserve listed Jobs without the missing detail. It never converts a timeout/429 into an
empty Board. Ledger `jobs` remains the provider's advertised card count, distinct from the
scraper's returned accessible-posting count. No second whole-population detail census was run.

The live cohort was fixed before its results: 16 hiring Boards selected with seed 20261003,
plus the largest Board and the JobScore, Pricefx and imgix metadata/vanity controls. Twenty
were attempted; 19 were readable and Predactiv returned 404. The 19 boards advertised 281
postings; 208 detail routes returned 404, leaving **73 accessible Jobs and five tech Jobs**.
Facefoundri alone advertised 197 and returned 42 accessible Jobs. Saved responses from this
session were reused, avoiding repeated detail downloads.

## Fields and cost

Posting pages supply the complete visible description, parsed with balanced HTML tags so
nested divs do not truncate it. JSON-LD supplies dates and structured compensation where
present; it is not required for a description. JobScore's own engineering posting has a full
visible description and no JobPosting JSON-LD, so its posted date remains unknown. Native
schema salary amounts are already major currency units and use the existing structured
salary codec, preserving fractional rates and unit periods. A lone ceiling supplies no floor.
No experience category is invented from the description.

| Field | Populated / 73 returned Jobs |
| --- | ---: |
| Description | 73 |
| Company, title, department, location | 73 |
| Remote decision | 70 |
| Posted date | 72 |
| Salary | 47 |
| Employment type | 73 |
| Native experience | 0 |

The public subtitle provides department, full displayed location and employment type. A
Hybrid label stays unknown; TELECOMMUTE or a remote location reads remote. Location text
is preserved. No additional location array appeared in the sampled public cards. The detail
may supply/change department, so there is **no title-only tech gate**. Held details are read
again because they also provide date, salary and type.

The cohort's 301 unique request URLs transferred **3,018,589 body bytes / five returned tech
Jobs = 603,718 bytes per tech Job**, about 0.60 MB, below half of ADR-0158's ~2 MB bar. This
includes the largest Board's non-tech traffic. Enable the scraper. Seven content-confirmed
integration/test Boards remain excluded. This is request-body cost, not served-index size.

## Discovery and reproducibility

`mine_jobscore.py` reads the robots-advertised gzip sitemap: 2,332 URLs named 498 Boards.
Wayback/Common Crawl and careers-page fingerprinting recognize the public path forms.
The upstream ats-scrapers repository has no JobScore seed file. The initial CC 2026-39
query yielded 697 captures / 93 labels and four additional candidates; older-archive work
and cross-provider overlap are tracked by the coordinating task.

Local reproduction artifacts remain under `experiment/jobscore-public-api/`: `artifacts/`,
`html_sample.py`, `html-sample-final.log`, `html-live-summary.json`, and
`html-sample-jobs.json`. The older feed captures establish identity/field hypotheses and are
not production requests. `experiment/polymer-public-api/concurrency.py` and its JSON results
record every ramp request. Fixtures retain real public markup and omit styles/unrelated
scripts. Browser checks covered working JobScore/Pricefx postings and the published dead link.
Actual served-row/filter verification remains post-pipeline; no workflow was dispatched.

## Bounded concurrency and spare-route health

After the initial courtesy runs, a fresh process measured two scenarios per provider:
one Board and eight distinct Boards. Each level issued twice its concurrency in requests,
then paused two seconds; any non-200/transport failure would stop that provider. No feed
was requested. This is a short operating-point experiment, not a claim about the vendor's
maximum or an adversarial stress test. Every one of 120 ramp requests returned HTTP 200.

| Scenario | Concurrency | Requests | Requests/s | p50 seconds | p95 seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| one-board | 2 | 4 | 42.75 | 0.045 | 0.066 |
| one-board | 4 | 8 | 69.5 | 0.056 | 0.0876 |
| one-board | 8 | 16 | 140.32 | 0.045 | 0.0885 |
| one-board | 16 | 32 | 118.43 | 0.049 | 0.0991 |
| many-boards | 2 | 4 | 12.28 | 0.047 | 0.3244 |
| many-boards | 4 | 8 | 9.75 | 0.07 | 0.7555 |
| many-boards | 8 | 16 | 60.66 | 0.04 | 0.2604 |
| many-boards | 16 | 32 | 273.33 | 0.047 | 0.0848 |

A serial direct/spare pair before the ramp returned 200 on both routes: direct 0.0716 s / 19,802 bytes, spare 0.6453 s / 19,802 bytes. The existing SOCKS proxy was used; no daemon changes or rotation occurred. This proves route health, not a live transport-failure recovery.

JobScore had a pronounced CDN warm-up effect; 273 requests/s is a short cached burst, not an origin quota. Ship eight detail workers and eight process-wide starts/s, substantially below that burst.

The scraper opts into one spare retry for an exhausted connection/timeout/reset failure through the shared Fetcher seam. HTTP responses, including 403/429 challenges/refusals, never trigger that switch. The selected pacing remains in place. Raw outcomes and reproduction script: `experiment/polymer-public-api/concurrency-results.json` and `concurrency.py`.

The archive-recovery candidate pools and captures are preserved locally. The initial 497-live denominator used by the coordinating overlap audit matches the final census; advertised-title comparisons are distinguished from reachable postings.
