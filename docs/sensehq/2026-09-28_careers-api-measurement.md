# SenseHQ careers API: measurement for the liveness probe and ledger

Measured live on 2026-09-28 from a residential egress. The probe scripts and raw captures are kept
locally (`experiment/sensehq-careers-api/`, not committed). Every number a reader needs is below.

## The listing surface

`GET https://{label}.sensehq.com/careers/api/jobs?page={n}` needs no auth, cookie or Referer.
Page 0 answers `{"success":true,"data":{"count":N,"rows":[...]}}`.

- **`count` is the Board's whole total.** It is stated on every page.
- **Pages hold 10 rows by default and `page` is 0-indexed.** On rightatschool (count 279), page
  27 held 9 rows and page 28 held 0.
- **`pageSize` is honoured; `limit`, `size`, `page_size` and `per_page` are ignored.**
  `pageSize=100` gave 100 rows, and `pageSize=1000` gave all 279 unique rows in one request. The
  scraper still walks pages of 10, which costs a few requests on Boards this small.
- **Rate limit: none found.** 40 sequential requests took about 1 s each. 160 requests at 16
  concurrent and a 9,897-label sieve at 32 concurrent drew zero 429s. That sieve ran at about
  17 req/s; its only non-500 answers were 20 transient 502s, all of which read 500 on retry.

## What a label that is not a Board answers

`*.sensehq.com` is a wildcard DNS zone: `zz-nonexistent-q.sensehq.com` resolves. So DNS says
nothing. The listing's own error body does:

| Body (HTTP 500) | Who answers it | Read as |
|---|---|---|
| `{"error":"Table 'master.career_page' doesn't exist"}` | any unknown label (`nonexistent-tenant-xyz`, 11,655 sieve misses), vendor labels (`auth`), and Sense Engage customers with no career page (the US staffing firms Common Crawl lists) | dead |
| `{"error":"no organization found with subdomain {label}"}` | labels an organization once held (`embitel`, `livspace`, `homelane`, `yellow-ai`) | dead |

- **No unknown label ever answered 200**, so a 200 with `count: 0` is a real, empty Board
  (adani, pretium and 4 others).
- Vendor infrastructure answers other shapes: `cdn` and `careers` give an S3 403, and `www` and
  `status` give 404. None of these is a candidate.

## Pool: 82 labels, 35 live

| Source | Labels | Only it found |
|---|---|---|
| Common Crawl index, `*.sensehq.com` (CC-MAIN-2026-39/34/30, 2025-51) | 51 | 28 |
| Wayback CDX, `sensehq.com` domain, URLs containing `careers` | 47 | 23 |
| Label sieve: 11,704 tenant labels of seven Indian ATS ledgers (darwinbox, keka, freshteam, zwayam, pyjamahr, peoplestrong, ripplehire) | 7 | 7 |

Certificate transparency names no tenant, because the cert is a wildcard.

The ledger's first probe returned 35 live and 47 dead.

- **29 live Boards hold jobs.** The largest are rightatschool (279), trm-dev (205, already
  excluded as a test sandbox), zee (149) and ltts (146).
- **6 live Boards are empty.**
- Every live Board has its own `organization_id`, so no two labels serve one account.

## Fields, over the 28 hiring Boards other than trm-dev (1,057 jobs)

- **Always present:** location, department, `posted_at` (from `created_on`, epoch ms),
  experience (`experience_start`–`experience_end`), employment type (`job_type`) and requisition
  (`code`). Each read 1,057/1,057.
- **Description:** 1,003/1,057.
- **Remote:** true on 16 postings, from `workplace_type`.
- **Pay:** no structured field.
- **Location** is free text, and on 229 sampled rows it often listed several places. The one
  hiring `office` stated a country on 209 of them. So the office's country is appended only to a
  single-place location.

## Cost

The 1,057 jobs serialise to 3.28 MB. The tech gate (`is_tech(title, department)`) keeps 250 of
them, so the cost is about 13 KB per tech Job, against ADR-0158's accepted bar of about 2 MB.
