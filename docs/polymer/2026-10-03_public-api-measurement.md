# Polymer public surface measurement — 2026-10-03

## Public surface, identity and fields

[Official public API documentation](https://developer.polymer.co/) explicitly separates
unauthenticated organization job feeds from the private Customer API. Only the public
`/v1/hire/organizations/{label}/jobs` and `.../jobs/{numeric id}` are used.
The Board identity is the lowercase public label; the hosted `jobs.polymer.co/{label}/{id}`
route works even when job_post_url names a customer's vanity domain (Cedar browser check).
No same-ATS repeated posting sets were found among the captured hiring feeds.

`meta.count` is postings and `meta.total` is pages. The largest Board observed was Aru Labs,
50 postings. Its page 2 returned an empty list but `is_last=false,next_page=3`: never chase
that flag indefinitely. Cedar's `per_page=2` was ignored and returned all five postings.
The scraper uses count/page bounds, detects a stalled walk, and marks shortfalls or failed
later pages truncated. No genuine multi-page live Board was found; the guarded multi-page
and 429 paths are tested with real rows and controlled HTTP envelopes.

Existing empty Boards have an organization_name and zero count. Four initial dead candidates
returned 422 with either `errors.organization=["could not be found"]` or
`errors.general=["no careers_page found"]`; only these measured error bodies settle DEAD.
The invented label also returned the latter. Other errors remain UNKNOWN.

Listings omit descriptions; 20 largest non-demo Boards were fetched through the Scraper:
202 jobs, 202 details, 34 tech postings, no detail losses/truncations. Detail also supplies
`department`, distinct from listing category, so there is no title-only tech gate and no
held-description skip that would discard department. Listing metadata survives failed details.
`published_at`, not created_at, is the publication date; Cedar's five values stayed identical
across the initial read and the later per_page comparison. Remote, Remote friendly, No remote,
and Hybrid are distinguished; Hybrid is unknown, No remote false. Remote country restrictions
and the stated restriction city join the location, preserving every eligible country.
No native experience field was observed. `kind_pretty` and organization_name are retained.

Salary comes from the listing (`salary_pretty`), not the detail: expand K, preserve decimals,
normalize an hour/a month to explicit periods, then encode through `salary.to_field`.
The structured decoder preserves 13.50/hour as 28,080/year; the generic decoder rounded too early.
Unsupported salary shapes decline rather than inventing a floor. New dispatch keys touch no
previously served rows, so no DERIVATIONS_VERSION bump is needed.

## Operation and cost decision

API and board robots are empty 200 documents. No credentials, tokens or private endpoints
are needed. The 392 uncached candidates completed with 2 workers in 49.4 seconds, zero
unknowns and no 429; the subsequent ramp below supports eight detail workers and eight process-wide starts per second. Mixed-board throughput flattened after concurrency eight in the subsequent ramp.
Other user agents and a larger/live multi-page Board are unmeasured.

Wayback completed 2 pages, 406 labels; search seeds make 410. No vendor-wide roster was
located. Aperture Labs, the documentation's example company, carries corporate-ipsum job
text and is excluded after its real detail was read. No native API posting-ID aliases were
found. Cross-ATS employer/title overlap remains unmeasured.

Enable: 384,986 listing bytes for 380 postings; the recorded detail mean is approximately
6.6 KB (203 detail captures including the one demo inspection). Even fetching every detail
projects about 2.9 MB / 91 tech postings ≈ 32 KB/tech posting, far below the ~2 MB bar.
The actual 20-Board pass read 202 details; the remaining Boards' detail cost is an estimate.

## Reproduction and limits

All observations are from 2026-10-03. The local notebook is
`experiment/polymer-public-api/`: `LOG.md`, `artifacts/`, and saved probe logs.
`experiment/polymer-public-api/measure.py`, `measure_summary.py` and `live_sample.py`
record requests, parse real responses and calculate the figures below. Captures stay
local; fixtures are trimmed real responses with recruiter/contact/token material omitted.

The bounded ramp below replaces the initial untested operating point; no origin was stressed past a refusal. No credential, customer API,
IP rotation, daemon rotation or pipeline/deploy workflow was used. The normal prober was
run through `probe_no_rotation.py`, which disables rotation and records every response.
Unexplained responses remain UNKNOWN; malformed scraper envelopes fail instead of
certifying a Board as empty. Real pagination beyond the largest observed Board remains
unmeasured. A source outage is not a zero-yield discovery result.

## Metadata and yield

The sample excludes confirmed vendor demo/integration-test Boards. Counts below are
non-null/non-empty Job fields after parsing, except `remote`, where false is populated.

| Field | Populated / 380 postings |
| --- | ---: |
| description | 202 |
| department | 272 |
| remote | 334 |
| location | 355 |
| posted_at | 380 |
| salary | 114 |
| experience | 0 |
| employment_type | 380 |

The listing sample covers 222 Boards and 380 postings, including 91
tech-filter matches. Listing bytes total 384,986; 4,231 bytes per tech posting.
This is request payload cost, not served-index size. Language detection (seed 20261003,
100 sampled descriptions, langdetect seed 0): en 100/100. This is a sampled indicator,
not a language guarantee; the existing English ingestion gate remains authoritative.
All observed employment-type values were passed through `employment_type_filter.flags`
in the local summary; source phrases are retained rather than inventing employment facts.

## Discovery and liveness

Final ledger after all archive increments: 422 rows, 227 live, 195 dead, 0 unknown. The final three labels were Motive (live, five advertised jobs), PopVax (live, 13), and Rock Rabbit (dead). The incremental pass settled two live and seven dead in 1.3 seconds at four workers.
These are rows; exclusions and identity election determine Scrapable/Hiring Boards.
Upstream `kalil0321/ats-scrapers/ats-companies` contains no seed file for this provider
(confirmed by the repository directory listing; all three guessed file requests 404).
Common Crawl was attempted directly, one request at a time, over a three-year index list.
The 2026-39 JobScore query yielded 697 captured URLs / 93 labels (4 outside the sitemap);
Comeet 2026-39 returned 504, Polymer 2026-39 returned 502, and JobScore 2026-34 returned
504. Their older remainder is **unmeasured**, not exhausted. No alternate address was used.

## Validation

Real-fixture parser/fetch tests, liveness and discovery identity tests, truncation and
failed-detail tests are in this PR. Browser checks opened the exact JobScore Senior
Front-End Engineer, Port Senior Backend Engineer and Cedar Senior Software Engineer
posting routes and showed their full descriptions and application controls.
The live filter harness ran 152 checks with zero filter violations/errors and no ATS
missing a URL shape. Its existing served corpus returned two pre-existing Freshteam board-query links, one Radancy 404, and ten provider 403/406/429 refusals; parent integration owns
the Freshteam correction (browser verification showed the query route opens the whole Board). Several SPA HTML bodies omit the title; those are not evidence
of a wrong route. No rows from these three new ATSes exist in the served corpus yet:
actual served-row/filter verification is a **post-pipeline follow-up**, not claimed here.

## Bounded concurrency and spare-route health

After the initial courtesy runs, a fresh process measured two scenarios per provider:
one Board and eight distinct Boards. Each level issued twice its concurrency in requests,
then paused two seconds; any non-200/transport failure would stop that provider. No feed
was requested. This is a short operating-point experiment, not a claim about the vendor's
maximum or an adversarial stress test. Every one of 120 ramp requests returned HTTP 200.

| Scenario | Concurrency | Requests | Requests/s | p50 seconds | p95 seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| one-board | 2 | 4 | 4.05 | 0.492 | 0.7107 |
| one-board | 4 | 8 | 6.93 | 0.565 | 0.7718 |
| one-board | 8 | 16 | 14.14 | 0.542 | 0.7613 |
| one-board | 16 | 32 | 18.64 | 0.703 | 1.0295 |
| many-boards | 2 | 4 | 2.91 | 0.559 | 1.3225 |
| many-boards | 4 | 8 | 4.99 | 0.591 | 1.6023 |
| many-boards | 8 | 16 | 11.33 | 0.7 | 1.3633 |
| many-boards | 16 | 32 | 12.42 | 0.785 | 1.73 |

A serial direct/spare pair before the ramp returned 200 on both routes: direct 0.7437 s / 4,785 bytes, spare 0.7817 s / 4,785 bytes. The existing SOCKS proxy was used; no daemon changes or rotation occurred. This proves route health, not a live transport-failure recovery.

Polymer mixed-board throughput rose from 11.33 to only 12.42 requests/s between eight and sixteen while p95 rose from 1.36 to 1.73 seconds. Choose eight detail workers and eight starts/s at that observed throughput knee. No refusal threshold is asserted.

The scraper opts into one spare retry for an exhausted connection/timeout/reset failure through the shared Fetcher seam. HTTP responses, including 403/429 challenges/refusals, never trigger that switch. The selected pacing remains in place. Raw outcomes and reproduction script: `experiment/polymer-public-api/concurrency-results.json` and `concurrency.py`.
