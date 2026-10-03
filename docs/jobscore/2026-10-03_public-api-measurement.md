# Jobscore public surface measurement — 2026-10-03

## Public surface, identity and fields

The [vendor's published feed guide](https://support.jobscore.com/hc/en-us/articles/202001320-Developers-Guide-to-Job-Feed-APIs)
documents `/jobs/{label}/feed.json`. One request includes the complete description.
The initial 498 sitemap Boards all answered 200; 270 had jobs and 228 were empty.
The largest observed feed was facefoundri, 197 postings. No pagination metadata exists.
The feed description matched the browser's complete JobScore posting (1 compared).
No per-job request or pre-detail tech gate is needed.

A Board is its lowercase canonical `company_code`. Three historical labels published
another code without redirect: challengepost → devpost, citylightandpower → clpinc,
oaklandshelter → lighthousemi. Only canonical codes are eligible. circadencecorporation
returned HTTP 410 with a Page Gone document; an invented label returned a 404 document.
A real empty Board returns a named feed with `jobs: []`; an absent/wrong container fails.
No cross-host alias was observed; no Board key or native id contains a colon.

`opened_date` is the publication field; `last_updated_date` and `created_on` are not used.
Immediate repeated-feed date stability was not tested because the guide asks for at most
hourly polling. The initial sitemap census read each feed once, serially. Four later
historical candidates were automatically retried while the first probe classified them
UNKNOWN; their measured 410/canonical-code outcomes now settle in one call.

Location is the feed's complete location string (city/state/country also present); no
separate multi-location array was observed. Department and experience_level are retained.
Native remote phrases distinguish Yes, No and Hybrid; Hybrid stays `None`.
Compensation is minor currency units: USD 4,000 means $40/hour and 5,000,000 means $50,000/year.
JPY is the measured exception: imgix's 10,000,000 means ¥10,000,000, matching its formatted
field. Seven observed currencies use /100, JPY /1. All observed intervals (hour/day/week/
month/year) use the shared structured salary codec, preserving fractional rates.
A lone maximum (two observed rows) does not become a minimum. The feed itself supplies
the company name; no separate name-page request is made.

## Operation and cost decision

Robots allows the feed and public postings, disallows `/apply_flow/`, and advertises the
cross-tenant gzip sitemap. Its 2,332 URLs named 498 Boards; `mine_jobscore.py` reproduces it.
All initial feed requests used `headstart/0.1`; alternative User-Agent throughput is unknown.
The scraper spaces process-wide starts by 1.5 seconds; the planner enforces one hour since
the persisted last look before either priority or Tail selection. Pipeline chaining is faster
than hourly, so the schedule is not the guard. Liveness reads public HTML instead of feeds:
197/197 facefoundri cards, 18/18 clpinc, 3/3 pricefx and Blueleaf's explicit zero matched the
feed census. A page without cards or an explicit empty marker remains UNKNOWN. A complete HTML liveness refresh then settled all 502 candidates at 2 workers in 84.9 seconds: 498 live, 4 dead, zero unknown, matching the feed census. Feed boundaries/caps above 197 jobs remain unknown.

Enable: 12,978,552 listing bytes / 221 tech postings ≈ 59 KB/tech posting, well under
ADR-0158's ~2 MB bar. Seven hiring integration/test Boards are excluded after reading their
placeholder descriptions. It is a native ATS surface, not a known backing-ATS front;
a complete cross-provider employer/title comparison was not performed.

## Reproduction and limits

All observations are from 2026-10-03. The local notebook is
`experiment/jobscore-public-api/`: `LOG.md`, `artifacts/`, and saved probe logs.
`experiment/polymer-public-api/measure.py`, `measure_summary.py` and `live_sample.py`
record requests, parse real responses and calculate the figures below. Captures stay
local; fixtures are trimmed real responses with recruiter/contact/token material omitted.

The rate-limit knee was deliberately not stress-tested. No credential, customer API,
IP rotation, daemon rotation or pipeline/deploy workflow was used. The normal prober was
run through `probe_no_rotation.py`, which disables rotation and records every response.
Unexplained responses remain UNKNOWN; malformed scraper envelopes fail instead of
certifying a Board as empty. Real pagination beyond the largest observed Board remains
unmeasured. A source outage is not a zero-yield discovery result.

## Metadata and yield

The sample excludes confirmed vendor demo/integration-test Boards. Counts below are
non-null/non-empty Job fields after parsing, except `remote`, where false is populated.

| Field | Populated / 1,816 postings |
| --- | ---: |
| description | 1,816 |
| department | 1,816 |
| remote | 1,504 |
| location | 1,816 |
| posted_at | 1,816 |
| salary | 1,163 |
| experience | 1,816 |
| employment_type | 1,816 |

The listing sample covers 491 Boards and 1,816 postings, including 221
tech-filter matches. Listing bytes total 12,978,552; 58,726 bytes per tech posting.
This is request payload cost, not served-index size. Language detection (seed 20261003,
100 sampled descriptions, langdetect seed 0): en 100/100. This is a sampled indicator,
not a language guarantee; the existing English ingestion gate remains authoritative.
All observed employment-type values were passed through `employment_type_filter.flags`
in the local summary; source phrases are retained rather than inventing employment facts.

## Discovery and liveness

Final ledger: 502 rows, 498 live, 4 dead, 0 unknown.
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

Review follow-up: pricefx's three feed detail URLs name careers.pricefx.eu. The scraper now
builds the provider-host route from url_slug; the Solution Strategist route was browser-verified
to show that job's full description, salary and application control. This keeps the URL contract
independent of customer vanity-host availability.
