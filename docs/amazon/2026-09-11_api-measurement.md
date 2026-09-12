# Amazon (amazon.jobs) — API measurement

Live-verified 2026-09-11 against `https://www.amazon.jobs/en/search.json`, the public JSON search
endpoint behind the amazon.jobs careers site. Per ADR-0139 ("a Single source scraper is its own
ats" — PR #438), this is `ats="amazon"`
with one fixed, undiscovered `slug`: `www.amazon.jobs`. Implementation: `src/headstart/scrapers/amazon.py`.

## The endpoint, in one request

```
GET https://www.amazon.jobs/en/search.json?offset=0&result_limit=100&sort=recent
```

No auth, no cookies, no `Referer` required — a bare request with no `User-Agent` at all still
returned 200. Response shape (trimmed):

```json
{
  "error": null,
  "hits": 10000,
  "jobs": [
    {
      "id_icims": "10537803",
      "title": "Data Center Engineering Operations Technician ",
      "job_category": "Operations, IT, & Support Engineering",
      "business_category": "aws",
      "job_schedule_type": "full-time",
      "location": "US, VA, Ashburn",
      "normalized_location": "Ashburn, Virginia, USA",
      "locations": ["{\"type\":\"ONSITE\", ...}", "..."],
      "posted_date": "September 11, 2026",
      "job_path": "/en/jobs/10537803/data-center-engineering-operations-technician",
      "description": "<full HTML description>",
      "basic_qualifications": "<HTML>",
      "preferred_qualifications": "<HTML>"
    }
  ]
}
```

**The listing carries the full description — no detail pass needed.** Unlike Oracle
(`ShortDescriptionStr` capped at 1,000 chars) or Eightfold (description only on a second
`position_details` fetch), `description` here is the complete, untruncated posting body.
`basic_qualifications` and `preferred_qualifications` are separate HTML blobs the job page renders
alongside it — folded into `Job.description` (module docstring) because they carry the "years of
experience" and salary-range phrasing `experience.extract()`/`salary.extract()` read from that
field. `has_detail_pass = False`.

## Page size: hard-capped at 100

```
result_limit=100  -> 200, 100 jobs
result_limit=200  -> 200, {"error": "Result limit cannot be greater than 100", "jobs": null}
result_limit=1000 -> same error
```
Tested 100/200/500/900/999/1000 — 100 is the ceiling, not a convention borrowed from elsewhere.

## The 10,000-result ceiling, and why `hits` cannot be trusted

```
offset=9950, result_limit=100  -> 200, 50 jobs (the tail of the reachable range)
offset=10000, result_limit=100 -> 200, 0 jobs, jobs: null
offset=10050, result_limit=100 -> 200, {"error": "Cannot return more than 10000 results at
                                          once", "jobs": null}
```
`offset + result_limit` past 10,000 is refused. The top-level `hits` field is **not** a real
count: it reads exactly `10000` on every unfiltered query *and* on `business_category[]=aws`
(8,238 real postings) *and* on `business_category[]=no-business-category` (675) — a static
Elasticsearch cap on tracked total hits, not the match count. Applying a **narrow enough** filter
does make `hits` honest: `business_category[]=ecp` (181 postings) correctly reports `hits: 181`,
and a full paginated walk of that category returned exactly 181 distinct ids with zero drift.

## Subdivision: `business_category[]`, read fresh each run

The real total is far past 10,000 — summing the `business_category` facet's 60 buckets
(`facets[]=business_category`) gave **22,539** postings in one snapshot. This mirrors Workday's
`jobFamilyGroup` (ADR-0017): a facet whose values partition the board, subdivided on so the union
of per-slice walks covers everything the flat 10,000-row ceiling cannot reach in one query.
Verified with `business_category[]={value}` — filtering to `aws` returns exactly 8,238 jobs
(matching its own facet count), and every sampled job's own `business_category` field matched the
bucket it was fetched from (0 cross-bucket duplicates in a 1,981-posting, 7-category sample). The
largest bucket measured (`aws`, ~8,238) sits well under the 10,000 ceiling, so no bucket needs a
second level of subdivision today — `_offsets_for` still caps at 9,900 and `AmazonScraper` calls
`mark_truncated` unconditionally (ADR-0121, like Oracle's own offset ceiling) if one ever grows
past it. Results are still deduped by `id_icims` defensively across buckets, the same posture
every other subdivided scraper here takes.

Facet snapshot (top 10 of 60, 2026-09-11):

| business_category | count |
|---|---|
| aws | 8,238 |
| fulfillment-ops | 1,801–1,802 (drifted between two snapshots seconds apart) |
| fulfillment-and-operations | 1,539 |
| alexa-and-amazon-devices | 1,529 |
| transportation-and-logistics | 1,142 |
| operations | 789 |
| finance | 758 |
| retail | 691 |
| no-business-category | 675 |
| subsidiaries | 515 |

## Rate limit: light bursts are clean, sustained volume is not (revised 2026-09-12)

**Original finding (2026-09-11), still true as far as it went:** two bursts, 60 requests total —
20 at 20 concurrent threads (6.0s, 3.3 req/s), 40 at 16 concurrent (10.3s, 3.9 req/s). 0 of 60
non-200. Latency is real, not throttling: 1.9–6.2s per request, median 2.8s. No `User-Agent`
requirement either, unlike zwayam (hangs) or SuccessFactors (a specific literal denylisted) — a
bare request with none set still returned 200.

**That undersold it.** A completeness re-verification the next day ran the real scraper's own
pagination against every `business_category` bucket twice in a row (a hand-rolled parity check,
then `AmazonScraper.fetch_raw()` itself) — roughly 530 requests inside two minutes. The second
walk (the real `fetch_raw()`) came back with only **9,181 of 22,577** postings. The cause was not
a bad status code: `curl`ing the same endpoint immediately after reproduced it directly —

```
$ curl -s -o /dev/null -w "status=%{http_code} size=%{size_download}\n" \
    "https://www.amazon.jobs/en/search.json?offset=0&result_limit=100&sort=recent"
status=200 size=2860
```

200, but the body is HTML: Amazon's own CAPTCHA interstitial, `<title>Server Busy</title>`, a
"Continue shopping" button posting to `/errors_page/validateCaptcha`. `json.loads()` on that body
raises, which `fan_out`/`fan_out_async`'s blanket exception handler folds into the same "page
lost" bucket as a genuine transport failure — so the two are indistinguishable in the current
code, and that's fine: `mark_truncated_unless_negligible` measured the shortfall against the
fresh facet total and correctly called the run truncated (`read 9181 of 22577 postings`) rather
than serving the missing 59% as a complete, delistings-eligible board.

It cleared on its own: a single unhurried request ~20 seconds later got a clean 200 JSON, and an
isolated re-run of `fetch_raw()` right after — with no concurrent burst immediately preceding it
— returned **22,576 of a fresh 22,576-job facet-sum, exactly.** Zero cross-category overlap and
zero id/`business_category` mismatches were confirmed in the same session (a manual walk of all
60 buckets via the sync `_page` path, tracking every job id against every bucket it was fetched
under). So: this is a real, load-triggered wall — not the "no rate limit" the lighter sample
suggested — but it is transient and recoverable, and the scraper's own truncation guard is the
thing that actually matters here, not a code change made on the strength of one incident.

## Completeness verification (2026-09-12)

Beyond the rate-limit incident above, the same session independently re-verified the
subdivision strategy itself, end to end, against the live board:

- **Fresh facet-sum vs. a from-scratch manual walk of every bucket**: `_categories()`'s own live
  call reported 22,577 across 60 buckets; a separate hand-rolled walk of every `(category,
  offset)` page (263 page fetches, 16 concurrent, via the scraper's own `_page` — not `fetch_raw`)
  found exactly 22,577 distinct `id_icims` values, with the per-category raw count summing to
  22,577 *before* any dedup — i.e. dedup removed nothing, because there was nothing to remove.
- **Cross-category overlap: zero.** Of 22,577 ids, 0 were seen under more than one
  `business_category` filter.
- **Label agreement: zero mismatches.** Every job's own `business_category` field matched the
  bucket it was fetched under, for all 22,577.
- **No bucket over the ceiling**: largest measured, `aws` at 8,258 (this run) — comfortably under
  10,000, consistent with the 2026-09-11 snapshot (8,238).
- **No silently-empty pages**: 0 of 263 page fetches came back empty where a job was expected.

Net: the pagination/subdivision logic is exhaustive and non-overlapping when the API is actually
answering with JSON. The only way this session found to make it undercount was the CAPTCHA wall
above — a real board-reading failure mode, but not a defect in how the board is partitioned.

## Field-mapping findings

- **`id_icims`**, not the UUID-shaped `id` field, is the durable native id — it is what
  `job_path` and `url_next_step` (the apply link) are built from. Present and non-null on all
  1,981 postings sampled across 7 categories.
- **`job_category`** (e.g. "Software Development", "Operations, IT, & Support Engineering") maps
  to `Job.department` — the role-family analogue every other scraper's `department` carries.
  `business_category` (aws/retail/advertising/...) is a business-unit label, spent entirely on
  subdividing the listing walk, and is not department-shaped.
- **Remote detection**: each posting's `locations` array carries a JSON-encoded entry per site,
  each with its own `type` (`ONSITE` or `VIRTUAL`). 138 of 139 location entries in one 100-posting
  sample were `ONSITE`; the one `VIRTUAL` case and a second one found later (a Principal PM role
  with a Nevada `VIRTUAL` entry alongside an onsite Seattle one) both read genuinely remote-shaped
  on inspection. Any `VIRTUAL` entry marks the Job remote; `is_remote()` on the location string is
  the fallback for a posting with no parseable `locations` at all.
- **`posted_date`** is a human string, `"September 11, 2026"` — and on a single-digit day the API
  emits a **double space** (`"September  9, 2026"`), confirmed across a 500-posting, 5-page
  sample (4 distinct posted_date strings observed, one of them double-spaced). `%B %d, %Y` cannot
  parse the double-space form directly, so whitespace is collapsed first.
- **`title`** routinely carries trailing whitespace on the wire (`"Data Center Engineering
  Operations Technician "`, trailing space included, verified in the raw JSON) and is stripped.
- **`company_name`** is the posting's legal entity (Amazon Data Services, Inc.; Amazon.com
  Services LLC; Amazon UK Services Ltd.; Souq.com for E-Commerce LLC; ...) — 10+ distinct values
  in the top 10 of a single 100-posting sample. Not a display name; `Job.company` stays the fixed
  `"Amazon"` self.company.
- **`job_schedule_type`**: `full-time` (500/500 in one sample) and `part-time` (16/1,981 in a
  wider 7-category sample) are the only values observed; `is_intern` was `None` on every sampled
  posting.

## Job URL

`job_path` from the API (`/en/jobs/{id_icims}/{title-slug}`) appended to the fixed host:
`https://www.amazon.jobs{job_path}`. Live-verified: `GET
https://www.amazon.jobs/en/jobs/10537803/data-center-engineering-operations-technician` returns
200. `scripts/eval/verify_filters.py`'s `URL_SHAPES["amazon"]` anchors on this exact shape.

## Liveness ledger

One row, `data/validate/liveness/amazon.csv` (`ats,tenant,url,status,jobs,checked_at`):
`amazon,www.amazon.jobs,https://www.amazon.jobs/en/,live,22539,2026-09-11` — the `jobs` count is
the facet-sum snapshot above, not a re-derivation; it will drift run to run on a real board.
