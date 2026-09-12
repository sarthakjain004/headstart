# TikTok (lifeattiktok.com) — API measurement, 2026-09-11

`ats="tiktok"`, a Single source scraper (ADR-0139): one company, one board, `slug` fixed to
`"lifeattiktok.com"`, never discovered. All numbers below are from live requests made this session.

## The marketing frontend is not the data source, and is currently unreachable

`https://lifeattiktok.com/` and every path tried under it answered a bare 503
(`503 Service Temporarily Unavailable`, server `TLB`) — measured on:

- The home page, `/search/{id}`, `/robots.txt`, `/sitemap.xml`, `/position/1`, `/job/1`,
  `/careers` — 3 plain attempts each, all 503.
- Four curl_cffi TLS impersonations (`chrome`, `chrome124`, `safari`, `edge`) against the root —
  all 503, three with an identical 590-byte body, one (`safari`) an 188-byte truncated variant of
  the same page.

`/robots.txt` failing identically is the strongest signal: it is the one path a bot wall almost
never gates, since refusing it breaks crawler compliance rather than protecting content. Read
together with the flat, content-free error body across every client shape (not a JS challenge,
not a CAPTCHA page), this looks like a genuine origin outage on the frontend app tier rather than
a fingerprint-based block — but that is inference, not confirmation, since no window opened where
the site was reachable to compare against. The job-detail URL this scraper emits
(`https://lifeattiktok.com/search/{id}`) is accordingly **unconfirmed to render** — it is the
reference implementation's documented convention, not something this session watched resolve.

## The real data source: a separate, healthy API host

```
POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts
Content-Type: application/json
website-path: tiktok

{"limit": 100, "offset": 0, "keyword": "", "category_id_list": [],
 "subject_id_list": [], "location_code_list": [], "job_function_id_list": []}
```

Found by reading the sibling `kalil0321/ats-scrapers` project's own `tiktok.py` scraper (MIT) and
confirmed against the live host — this is a different host from the frontend above and answers
normally.

**Response shape**, `data.job_post_list[]`:

| field | shape | measured presence (100-row sample) |
|---|---|---|
| `id` | numeric string, the native id | 100% |
| `code` | human requisition code (`A28333`) | 100% |
| `title` | string | 100% |
| `description` | plain text (no HTML tags found in any sampled value) | 100% |
| `requirement` | plain text, a second body section | 100% |
| `recruit_type.en_name` | `"Intern"` / `"Regular"` (employment type) | 100% |
| `job_category.en_name` | department (`"Operations"`, `"Machine learning"`, `"R&D"`, …) | 100% |
| `city_info` | nested `{en_name, parent: {en_name, parent: {...}}}` (city → region → country) | 100% |
| `job_subject.en_name` | narrower team/family label | 35% |
| `job_post_info` (salary, level, expiry) | object, every sub-field | **0%** — null on every sampled row |
| `department_info`, `tag_list`, `vacancies`, `process_type`, `channel_online_status` | — | **0%** — null on every sampled row |

**No date field of any kind exists in this payload.** The reference scraper reads
`publish_time`/`post_time` keys; neither appears in any of the 100 sampled rows, nor in
`job_post_info.expiry_time`. `posted_at` is therefore always `None` for this ATS — verified
absent, not merely unmapped.

**`count`** in the envelope is the board's stated total (4,231 at measurement time) and is stable
across repeated calls (12 requests, same value every time).

## Header requirement, measured directly (4 combinations against the same POST body)

| headers sent | result |
|---|---|
| none (no `website-path`, no `Origin`/`Referer`) | HTTP 400, body `invalid request` |
| `website-path: tiktok` only | HTTP 200, full response |
| `website-path: tiktok` + `Origin` | HTTP 200, identical to above |
| `website-path: tiktok` + `Origin` + `Referer` (full browser-shaped set) | HTTP 200, identical |

Only `website-path: tiktok` is required. The reference scraper's own docstring claims
`Origin`/`Referer` are required too ("otherwise the endpoint refuses with 400") — that is not what
this host does today; this scraper still sends them since they cost nothing, but they are
confirmed non-load-bearing.

A **wrong** `website-path` value is also rejected: `website-path: bytedance` against this same
host returns HTTP 400 (see the ByteDance check below), so the header is validated against a fixed
per-tenant value, not merely required to be present.

## Rate-limit sample

12 sequential POST requests, offsets 0 → 1,100 (limit=100 each), ~12s wall clock:

```
0: offset=0    status=200 jobs=100 total=4231 t=0.57s
1: offset=100  status=200 jobs=100 total=4231 t=1.65s
...
11: offset=1100 status=200 jobs=100 total=4231 t=0.82s
```

All 12 returned HTTP 200, zero `Retry-After` headers, no 429/5xx. 12 requests is a small sample —
it rules out an aggressive per-request wall, not a sustained-volume one — but the whole board is
~43 pages at `limit=100` for the measured 4,231-job total, well inside what this sample could miss.

## Coverage re-verification, 2026-09-12: two independent full sweeps + a live failure mode

Two complete offset/limit sweeps of the whole board, run back-to-back (~1s apart), each following
the scraper's own terminator (`not batch or len(batch) < 100 or (total and offset >= total)`):

```
sweep 1: pages=43 collected=4239 unique=4239 stated_total=4239
sweep 2: pages=43 collected=4239 unique=4239 stated_total=4239
sweep1 - sweep2 (in 1 not 2): 0
sweep2 - sweep1 (in 2 not 1): 0
same id set: True
```

The board grew from the 4,231 measured 2026-09-11 to 4,239 (postings open/close between runs, as
expected). Both sweeps landed on an **identical id set** — the `offset >= count` terminator is not
an artifact of one lucky crawl. 43 full pages each, all short only on the final one; no non-final
page came back under 100 in either sweep, so the "a short page is not always the end" trap
oracle.py's own docstring warns about (which this scraper's terminator does *not* independently
guard against) has no live evidence against this ATS today — worth re-checking if a future sweep
ever disagrees with itself.

**A different failure mode does have live evidence, and the scraper's first version missed it.**
Probing a handful of malformed requests (not part of the ordinary crawl) found one that returns
**HTTP 200** with an application-level error in the envelope:

```
negative offset -> HTTP 200 code=-4000001 data=None
huge offset (past total) -> HTTP 200 code=0 data={'job_post_list': [], 'count': 10000}
```

Both answers are HTTP 200 with an empty `job_post_list`, but only one is a real end-of-board
(`code=0`, a real `count`); the other (`code=-4000001`, `data=None`) is a live application-level
failure that the transport layer's own retry/raise never sees, since HTTP itself settled fine. The
scraper's first version read `data or {}` and collapsed the second shape onto the same empty batch
the first shape produces — a mid-crawl failure would have silently read as "the board ended,"
losing whatever pages came after it with no `mark_truncated` call at all. Fixed: `fetch_raw` now
checks the envelope's own `code` before ever reading `data`, and calls `mark_truncated` on anything
nonzero — covered by `tests/test_tiktok.py::test_an_application_level_error_on_http_200_marks_truncated_not_the_end`.
This is the same class of trap `base.py`'s `USER_AGENT` comment documents for SuccessFactors' User-
Agent denylist (a 403 read as "unparseable" for five runs) — a status that looks like ordinary
emptiness until the two causes are told apart.

## No detail pass needed

Every sampled posting carried both `description` and `requirement` inline on the listing, so
`has_detail_pass = False` — this differs from oracle/eightfold, whose listing teasers are
truncated or absent and need a per-job fetch. `description` is `description + "\n\n" + requirement`
through `html_to_text` (the same flattening every other scraper applies), since neither field
carries HTML markup.

## ByteDance-platform-sharing check

A sibling agent is building `ats="bytedance"` against `jobs.bytedance.com`; TikTok is owned by
ByteDance, so it was worth checking whether the two share one backend behind a brand filter before
building a second, separate scraper.

Measured, quickly (a few targeted probes, not a deep investigation):

- `https://jobs.bytedance.com/en/position` (root) returns **HTTP 302** — a different app from
  TikTok's frontend, which returns 503 on everything.
- `POST https://jobs.bytedance.com/api/v1/search/job/posts` (the shape this scraper's own API
  uses, adapted to that host) returns **HTTP 405** — not the same endpoint shape, or not reachable
  the same way.
- `POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts` with
  `website-path: bytedance` (i.e., asking TikTok's own working host for ByteDance's postings)
  returns **HTTP 400** — rejected, not silently served.

**Verdict: not shared, at the level that would matter for scraping.** The two products are on
different hosts with different request shapes; passing the other brand's identifier to either
host does not return the other brand's jobs. The shared `website-path` header convention (and the
overall request/response shape, which closely mirrors this scraper's own) suggests both career
sites are plausibly built on the same internal ByteDance recruiting-platform tooling — but that
resemblance does not make them one endpoint filterable by brand, so each needs (and gets) its own
scraper, matching ADR-0139's decision to model each Single source scraper independently.
