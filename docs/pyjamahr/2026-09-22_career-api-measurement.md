# PyjamaHR `/api/career/jobs/`: what the API actually does

Measured 2026-09-22, before `headstart.scrapers.pyjamahr` was written, against the live API and
the live board host. The July research note (`experiment/ats-provider-expansion/artifacts/
research_pyjamahr_turbohire.md`) had the endpoint right and three things wrong — the key, the
roster, and the page size — and four public implementations on GitHub had walked into three
traps between them. Every number here comes from a request made that day; the run record with
per-step counts is `experiment/pyjamahr-career-api/LOG.md`, and the per-tenant table is
`experiment/pyjamahr-career-api/artifacts/2026-09-22_tenant_census.csv`. The decisions are
ADR-0175.

Sample: **757 tenants** (every one the vendor's sitemap and the Wayback Machine name), **8,897
listing rows**, **1,741 detail payloads** across ten tenants, **~3,800 requests** in total.

## 1. The key is the slug, not the UUID

The research keyed the API on `company_uuid`, a ten-character code that lives only in the board
page's `__NEXT_DATA__`. The API also takes `company_slug`, and it is the same filter:

```
GET https://api.pyjamahr.com/api/career/jobs/?company_uuid=C9047F0152   -> count 2
GET https://api.pyjamahr.com/api/career/jobs/?company_slug=pyjamahr     -> count 2, same rows
GET https://api.pyjamahr.com/api/career/jobs/315177/?company_slug=pyjamahr -> the detail
```

The board's own `[company]` route chunk builds its requests with `company_slug` (plus `page`,
`query`, and the department/location/product filters), so this is the front door, not a side
one. The slug is the public board's path segment (`jobs.pyjamahr.com/{slug}`) and the sitemap's
key (§3), which makes it the identity — nothing has to be resolved per tenant.

Three properties of the key, each checked:

- **Case-sensitive.** `company_slug=PYJAMAHR` answers `count: 0`; `company_uuid=c9047f0152`
  (lower-cased) still answers the board. Every slug in the sitemap is lowercase `[a-z0-9-]`, and
  the 8,897 job slugs are `[a-z0-9_-]`.
- **Required on the detail.** `/api/career/jobs/315177/` alone → 404 `{"detail":"Not found."}`.
  With another tenant's slug → the same 404. A nonexistent id with the right slug → the same 404
  again, so a posting that closed between the listing and the detail call is indistinguishable
  from a wrong key; the scraper counts either as a detail gap and emits the Job from the listing.
- **Cross-tenant isolation holds.** A `tulip-group` id fetched with `company_slug=pyjamahr` → 404.

## 2. Pagination: DRF, page of 10, and an unbounded `limit`

The envelope is Django REST Framework's `{"count", "next", "previous", "results"}`. Measured on
`tulip-group` (124) and `hunarstreet-technologies` (643):

| request | rows | `next` |
| --- | --- | --- |
| bare | 10 | `…&page=2` |
| `page_size=200` | 10 | `…&page=2&page_size=200` (ignored) |
| `limit=200` | 124 | null |
| `limit=100` | 100 | `…&limit=100&page=2` |
| `limit=100&page=2` | 100 | `…&page=3`, `previous` set |
| `limit=100&offset=100` | 100 | `…&offset=100&page=2` (`offset` ignored) |
| `limit=5` / `limit=123` on 124 | 5 / 123 | set |
| `limit=0`, `-1`, `abc` | 10 | set (fall back to the default) |
| `limit=1000`, `5000`, `100000`, `999999999` on 643 | 643 | null |
| `page=0`, `page=999` | — | HTTP 404 `{"detail":"Invalid page."}` |

So `limit` is a real page-size parameter with **no ceiling found**, `page` still works alongside
it, and `next` carries both. A full `next` walk of `tulip-group` at the default page read 13 pages
(10×12 + 4), 124 unique ids in descending order, page 1 byte-stable across a re-fetch. The
scraper asks for `limit=1000` — the largest Board in the census is 643 — and reads every known
Board in one call, but follows `next` whenever it is set, so a ceiling added later cannot
silently shorten a Board.

`count` equalled the rows served on **757 of 757** tenants. A shortfall is therefore the API
changing under us, and the scraper reports one through the ADR-0121 tolerance.

## 3. The roster is published by the vendor

The Next.js build manifest names a `/sitemap-jobs.xml` route. It is a single cross-tenant
sitemap of every published posting on the platform:

```
GET https://jobs.pyjamahr.com/sitemap-jobs.xml      # 2.4 MB, application/xml
  <loc>https://jobs.pyjamahr.com/{tenant}/{job-slug}</loc> × 7,801, 680 distinct tenants
  <lastmod> minutes old; oldest 2025-11-10
```

Per-tenant size: 110 tenants with one URL, 360 with 2–9, 186 with 10–49, 24 with 50+; the
largest is `hunarstreet-technologies` at 448 URLs against 643 API rows. That gap is general:
the sitemap's per-tenant count disagreed with the API's `count` on 165 of 680 tenants, **164 of
them low** (by a handful each), so the sitemap is a roster and not a job count. It is also not a
filter on `is_seo_indexable` — the vendor's own non-indexable posting is in it.

The Wayback Machine's CDX index for `jobs.pyjamahr.com/*` returned 413 captures naming 144
first path segments, three of them assets (`images`, `&`, `rawjsfromfile.js`) and 141 tenants.
64 are also in the sitemap; **77 are not**, and all 77 answered a live board page — 3 of them
hiring, 128 postings between them. Union: 757.

Common Crawl's newest index (`CC-MAIN-2026-39`, one page for this host) names 22 tenants;
every index of the last three years together (33, `CC-MAIN-2023-40`..`2026-39`, 3 of them
re-run after CC throttled the first pass) names 161, of which 10 are in neither the sitemap nor
Wayback — all live, one hiring. The running total went flat at 136 before `CC-MAIN-2025-05`, so
older indexes hold nothing further. So the vendor's
sitemap states what is live today, Wayback and Common Crawl cover what was live before, and
all three are wired: `scripts/discover/mine_pyjamahr.py` reads the sitemap,
`wayback_feeder.ATS_HOSTS` gains a `path` entry and `cc_miner.ATS_PATTERNS` a `slug` entry for
`jobs.pyjamahr.com`. Run through those tools rather than this census, the roster landed as
**768 ledger rows: 767 live, 1 dead, 683 hiring, 8,895 postings** — the one dead row is
`images`, a path the Wayback feeder's validity filter admits (it is a legal slug) and the prober
kills off the board page's 404, which is the dead branch of §4 doing its job on the first day.

## 4. Dead versus empty: the API cannot say, the board page can

```
GET …/api/career/jobs/?company_slug=notacompany123   -> 200 {"count":0,"next":null,"previous":null,"results":[]}
GET …/api/career/jobs/?company_slug=volopay          -> 200, the identical 52 bytes (a real tenant, nothing open)
GET https://jobs.pyjamahr.com/notacompany123          -> 404, <title>404: This page could not be found</title>
GET https://jobs.pyjamahr.com/volopay                 -> 200, __NEXT_DATA__.props.pageProps.companyDetails present
```

The listing is a filter over one table, so an unknown key is an empty result, not an error. The
board page is server-rendered by slug and 404s honestly. Across the census every one of the 757
tenants answered 200 on the page (75 of them with `count: 0`), and the only 404s were the three
non-tenant paths above. The liveness prober therefore asks the API first (`limit=1`, ~200 bytes;
a positive `count` is proof of a tenant) and spends the page fetch only on a zero.

The facet endpoints do not help: `…/jobs/departments/?company_slug=notacompany123` returns the
same global department list a real tenant gets, and `…/jobs/locations/` returns `[]` for both.

## 5. The listing carries the identity; the detail carries the rest

A listing row:

```json
{"id": 143305, "slug": "technical-support-tier-2-hk", "title": "Technical Support - Tier 2 | HK",
 "min_experience": 5.0, "max_experience": 8.0, "country": "India", "location": "India",
 "other_locations": [], "department_name": null, "published_internally": false,
 "workplace_type": "REMOTE", "product": null}
```

Over 8,897 rows: `title` never blank or padded; `location` never null (1,145 rows say just the
country, 41 say `Remote`); `other_locations` non-empty on 1,396 (15.7%), and repeats `location`
on one; `department_name` on 4,924 (55%); both experience bounds present on every row, always
integral floats, never inverted; `product` always null; `slug` missing on 6 rows (four on
`truww`), and 2 tenants publish two postings under one slug.

The detail adds `description` (HTML, non-empty on **1,741 of 1,741**, p50 ~2,200 characters),
`uuid`, `job_type`, `remote`, `min_salary`/`max_salary`/`currency`/`salary_type`/
`is_salary_visible`, `skill[]`, `seniority[]`, `education[]`, `industry`, `created_at`,
`valid_through`, and a dozen tenant-config fields (`referral_email`, `email_domain`,
`is_seo_indexable`…). `title`, `location`, `workplace_type`, the experience bounds and
`department_name` never disagreed between the two payloads.

Because `job_type`, the salary quartet and `created_at` exist only on the detail, the ADR-0048
skip of the already-described is not taken — it would blank `employment_type`, `salary` and
`posted_at` on a Job the description store already covers. Same fork, same answer as oracle. The
ADR-0166 tech gate is a different skip and *is* taken, as an exact site: `parse` reads `title`
and `department_name` off the listing row and the detail overrides neither, so the gate asks
`filter_tech`'s question with its own inputs. At the 25.5% tech share of §9 that is about three
of every four detail fetches not made in the pipeline.

### `remote` is dead

`remote` was `false` on **all 1,741** details, including the 102 whose `workplace_type` was
`REMOTE`. `workplace_type` is the tenant's answer: ON_SITE 6,824 / REMOTE 1,172 / HYBRID 850 /
null 51 across the census. It never disagrees with the location string — of 7,674 rows stating
ON_SITE or HYBRID, **none** names a remote location, and of 1,172 REMOTE rows 41 say `Remote`
and the rest name a country or city. So the stated type wins, the location guess covers the 51
unstated rows, and HYBRID is not remote (ashby's rule).

### `published_internally` rows are hidden by the board

112 rows across 39 tenants carry `published_internally: true`. The API serves them and counts
them; the board's `[company]` chunk filters them out before rendering:

```js
n.filter(function(e){return!(null!==e&&void 0!==e&&e.published_internally)})
```

They are internal postings. The scraper drops them in `parse` and skips their details. `count`
still includes them, so the shortfall check compares the unfiltered walk.

### Salary: bounds appear exactly when the tenant says so

| | visible | hidden |
| --- | --- | --- |
| details | 1,236 | 505 |
| with both bounds | 1,236 | 0 |
| with one bound | 0 | 0 |

`salary_type`: ANNUAL 1,022 / MONTHLY 641 / HOURLY 78. `currency`: INR 1,577, USD 160, and one
each of MYR, HKD, EUR, AED — always present, even when hidden. Amounts are floats
(`1000000.0`), so the scraper writes them as digits (never `:g`, which spells 1,200,000 as
`1.2e+06`) in the form `salary._field_generic` reads: `30000-40000 INR per-month`. An unobserved
`salary_type` yields no salary rather than a figure read at the annual default.

### `valid_through` is not a closing date

It was in the past on **1,408 of 1,741** postings that the listing still returns and the board
still shows. It is `created_at` + 60 days on every sample, a JSON-LD default, and is ignored.

### `job_type`

FULLTIME 1,667 / CONTRACT-BASED 48 / PART-TIME 16 / INTERN 8 / FREELANCER 2. Mapped to labels;
an unobserved code passes through as spelled.

### `created_at`

ISO-8601 with the tenant's own offset: `+05:30` on 1,512 of 1,741, then `-07:00` (124),
`+08:00` (40), `-08:00`, `+03:00`. Kept as `posted_at`.

## 6. The job URL is the slug page

```
/hunarstreet-technologies/technical-support-tier-2-hk   -> 200, <link rel="canonical"> to itself, JobPosting JSON-LD
/pyjamahr/315177  (numeric id)                          -> 307 /pyjamahr?job_uuid=315177
/pyjamahr/E8E706F46B  (uuid)                            -> 307 /pyjamahr?job_uuid=E8E706F46B
/pyjamahr?job_uuid=<anything at all>                    -> 200, the board page
/notacompany123/marketing-manager                       -> 307 /notacompany123?job_uuid=marketing-manager
```

The `?job_uuid=` form the ever-jobs implementation calls canonical is a board-page query that
the client resolves to `/{company}/{slug}` only when the value is a real uuid — and it answers
200 for any value, so it can never be checked. The scraper links `/{slug}/{job-slug}` and, for
the six slugless rows, the board page. `url_shape` is
`https://jobs\.pyjamahr\.com/[\w-]+(?:/[\w-]+)?`.

## 7. Company name: the page title *is* the field

The board page's `<title>` equalled `__NEXT_DATA__…companyDetails.name` on **757 of 757**
tenants — it is the name, with no wrapper. Through `company_name.from_title` with a catch-all
pattern, 723 resolve; of the 34 that keep their slug, 25 have a title that is the slug itself
(`smallcase`), 5 carry a separator (`RealPage | Rexera`), 3 are hostnames (`aainacareers.com`)
and one is a page label (`Careers at AiFA Labs`). 595 of the 757 names are the slug re-cased
(`1-percent-group` → `1 Percent Group`); 162 are genuinely different (`8byte` → `Octa Byte AI
Pvt Ltd`).

## 8. Rate limiting: none found

| burst | requests | concurrency | rate | non-200 |
| --- | --- | --- | --- | --- |
| 145 slugs, listing + page | 290 | 16 | 35 req/s | 0 |
| 5 tenants, details | 255 | 16 | 30 req/s, p95 1.0 s | 0 |
| 757 tenants, listing + page | 1,514 | 16 | 42 req/s | 0 |
| 5 tenants, details | 1,486 | 32 | 84 req/s, p50 239 ms, p95 924 ms | 0 |
| liveness prober, 680 boards | ~1,360 | 432 workers | 3.8 s wall | 0 unknown |

The API is User-Agent-agnostic — a bare UA, `curl/8.7.1`, `python-requests/2.32.3` and a
Mozilla string all 200 — and needs no Referer, cookie or token. `detail_workers` is 16, half the
measured-clean width, for the reason oracle gives (Boards fan out concurrently). Neither host is
seeded in the prober's gate table.

## 9. Tech share and shape of the platform

`tech_filter.is_tech(title, department_name)` over the 8,785 public rows: **2,236 (25.5%)**.
`country` over 8,897 rows: India 6,941 (78%), United States 737 + `USA` 409, Nepal 123, UAE 118,
Australia 42, Singapore 36. The largest tenants are staffing agencies (`hunarstreet-technologies`
643, `tcp-corps` 313, `techhost-services` 218); 679 of the 680 sitemap tenants were hiring on the
day, 8,766 postings between them.

## 10. What the other implementations got wrong

Read so the same traps are pinned by tests here:

- `jobscraper_hourly` skips the detail because "the detail endpoint needs a per-company
  `company_uuid` that isn't derivable from the slug" — it takes `company_slug`.
- `ever-jobs` links `…/{tenant}?job_uuid={id}` and calls it verified — it is the board page.
- All four serve `published_internally` rows.
- `freehire` and `jobdex` read `remote` (OR-ed with the type or the location) — harmless only
  because it is always false.
- `jobdex` reads `department_name` from the merged list+detail dict; both payloads carry it, so
  that one is fine.

## 11. Reproduction

```bash
UA='headstart/0.1'
# the key: slug and uuid are the same filter
curl -sA "$UA" 'https://api.pyjamahr.com/api/career/jobs/?company_slug=pyjamahr' | head -c 200
# one call reads a 643-row Board; `next` is null
curl -sA "$UA" 'https://api.pyjamahr.com/api/career/jobs/?company_slug=hunarstreet-technologies&limit=1000' \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["count"],len(d["results"]),d["next"])'
# dead vs empty: same envelope, different page
curl -sA "$UA" 'https://api.pyjamahr.com/api/career/jobs/?company_slug=notacompany123'
curl -sA "$UA" -o /dev/null -w '%{http_code}\n' https://jobs.pyjamahr.com/notacompany123
curl -sA "$UA" -o /dev/null -w '%{http_code}\n' https://jobs.pyjamahr.com/volopay
# the roster
curl -sA "$UA" https://jobs.pyjamahr.com/sitemap-jobs.xml | grep -c '<loc>'
```
