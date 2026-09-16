# Phenom `/widgets`: what the API actually does

**Date:** 2026-09-16 · **Scraper:** `src/headstart/scrapers/phenom.py` · **Tests:**
`tests/test_phenom.py` · **Ledger:** `data/validate/liveness/phenom.csv`

Every number here came from live GET/POST probes against production Phenom tenants on
2026-09-16. The seed was the 98-tenant list published by `kalil0321/ats-scrapers`
(`ats-companies/phenom.csv`, MIT); **91 were reachable**, carrying **155,738 postings** between
them.

This document exists because the upstream implementation we adapted from is wrong in four
load-bearing ways, and because two of this API's failure modes are silent — they answer HTTP 200.

---

## 1. The surface

One endpoint per tenant, discriminated by a `ddoKey` field in the POST body:

```
POST https://{host}/widgets      ddoKey=refineSearch   -> the job list
POST https://{host}/widgets      ddoKey=jobDetail      -> one posting, in full
```

`{host}` is the tenant's own vanity domain (`careers.mastercard.com`, `jobs.tjx.com`,
`workwithus.circlek.com`). The legacy `*.phenompeople.com` namespace is dead, so the host *is*
the slug — the same shape eightfold and icims use.

### No CSRF, no session, no Referer

Upstream seeds a session with `GET /{cc}/{lang}/search-results`, scrapes a CSRF token out of the
cookie jar or the HTML, and replays it as `x-csrf-token`, on the stated grounds that the POST
returns 403 without it.

It does not. A bare client — no prior GET, no cookie jar, no `Origin`, no `Referer`, no token —
returns 200 with the full payload:

| request | result |
|---|---|
| session + `Origin`/`Referer` + csrf | 200, 10 jobs, `totalHits` 1,108 |
| **bare: no GET, no cookies, no headers, no token** | **200, 10 jobs, `totalHits` 1,108** |

No `csrf` cookie is even set on the seeding GET — the jar holds `PLAY_SESSION` and `PHPPPE_ACT`
and nothing else. So the GET is one wasted request per Board, and the scraper does not make it.

---

## 2. Pagination

### `size` clamps at 500, silently

| asked | returned |
|---|---|
| 100 | 100 |
| 200 | 200 |
| 500 | 500 |
| **1000** | **500** (HTTP 200 — a clamp, not an error) |

Upstream uses `PAGE_SIZE = 100`, which is five times the calls for the same rows.

### Reading stops dead at `from + size >= 10000`

This is the sharpest edge in the API. Measured on `jobs.cvshealth.com` (19,649 postings):

| `from` | `size` | sum | rows | `totalHits` reported |
|---:|---:|---:|---:|---:|
| 0 | 10 | 10 | 10 | 19,649 |
| 5,000 | 10 | 5,010 | 10 | 19,649 |
| 9,000 | 500 | 9,500 | 500 | 19,649 |
| **9,500** | **500** | **10,000** | **0** | **0** |
| 9,600 | 400 | 10,000 | 0 | 0 |
| 9,990 | 10 | 10,000 | 0 | 0 |
| 9,999 | 1 | 10,000 | 0 | 0 |

An Elasticsearch `max_result_window`, and the boundary is **exclusive on the sum**: 9,500 reads,
10,000 does not. The last reachable posting is index 9,998.

**The trap:** past the wall `totalHits` itself comes back **0**, not the real total. A walk that
re-reads the total on every page is therefore *told the Board is finished* — it would mark a
19,649-posting Board complete at 9,500 and hand the unreachable 10,150 to `index sync` as
delistings. The scraper reads `totalHits` **once, from page one**, and never again.

Five of the 91 seed tenants are over the wall: CVS Health (19,649), Advance Auto Parts (16,755),
Dollar Tree (14,926), Circle K (10,507), TJX (10,339). Those Boards are truncated by construction
and say so via `mark_truncated` — the unconditional verdict (ADR-0053), because a hard cap is
unreachable rather than merely unread (ADR-0121). Verified end to end against `jobs.tjx.com`:
9,999 unique ids read in 15.6s, `truncated = "9999 of 10339 readable — the result window closes
at 10000"`.

Deep offsets below the wall behave: `from` past a small Board's total returns 0 rows cleanly.

---

## 3. The listing carries no description

The `refineSearch` job object has **no `description` key at all**. It has
`descriptionTeaser` — a marketing blurb — and `ml_job_parser`, whose sub-fields are themselves
truncated at ~200 characters.

On one Mastercard posting (`R-289042`):

| field | source | length |
|---|---|---:|
| `descriptionTeaser` | listing | ~350 |
| `description` | **detail** | **5,121** |

Upstream's parser reads `item.get("description") or item.get("descriptionTeaser")`. The first
operand is always absent, so every posting would be served its teaser — and, worse, would be
*marked described*, so the ADR-0050 store would never fetch the real body.

Hence `has_detail_pass = True` and a per-Job `jobDetail` fetch. Across the 91 reachable tenants,
**90 return a real multi-KB description** on the detail payload.

### The detail payload is 19x cheaper than the job page

The same posting is reachable two ways:

| surface | bytes |
|---|---:|
| `GET /{cc}/{lang}/job/{id}` (HTML, JSON-LD embedded) | 652,123 |
| **`POST /widgets` `ddoKey=jobDetail`** | **34,643** |

Both carry the full description. The scraper uses the JSON.

`jobDetail` is genuinely keyed by id — three distinct ids each returned their own posting — and
takes `jobId` alone (`jobSeqNo` is accepted but unnecessary). **An unknown id is a 200 whose
envelope simply has no `job` key**: a silent empty, not a 404 and not an empty list, so it is
recorded as a detail loss rather than raised on.

---

## 4. The locale prefix decides whether links work

Job URLs are `https://{host}/{cc}/{lang}/job/{id}`. The trailing title slug real links carry is
**cosmetic** — verified on three tenants, `/job/{id}` renders the same posting with its JSON-LD,
and a deliberately wrong slug still resolves by id — so `job_url()` omits it (ADR-0153 hands that
method an id and nothing else).

`{cc}` is **not** always `us`. Derived from the redirect for all 91 reachable tenants:

| prefix | tenants |
|---|---:|
| `us` | 61 |
| `global` | 25 |
| `ca` | 2 |
| `amer`, `gb`, `na` | 1 each |

**30 of 91 are not `us`** — and a wrong prefix does **not** 404. It answers 200 and redirects to
the tenant's landing page, so the link renders a careers homepage with no posting on it: dead, but
alive to anything checking status codes. Upstream hardcodes `us`/`en_us` defaults and omits the
prefix from its job URLs entirely.

The same redirect is the fix. `GET {host}/us/en/search-results` with redirects followed lands on
the tenant's real prefix, which is read straight off the final URL — one request per Board,
self-correcting, and no per-tenant locale column to drift. Verified: all eight prefix families
(`us`, `global`, `ca`, `amer`, `gb`, `na`) produce links that return 200 **with JSON-LD and the
`/job/` path intact**.

The prefix does **not** affect the listing. All seven tenants the upstream seed marks `global`
return byte-identical totals asked as `us` or as `global`, so the `country`/`lang` body fields are
cosmetic for search. The prefix matters for links, and only for links.

---

## 5. Salary: no native field exists

43 of the 91 tenants carry *some* compensation-shaped key. Not one is usable as a field, because
Phenom stores no compensation of its own — it passes through whatever the tenant's backing ATS
sends, under names the tenant invents:

| tenant | key and value |
|---|---|
| cencora | `salary: "Salary"` |
| ace.aaa | `salaryHourly: "false"` |
| stanfordhealthcare, veralto, landolakes | `payment: "0"` |
| ecolab | `compensationGrade: "C5"` |
| mastercard | `compensationGradeId: "Grade 6 - Manager / Consultant"` |
| virginia.edu | `compensationRange: "The pay range for this role is $19.88 - $33.94 hourly. Individual compensation will be determined by…"` |
| ascension | `psStartingWageRate: "From $37.86+ per hour"`, `psMaximumWageRate: "53.44"` |

No key means the same thing on two tenants, so there is nothing to dispatch on. `_salary_field`
returns `None` with this measurement cited, which is the contract base.py's abstract method asks
for. The prose cases are not lost — they sit inside the description the scraper does fetch, where
`salary.extract`'s Tier-2 regex reads them.

## 6. Company name comes free

`jobDetail.companyName` is the real display name (`Mastercard`, `Cisco`, `Zelis`) rather than the
host. So unlike the six scrapers that scrape a page `<title>`, `resolve_company` needs no extra
request and no title patterns — it reads a field already fetched, under the base class's own slug
guard (ADR-0114: a slug is only ever replaced, never the reverse).

## 7. Rate limiting: none found

360 requests against `careers.mastercard.com`, zero 429s, zero non-200 HTTP, no `Retry-After`
ever sent.

| concurrency | requests | throughput | p50 | max | non-200 |
|---:|---:|---:|---:|---:|---|
| 4 | 24 | 2.5 req/s | 1.56s | 2.63s | none |
| 8 | 48 | 5.4 req/s | 1.50s | 2.25s | none |
| **16** | **96** | **14.9 req/s** | **0.88s** | **1.83s** | **none** |
| 32 | 192 | 15.9 req/s | 1.04s | 8.05s | 3 transport SSLErrors |

Throughput knees at 16: doubling to 32 buys 7% more throughput for a 4x worse tail and the run's
only errors. `_DETAIL_WORKERS = 16`.

As with every provider here, "no limit found" is a measurement at this volume, not a guarantee.

---

## 8. Phenom is a skin over other ATSes — which is why the ledger is 17 boards, not 91

`jobDetail.ats` names the system actually holding the requisition:

| backing ATS | tenants |
|---|---:|
| Workday | 68 |
| SuccessFactors | 10 |
| Taleo | 3 |
| CRM / HRM (tenant-internal) | 4 |
| iCIMS, Lever, Ashby, Eightfold, Avature, Brassring | 1 each |

So Phenom is overwhelmingly a career-site front-end, and most of its postings are the same
requisitions HeadStart already reads from the source ATS. Resolving each tenant's backing board
from its `applyUrl` (both Workday URL shapes: `{tenant}.wdN.myworkdayjobs.com` and
`wdN.myworkdaysite.com/recruiting/{tenant}/`) and testing it against the committed liveness
ledgers:

- **74 of 91 tenants — 135,534 postings — already resolve to a Board we hold.**
- **17 tenants / 20,204 postings do not.**

`index_plan.evict_duplicate` groups by `(lowercased Board, native id)` — *within* a Board — so a
posting served under both `phenom:careers.mastercard.com` and `workday:mastercard.wd1…` is two
Boards with two native ids and would be served twice, with nothing to catch it. Onboarding all 91
would put ~135k duplicate rows into the served table.

The ledger therefore carries **only the 17 tenants whose backing board we do not already hold**.
All 17 probe live, 20,214 postings.

Where that list lives matters, because the two files have different durability. The candidate pool
(`data/ats-tenants-merged/phenom.csv`, source `curated2026`) is **gitignored**, like every other
ATS's pool — candidate-grade input, not a record. The committed
`data/validate/liveness/phenom.csv` is the durable one and the only thing
`load_active_companies` reads, so the 17-Board gate survives in the ledger whether or not the pool
is ever regenerated. Widening this provider means adding the other 74 hosts back to the pool and
re-probing — a deliberate act, and one that should not happen until cross-ATS deduplication exists.

Two consequences worth recording:

- **The original India rationale for Phenom is void.** CLAUDE.md's TODO ranked it on Mastercard
  (250 India roles) and Adobe (148). Both are Workday-backed and already in the Workday ledger, so
  neither is new coverage. The 17 boards that *are* new are mostly European and North-American
  enterprises (DHL, Allianz, BAE, Merck KGaA, Kuehne+Nagel, Hugo Boss, UCB, Bell, BCG).
- **Discovery is no longer the blocker the 2026-07 research called it.** The seed list is public
  and the prefix derives itself. What limits this provider is duplication, not findability.

---

## 9. Reproduction

```bash
# the listing, with no session of any kind
curl -s -X POST https://careers.mastercard.com/widgets \
  -H 'Content-Type: application/json' \
  -d '{"lang":"en_us","deviceType":"desktop","country":"us","pageName":"search-results",
       "ddoKey":"refineSearch","from":0,"size":10,"jobs":true,"counts":true,
       "siteType":"external","keywords":"","global":true,"clearAll":false,
       "jdsource":"facets","isSliderEnable":false,"pageId":"page20",
       "selected_fields":{},"locationData":{},"all_fields":["category"]}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["refineSearch"]; print(d["totalHits"], len(d["data"]["jobs"]))'

# the result window: the second call returns 0 rows AND totalHits 0
for FROM in 9000 9500; do
  curl -s -X POST https://jobs.cvshealth.com/widgets -H 'Content-Type: application/json' \
    -d "{\"lang\":\"en_us\",\"country\":\"us\",\"pageName\":\"search-results\",\"ddoKey\":\"refineSearch\",
         \"from\":$FROM,\"size\":500,\"jobs\":true,\"counts\":true,\"siteType\":\"external\",
         \"keywords\":\"\",\"global\":true,\"clearAll\":false,\"jdsource\":\"facets\",
         \"isSliderEnable\":false,\"pageId\":\"page20\",\"selected_fields\":{},
         \"locationData\":{},\"all_fields\":[\"category\"]}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin)['refineSearch']; print('$FROM', d['totalHits'], len(d['data']['jobs']))"
done

# the prefix redirect that reveals a non-`us` tenant
curl -s -o /dev/null -w '%{url_effective}\n' -L https://jobs.tjx.com/us/en/search-results
```

The scraper itself runs a Board end to end:

```bash
python3 -c "
from headstart.scrapers.registry import get_scraper
s = get_scraper('phenom', 'careers.zelis.com'); jobs = s.fetch()
print(len(jobs), s.company, s.truncated, sum(1 for j in jobs if j.description))"
```
