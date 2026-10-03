# Comeet public surface measurement — 2026-10-03

## Public surface, identity and fields

The public hosted page `/jobs/{label}/{company_uid}` embeds `COMPANY_DATA` and
`COMPANY_POSITIONS_DATA` as JSON, including the full custom-field descriptions and requirements.
The [Careers API](https://developers.comeet.com/reference/retrieve-a-position-new) is not
needed: the scraper neither extracts nor sends its token. Port's browser detail matches
the embedded full description/requirements (1 compared). The largest non-demo Board read
was Resolv.Global with 401 postings, in one HTML response. Pagination is not present on
this surface. `is_internal` postings are excluded.

The fetch slug is lowercase label/UID; lowercased nSure a7.007 and Scopio 87.00c read
successfully. The immutable company UID owns the Board and Job ids. Renamed labels such
as echosoftware/echo share UID 9a.006 and their postings. Canonical normalization removed
54 redundant rows from the 5,117-candidate pool/ledger and verified 577 canonical UIDs;
14 seeded live Boards already had their canonical labels. A dead old label cannot remain
beside a live canonical row and later override its verdict. Run
`comeet_canonical_boards.py` after discovery merges and liveness refreshes. It replaces
rows only after reading the canonical public page; failed/unknown reads preserve history.

Wrong/departed label+UID routes answer 302 to the vendor home. Valid empty Boards carry
the company object and an empty positions array (nSure, Scopio, Raft and H2Pro measured).
195 candidates instead show a Request for consent page: UNKNOWN, never empty or dead.
There is no consent bypass. No native id contains a colon in the sample.

Only `time_updated` exists, so posted_at remains null. `workplace_type` wins over the
location boolean: Port's hybrid Account Manager has `location.is_remote=true`. Explicit
Hybrid stays unknown, Remote true, On-site false. Location is the published location name;
no extra location array was present. Department, experience_level, employment_type and
company name are read directly. There is no native structured salary in these 9,005
postings; description salary extraction remains available. No detail pass or detail gate.

## Operation and cost decision

Robots allows `/jobs/` and forbids tracking-query variants; the scraper adds none.
Courtesy sample: 8/8 public boards succeeded at concurrency 4 in 2.67 seconds. The full
first pass probed 5,102 uncached candidates at 4 workers in 530.5 seconds, with no HTTP
429. It found consent pages and aliases, not a measured rate-limit knee. Production
spaces request starts process-wide by one second. Only `headstart/0.1` was compared at scale;
other user agents and limits beyond these observations are unknown.

Wayback completed 89 comeet.com and 22 comeet.co pages: 5,116 identities, 5,117 with
search seeds. Canonicalization then removed duplicate routes as above. No complete vendor
roster was located. The Demo Company (yourcompany/80.003) lists 695 fabricated jobs and is
excluded after reading its contents. Native Comeet job/apply pages do not name a separate
backing ATS; cross-provider employer overlap remains unmeasured.

Enable: 113,839,488 bytes / 3,094 tech postings ≈ 37 KB/tech posting, comfortably below
ADR-0158's ~2 MB bar. Missing descriptions (30) remain null rather than dropping Jobs.

## Reproduction and limits

All observations are from 2026-10-03. The local notebook is
`experiment/comeet-public-api/`: `LOG.md`, `artifacts/`, and saved probe logs.
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

| Field | Populated / 9,005 postings |
| --- | ---: |
| description | 8,975 |
| department | 8,565 |
| remote | 6,547 |
| location | 8,904 |
| posted_at | 0 |
| salary | 0 |
| experience | 5,040 |
| employment_type | 7,323 |

The listing sample covers 590 Boards and 9,005 postings, including 3,094
tech-filter matches. Listing bytes total 113,839,488; 36,794 bytes per tech posting.
This is request payload cost, not served-index size. Language detection (seed 20261003,
100 sampled descriptions, langdetect seed 0): en 94/100, he 5/100, de 1/100. This is a sampled indicator,
not a language guarantee; the existing English ingestion gate remains authoritative.
All observed employment-type values were passed through `employment_type_filter.flags`
in the local summary; source phrases are retained rather than inventing employment facts.

## Discovery and liveness

Final ledger: 5,063 rows, 591 live, 4,277 dead, 195 unknown.
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

Review follow-up: explicit On-site now overrides contradictory Remote location text
(41 captured jobs; Zero Networks 83.26C is the regression fixture). Canonical normalization
preserves capture timestamps, rejects proofs older than any ledger verdict, streams each
attempt, and atomically checkpoints dated proofs; `--resume` continues a partial scan.
Captures with no reliable timestamp are declined rather than stamped as current.
