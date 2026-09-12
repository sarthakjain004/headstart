# ByteDance careers site: what the API actually returns

Measured live 2026-09-11 against `jobs.bytedance.com` / `joinbytedance.com`, the Single source
scraper board this scraper reads (ADR-0139, CONTEXT.md's glossary — a Single source scraper is
its own `ats`, not a slug under one shared platform). Every figure below is a live measurement
(`curl`, no browser), not a reading of the jobhive reference scraper or of ByteDance's own bundle
source.

Evidence base: following the `/en/position` redirect, downloading and reading the site's own
minified JS bundles to find the real API client, 15 sequential search requests spanning three
`limit` values and seven offsets, four header-variation probes, and one cross-domain probe of
`lifeattiktok.com`.

## 0. The public browse page moved domains; the API did not

`GET https://jobs.bytedance.com/en/position` answers a bare **HTTP 302** to
`https://joinbytedance.com/search` — this is the redirect CLAUDE.md's task brief flagged in
advance, and following it lands on a fully client-rendered Next.js app (`bd-career-site`, served
from ByteDance's own Feishu CDN under the path segment `atsx-throne`). The static HTML that page
returns (81,896 bytes) carries **zero** job data — no `job_id`, `position_id`, or similar key
anywhere in it — only `<script>` tags for its JS bundles.

Reading those bundles (specifically module `61215` inside
`_next/static/chunks/8321-22536180820e7ed8.js`) found the real API client rather than guessing at
one: it targets `https://jobs.bytedance.com/api/v1/public/supplier` by default
(`NEXT_PUBLIC_JOB_API_CLIENT` overrides it; unset in the deployed bundle), with three routes:

```
POST {base}/search/job/posts     -> the listing (this scraper's only call)
POST {base}/config/job/filters   -> the UI's filter-dropdown options (not needed to scrape)
GET  {base}/job/posts/{id}       -> declared in the route table, never actually called by the UI
```

A GET on `jobs.bytedance.com`'s bare API root, and a GET on `/api/v1/public/supplier` with no
path suffix, both 200 with an unrelated internal page (title "字节跳动猎头平台" — ByteDance's
internal recruiter/headhunter portal, "Hunter FE", built on different `pinnacle`/`reunion-cn`
static assets). That confirms the same physical host serves at least two unrelated apps behind
one path prefix; only the exact routes above return the job API.

## 1. The listing endpoint, verified live

```
POST https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts
Content-Type: application/json
accept-language: en-US
website-path: en

{"keyword":"","limit":200,"offset":0,"job_category_id_list":[],"location_code_list":[],
 "recruitment_id_list":[],"subject_id_list":[],"tag_id_list":[]}
```

**Both headers are required, not cookie fallbacks.** A request missing `accept-language` or
`website-path` returns `HTTP 400` with the plain-text body `invalid request` (no JSON at all —
`.raise_for_status()` catches it cleanly). `"en-US"` / `"en"` are the app's own default values
(read from its bundle: `Lr = "en-US"`, `RR = "en"`, used whenever no locale cookie is set), and
they are also the only values this scraper needs, since HeadStart's search corpus is English-only
by design (CLAUDE.md). No `Origin`, `Referer`, or auth cookie is required — a bare `curl` with
this repo's own `headstart/0.1` User-Agent gets the identical 200 a browser would.

**One `website-path` value tried and rejected:** `"cn"` returned `HTTP 400` on the same endpoint
(`experienced` and `subject`-family paths were not probed — not real locale paths, guessed from
naming, and moot once `"cn"` had already shown the header is validated against a fixed known
list rather than free-form). This settles the task brief's locale question: there is no live
knob that narrows the board by region, so a single request with `en-US`/`en` reads it all.

## 2. One call reads the whole global board — no region looping needed

With every `*_id_list` filter empty, the very first page (`limit=10, offset=0`) already spanned
nine cities across eight countries: the US (San Jose, Seattle, San Diego, New York, Ashburn),
Singapore, Malaysia, Thailand, the UAE, Hong Kong, the UK, Mexico, and South Korea. `data.count`
was **1,395** at measurement time (2026-09-11) and every page read matched it exactly — no
per-region split was ever needed or found. This matches CLAUDE.md's global (not India-only)
company scope directly: the board itself is already global, with no filter to apply.

## 3. Pagination: offset+limit, no cap found up to 2,000

| `limit` requested | rows returned | `count` |
|---|---:|---:|
| 10 | 10 | 1,395 |
| 100 | 100 | 1,395 |
| 1,000 | 1,000 | 1,395 |
| 2,000 | **1,395** (all of them, one call) | 1,395 |

`offset` past the end of the board returns an empty `job_post_list` with `count` unchanged — the
ordinary end-of-list shape, not an error. No clamp was found at any tested `limit`, but this
scraper still pages at a fixed size (`_PAGE_SIZE = 200`) rather than requesting one giant page:
an un-clamped limit measured today is not a contract, and every other paginated scraper in this
repo (icims, oracle, eightfold) makes the same call for the same reason.

## 4. The listing carries the full description — no detail pass

Every posting in `job_post_list` already has full `description` and `requirement` text. Sampled
100 postings from one page: **0 of 100** show any HTML tag (`<[a-zA-Z/][^>]{0,20}>` never
matched) or any sign of truncation — both fields are plain text with literal `\n` newlines. A
`GET {base}/job/posts/{id}` route exists in the bundle's own route table
(`JOB_DETAIL = "/job/posts/"`) but is never called by the app itself; probed directly with a
plausible empty-ish body, it answered `HTTP 200` with `{"code":-9000002,...,"message":"params is
invalid"}` — a real but unused route, not worth reverse-engineering when the field it would add
is already on every listed row. `has_detail_pass = False`.

## 5. Field-presence census (100 sampled postings, one page, `limit=100&offset=0`)

| field | non-null / present |
|---|---:|
| `city_info` (location) | 100/100 |
| `department_info` | 0/100 |
| `job_post_info.min_salary` / `.experience` | 0/100 each |
| `tag_list` | 0/100 |
| `recruit_type.en_name` | 100/100 — values seen: `Regular`, `Intern` |
| `job_category.en_name` | 100/100 — used as `department` (`department_info` is dead weight) |
| a date field of any kind | **0/100 — no such field exists in the payload at all** |

Unlike icims (fabricates `datePosted` on 22% of boards) or Oracle (states one on every
requisition), there is no date field anywhere on a ByteDance posting to read, fabricate-detect, or
fall back from. `posted_at` is therefore always `None` here — a measured absence, not a missed
field.

`city_info` nests a `parent` chain up to country (e.g. San Jose -> California -> United States of
America). Some tenants state the identical name at every level — Singapore's city, state, and
country `en_name` are all literally `"Singapore"` — so the location builder skips a level whose
name exactly repeats the one just appended, rather than joining three identical strings.

## 6. Rate limit: none found

15 sequential POST requests (10 at `limit=10` across offsets 0-90, 5 at `limit=100` across
offsets 100-500) all returned `HTTP 200`, each taking 2.3-4.3s (consistent with server-side
render latency on a Next.js API route, not throttling) — no 429s, no degradation across the
sequence.

## 7. Job detail page (human-facing link, not used by the scraper for data)

`https://jobs.bytedance.com/en/position/{id}` answers `HTTP 200` for a real id pulled from the
search API (verified against `7673941558289205509`). It is itself a client-rendered shell — a
bogus id (`1`) also answers 200 — so this scraper depends only on the route shape being ByteDance's
own, not on the page's rendered content; `scripts/eval/verify_filters.py`'s `bytedance` entry
checks exactly that shape.

## 8. Does TikTok's careers site share this platform?

Partially confirmed, not fully — this is the explicit cross-check the task brief asked for, since
a parallel effort was building a TikTok (`lifeattiktok.com`) scraper at the same time.

`https://lifeattiktok.com/` and `https://lifeattiktok.com/search` both answered **HTTP 503** on
every attempt on 2026-09-11 (default UA and a full Chrome UA alike), with body
`503 Service Temporarily Unavailable` and header `Server: TLB`. Guessing the ByteDance API shape
directly against that host (`POST lifeattiktok.com/api/v1/public/supplier/search/job/posts`) also
503'd — the same error, not a different one, so the guess neither confirmed nor refuted anything
on its own.

What tips this from "unrelated 503" to "shared infrastructure": the 503 response itself carries
`Server: TLB` and the `x-tt-*` trace-header family (`x-tt-system-error: 23`, `x-tt-trace-id`,
`x-tt-logid`) — the **same** load-balancer name and header convention that every real 200 from
`jobs.bytedance.com` and `joinbytedance.com` carries in this investigation. `TLB` is not a generic
CDN or hosting-provider signature; it is ByteDance's own internal load balancer name appearing on
both domains. That is strong circumstantial evidence the two career sites sit behind the same
internal infrastructure — but `lifeattiktok.com` was genuinely erroring (system error code 23, not
a slow response) throughout this investigation, so its actual request/response shape for a job
search remains unverified. **If the TikTok scraper independently finds the same
`/api/v1/public/supplier/search/job/posts` route live on its own host**, the two should be
reconsidered as one scraper with a brand/company filter rather than two independent ones — see
ADR-0139's registry-cost argument for why that would still need to be a deliberate decision, not
an assumption.

## 9. Scraper shape

- `ats = "bytedance"`, `slug` fixed to `"jobs.bytedance.com"` — never discovered (ADR-0139).
- One endpoint, paginated: `POST {base}/search/job/posts`.
- `has_detail_pass = False` — description+requirement are already on the listing.
- `alias_key()` overridden to return `self.slug` directly: a Single source scraper has no sibling
  hostname to alias against (ADR-0139's consequence section — decide this per scraper, don't
  default it).
- `posted_at` is always `None` (§5).
- Liveness ledger: one hand-entered row in `data/validate/liveness/bytedance.csv`
  (`jobs=1395`, `checked_at=2026-09-11`), not a discovery-tool output.
