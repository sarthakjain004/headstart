# Group 5 — direct big-tech career sites (amazon, apple, google, meta, tesla, tiktok, uber, bytedance)

OURS: `scratchpad/wt-main/src/headstart/scrapers/{ats}.py` (origin/main 8efb9a85)
THEIRS: `scratchpad/kalil/src/ats_scrapers/scrapers/{ats}.py` (kalil0321/ats-scrapers @ 6b44a1b)

Every "measured" figure below is from a live probe run **2026-09-22** during this review, sample
size stated inline. Nothing was bulk-crawled: at most one full listing page per host, plus ~50
Apple detail fetches.

**Browser/paid-service dependency (asked explicitly):** of these eight, only **tesla** and **meta**
depend on a browser upstream — both on `cloakbrowser`, a stealth-patched Chromium fork
(`scrapers/_cloakbrowser.py:1-32`), plus the **Evomi residential proxy** for Tesla from a
datacenter IP (`tesla.py:15-19`). `_browserbase.py` (paid SaaS) is wired for the same two as an
alternative path but both currently prefer cloakbrowser; avature is the only live Browserbase user.
**uber** uses `httpcloak` TLS impersonation (`uber.py:70`) — free, and our `headstart.http`
curl_cffi transport already clears the same Cloudflare wall. amazon/apple/google/tiktok/bytedance
are plain HTTP on both sides.

---

## Findings, most valuable first

### 1. Apple — `standardWeeklyHours` on the **listing** is the real employment-type signal; the detail field is the string `"Standard"`
- **Theirs:** `apple.py:206-213` — derives `employment_type` (`FULL_TIME` if `hours >= 30` else
  `PART_TIME`) and `commitment` (`"40h/week"`) from the listing row's `standardWeeklyHours`. No
  detail fetch involved.
- **Ours:** `apple.py:327` sets `employment_type=detail.get("employmentType")`, and
  `apple.py:231-240` **disables the ADR-0048 detail skip for the whole Board** on the stated
  grounds that "this payload is also the only source of `employment_type` … Skipping it for an
  already-described Job would blank that field on every later run."
- **Measured today:** `standardWeeklyHours` present and non-zero on **20/20** listing rows sampled
  (values 20, 35, 38, 40, 42). Detail `employmentType`: **absent on 10/10** details sampled off
  page 3, and present-but-equal-to `"Standard"` on **30/30** details sampled off pages 50/150/250 —
  including 6 postings whose listing says **20 h/week** (`US-Technical Specialist`, retail). So the
  column we pay a per-Job fetch to keep is either null or a constant, while the listing carries the
  distinguishing number for free.
- **Verdict: ADOPT** — read employment type from `standardWeeklyHours` (our column is a display
  string, so `"20h/week"` or `"Part-time"` both fit). The knock-on is the bigger prize: once
  `employment_type` comes off the listing, the detail pass supplies only `description`, which is
  exactly the ADR-0048/ADR-0050 case, so `needs_detail` can be re-enabled and a steady-state run
  drops from ~4,400 detail fetches (6,235 postings x ~70.8% tech) to roughly the day's new postings.
  Re-measure `employmentType` across a wider slice before deleting the field, and bump
  `DERIVATIONS_VERSION` only if this feeds a derived column (it doesn't today — `employment_type` is
  a raw display string per ADR-0019).
- **Confidence: CODE-EVIDENCED + measured (50 details, 60 listing rows).**

### 2. Uber — a multi-location posting keeps only its first office, and `Address` is the fuller string
- **Theirs:** `uber.py:138-141` joins **every** `Locations[]` entry with `"; "`, and
  `uber.py:248-257` prefers each entry's own `Address` field, falling back to City/Region/Country.
- **Ours:** `uber.py:173-186` reads `Locations[0]` only and rebuilds `City, Region, Country`.
- **Measured today (one request, whole board, 535 postings):** **149/535 (27.9%)** list 2-5
  locations (e.g. id `302437` → San Francisco / New York / Seattle — we serve only San Francisco).
  `Address` is non-empty on **535/535** and is strictly richer on some rows: `Address` =
  "Istanbul, İstanbul, Türkiye" where City/Region/Country = "Istanbul, Türkiye" (`Region` is `""`).
- **Verdict: ADOPT** (join with `"; "`, prefer `Address`) — it matches what our own `apple.py:285-291`
  and `google.py:152-163` already do, and location is a user-facing filter, so dropping two of three
  offices is a silent recall loss on a quarter of the board.
- **Confidence: measured, 535/535 rows.**

### 3. Amazon — same multi-location loss (but **do not** copy their row-per-location design)
- **Theirs:** `amazon.py:231-269` emits **one `Job` per (posting × location)** with a synthetic
  `ats_id` of `"{req}@loc{idx}"`, decoding the JSON-string-encoded `locations` array at
  `amazon.py:315-341`.
- **Ours:** `amazon.py:250` uses `normalized_location` (falling back to `location`) — one string,
  one row.
- **Measured today (page 1, 100 postings):** **39/100** have 2+ entries in `locations`, and
  `normalized_location` names only one of them (id `10555899`: normalized "Tel Aviv-Yafo, Tel Aviv,
  ISR" while `locations` = `["Haifa, Haifa, ISR", "Tel Aviv-Yafo, Tel Aviv, ISR"]`).
- **Verdict: ADOPT the joined string / REJECT the row multiplication.** Our Job identity is
  `{ats}:{slug}:{native_id}` and `index_plan.evict_duplicate` groups within a Board — minting
  `@loc1` ids would double-serve postings and interact badly with the Unconfirmed/eviction
  bookkeeping. Join the decoded `normalizedLocation` values the way `apple`/`google` do.
- **Confidence: measured, 100 rows.**

### 4. Uber salary — their structured read is dead, but the check exposed a real `salary.py` gap of ours
- **Theirs:** `uber.py:204-207` sets `salary_min/max/currency/period` from
  `Salary.MinValue/MaxValue/Currency/Period` and `salary_summary` from `Salary.Description`.
- **Ours:** `uber.py:166-170` returns `None` from `_salary_field`, citing those fields as null and
  `Salary.Description` as duplicating the main `Description`.
- **Measured today (535 postings):** `Salary.MinValue` non-null on **0/535**; `ExperienceLevel`
  non-null on **0/535**; `Salary.Description` non-empty on **271/535**, carrying a numeric range on
  **263**. Our docstring's duplication claim **holds**: every `$` figure in `Salary.Description`
  also appears in the main `Description` on **263/263**.
- **But:** running our own `salary.extract(None, html_to_text(Description))` over those 263 rows
  finds a range on only **176** — **87 misses**, all on Uber's house phrasing
  `"USD $122,000 per year - USD $135,000 per year"` (the `per year` between the two figures breaks
  the range pattern). Parsing `Salary.Description` alone recovers **0 of 87**, so this is a
  `salary.py` pattern gap, not a missing source field — a Tier-1 `_salary_field` would not fix it.
- **Verdict on theirs: THEIRS-IS-WORSE** (their `parse_salary_range`
  `enrichment/derived.py:60-120` fails the same phrasing and then falls through to
  `_SALARY_SINGLE_RE`, emitting `min == max == 122000` — a wrong number rather than none).
  **Verdict for us: separate ADOPT-class gap** — add the `X per year - Y per year` shape to
  `salary.py` and bump `DERIVATIONS_VERSION` (it changes already-indexed rows: 33% of Uber's
  stated ranges, ~16% of the whole board).
- **Confidence: measured, 535 rows, run against our own extractor.**

### 5. Meta — theirs needs a stealth browser and captures only the first GraphQL wave
- **Theirs:** `meta.py:56-60` returns `[]` outright when cloakbrowser is absent;
  `meta.py:73-111` launches it, listens for `graphql` responses for `_GRAPHQL_SETTLE_MS = 8_000`
  (`meta.py:42`) and **never scrolls** ("we don't bother scrolling", `meta.py:38-42`), so the job
  set is whatever the first render fired. Descriptions then come from the public detail page's
  JSON-LD over plain httpx (`meta.py:207-246`).
- **Ours:** `meta.py:113-142` — `robots.txt`-discovered `/jobsearch/sitemap.xml` + per-job JSON-LD,
  no browser at all.
- **Measured today:** our sitemap returns **986** unique job ids (200, no UA gate) — our docstring's
  952 has simply grown. Upstream's un-scrolled capture cannot plausibly exceed one page of results.
- **Verdict: THEIRS-IS-WORSE.** The one field they get that we don't is `teams`/`sub_teams` →
  `team`/`department` (`meta.py:136-137`) against our `department=None` (`meta.py:201`, backed by
  0/80 JSON-LD payloads carrying any category key). **NOT-APPLICABLE** — it only exists behind the
  browser path we deliberately don't take.
- **Confidence: CODE-EVIDENCED + measured (sitemap).**

### 6. Tesla — theirs gets descriptions we don't, via batched in-page `fetch()` inside a stealth browser
- **Theirs:** `tesla.py:181-239` — a single `page.evaluate` per batch of 10 running
  `Promise.all(ids.map(fetch('/cua-api/careers/job/{id}')))` **from inside the already-warmed page
  context**, yielding `jobDescription` / `jobResponsibilities` / `jobRequirements` /
  `jobCompensationAndBenefits` / `department` (formatted at `tesla.py:319-346`). No navigation per
  job.
- **Ours:** `tesla.py:60-67` — `has_detail_pass = False`; **every Tesla Job ships with
  `description = None`** (~8,105 postings), on the stated ground that a detail costs a full browser
  navigation per job.
- **Measured today:** `GET https://www.tesla.com/cua-api/apps/careers/state` via curl_cffi Chrome
  impersonation → **HTTP 429 `{"cpr_chlge":"true"}`**, exactly as our docstring records. The wall is
  real and unchanged.
- **Verdict: NOT-APPLICABLE as written** (cloakbrowser fork + Evomi residential proxy is a paid
  dependency on CI — disqualifying) **but NEEDS-LIVE-CHECK on the mechanism**: our own negative
  result used pydoll's `tab.request` (one page-context fetch) and got 429; theirs is a batched
  `Promise.all` of ordinary `fetch()` calls issued by page JS after a scroll/mouse warm-up. That
  is the one variant our measurement did not try, and Tesla is our largest description-less Board,
  so it is the highest-value re-test in this group. Everything else in their Tesla path is worse:
  they take `department` from the detail (`tesla.py:174-175`) where we already resolve it from
  `lookup.departments` (`tesla.py:322`), and they never read `lookup.types`, so their
  `employment_type` is always null where ours is populated (`tesla.py:329`).

### 7. Uber / ByteDance — theirs raises and loses the whole Board where ours records a measured shortfall
- **Theirs:** `uber.py:88-108` raises `ScraperError` on a total that moved during pagination, on any
  duplicate id, and on `len(unique) != total`. `bytedance.py:116-164` raises on a short page before
  the count, an empty page before the count, and a final count mismatch, retrying the entire
  catalogue twice (`bytedance.py:74-81`) and then raising.
- **Ours:** `uber.py:118-137` re-sizes a single page against the freshly-stated total
  (`_MAX_ATTEMPTS`), `bytedance.py:159-184` marks truncation.
- **Verdict: THEIRS-IS-WORSE** under our semantics — a raise yields zero rows, which for us is
  strictly worse than a short-but-flagged scrape (ADR-0053/ADR-0121: an unauthoritative scrape
  leaves eviction scope rather than deleting live jobs). Their Uber path also paginates
  (`pagesize=1000`, `uber.py:84-92`) where we deliberately fetch one page sized to `totalJobs`
  because page boundaries measurably drop a different job each walk.
- **Confidence: CODE-EVIDENCED.**

### 8. TikTok / ByteDance — their `job_post_info` salary and `publish_time` dates are fields that do not exist
- **Theirs:** `tiktok.py:140-143` and `bytedance.py:188-189, 239-245` read
  `job_post_info.min_salary/max_salary/currency`; both read `publish_time`/`post_time` for
  `posted_at` (`tiktok.py:143`, `bytedance.py:245`).
- **Ours:** documents both as absent and returns `None` (`tiktok.py:206, 214-217`;
  `bytedance.py:216, 225-228`).
- **Measured today (100 rows per tenant, one request each):** `job_post_info` is present on
  **100/100** on both hosts but every one of `min_salary`, `max_salary`, `currency`,
  `required_degree`, `experience` is **null**; and there is **no time- or date-shaped key anywhere**
  in the row (`['channel_online_status','city_info','code','department_info','description','id',
  'job_category','job_post_info','job_subject','process_type','recruit_type','requirement',
  'tag_list','title','vacancies']`).
- **Verdict: ALREADY-HAVE / THEIRS-IS-WORSE** — their `posted_at` is silently always `None` while
  claiming to read a field.
- **Side correction to our own docstrings (not an upstream finding):** `job_subject` is non-null on
  **38/100** (TikTok) and **48/100** (ByteDance) today, contradicting both modules' "null on every
  sampled row". It is a campus-cohort label ("PhD Graduates - 2027 Start", "Project Intern"), not a
  team, and `job_category` is non-null on 100/100 — so our `department = job_category` stays right
  and `tiktok.py:204`'s fallback to subject simply never fires. Worth correcting the prose if either
  file is touched.
- **Confidence: measured, 200 rows.**

### 9. TikTok — theirs never checks the envelope's `code`
- **Theirs:** `tiktok.py:77-88` reads `.get("data") or {}` and breaks on an empty
  `job_post_list` — an HTTP-200 application error (`{"code": -4000001, "data": null}`) reads
  identically to "the board ended". Their own ByteDance scraper does check (`bytedance.py:100-104`);
  the TikTok one was not given the same treatment.
- **Ours:** `tiktok.py:146-159` checks `code` first and marks truncation.
- **Verdict: OURS AHEAD / THEIRS-IS-WORSE.** **Confidence: CODE-EVIDENCED.**

### 10. Google — theirs pays one HTML detail fetch per job for what our listing already carries
- **Theirs:** `google.py:110-117` fans out a detail GET per job at `DETAIL_CONCURRENCY = 8`, then
  stitches five known `<h3>` sections with BeautifulSoup (`google.py:224-316`), plus
  Material-icon chips for location and team (`google.py:204-212`).
- **Ours:** `google.py:236-294` reads the `AF_initDataCallback('ds:1')` payload straight off each
  listing page — `has_detail_pass = False`.
- **Measured today (page 1, 20 jobs):** the `ds:1` record carries about-the-job (idx 10),
  min+preferred qualifications (idx 4), responsibilities (idx 3), every location (idx 9) and the
  brand (idx 7). **14/20** postings state a US pay range, all of them inside idx 10 which we already
  fold into `description`, and our `salary.extract()` recovers **14/14**. Index 19 is a strict
  subset of index 4 (minimum quals only, unlabelled); index 15 is location/EEO notes.
- **Their `team` chip:** measured — **no team or org string exists in the `ds:1` record** (idx 5 is
  a `projects/gweb-careers-proto/tenants/...` resource path; idx 7 is the brand "Google"/"YouTube",
  which we already emit as `company`, `google.py:310`). The `corporate_fare` chip is therefore
  almost certainly that same brand. **ALREADY-HAVE (different column)**; not worth 3,239 detail
  fetches to restate.
- Their terminator is also weaker: break on a page with no new ids (`google.py:99-101`) versus our
  fan-out + short-page frontier walk + `mark_truncated` (`google.py:269-293`).
- **Verdict: THEIRS-IS-WORSE.** **Confidence: measured, 20 jobs / 1 page.**

### 11. Apple — theirs requires a CSRF handshake we measured unnecessary, and scrapes HTML where we call JSON
- **Theirs:** `apple.py:76-83` GETs `/api/v1/CSRFToken` and **raises** if the
  `x-apple-csrf-token` header is missing, before any search. Descriptions come from the job page's
  `window.__loaderData__ = JSON.parse("…")` blob, character-walked out of the HTML
  (`apple.py:321-404, 406-434`).
- **Ours:** `apple.py:142-162` POSTs `/api/v1/search` with no cookie/CSRF/Referer;
  `apple.py:260-269` fetches `GET /api/v1/jobDetails/{jobNumber}` as JSON.
- **Measured today:** a bare `POST /api/v1/search` (no cookies, no CSRF, no Referer) returned
  **200** with `totalRecords: 6235`; a bare `GET /api/v1/jobDetails/{id}` returned **200** JSON.
- **Verdict: THEIRS-IS-WORSE** (an extra request plus a failure mode we don't have, and a much
  heavier description path). Their sibling-row description broadcast (`apple.py:379-403`) is
  **NOT-APPLICABLE** — an artifact of their row-per-location design.
- **Confidence: measured, 1 search + 1 detail.**

### 12. Amazon — their facet discovery leans on an endpoint they document as broken; their cap handling is silent
- **Theirs:** `amazon.py:71-77` POSTs `/api/jobs/search` purely to read `found` and the
  `businessCategory` facet, documenting at `amazon.py:11-16` that the same endpoint's `filters`
  body is silently ignored. Past the cap they bucket by `business_category[]`
  (`amazon.py:127-134`) — the same subdivision we use. A page past the 10k ceiling is swallowed as
  `handled={400}` → empty page (`amazon.py:99-105`), and a bucket that itself exceeds 10k is capped
  at `min(count, PAGINATION_CAP)` (`amazon.py:128`) **with no truncation signal**.
- **Ours:** `amazon.py:154-165` reads the same facet off the GET endpoint itself
  (`facets[]=business_category`) — one endpoint, no reliance on a known-broken POST — and
  `amazon.py:206-215` marks the board truncated when any bucket exceeds the ceiling, plus
  `amazon.py:234-240` against the facet sum.
- **Verdict: OURS AHEAD.** Their `is_intern → INTERN` mapping (`amazon.py:363-384`) and
  `apply_url` (`amazon.py:182`) are the only extras: measured `is_intern` true on **0/100** and
  `job_schedule_type` = "full-time" on 100/100 on today's page, and we have no `apply_url` column.
  **NOT-APPLICABLE / low value.** Their `description_short` fallback (`amazon.py:187-192`) is
  strictly poorer than our `description` + `basic_qualifications` + `preferred_qualifications`
  assembly (`amazon.py:300-318`), which is where the years-of-experience and pay phrasing lives.
- **Confidence: CODE-EVIDENCED + measured (100 rows).**

### 13. Amazon — theirs has no story at all for the 200-with-CAPTCHA-HTML wall
- **Theirs:** the shared `Fetcher` turns a 200 whose body isn't JSON into `MalformedJSONError`
  (`fetch.py:62-69, 137-143`), which is a `ScraperError` — it propagates out of `afetch` and kills
  the whole run.
- **Ours:** `amazon.py:177-200` counts the page lost and
  `mark_truncated_unless_negligible` (`amazon.py:234-240`) reports the shortfall — the behaviour
  our eviction semantics require.
- **Verdict: OURS AHEAD.** **Confidence: CODE-EVIDENCED.**

### 14. Fields they populate that our schema simply has no slot for
`apply_url`, `requisition_id`, `country_iso`, `lat`/`lon`, `region`, `language`, `commitment`,
`team` (as distinct from `department`), `application_deadline`, `raw` overflow — all
`models.py:216-518` on their side. Measured where it mattered: Uber `CountryCode` is null on
**535/535** today (so their `country_iso` is empty anyway) while `LocationPoint.coordinates` is
populated. **NOT-APPLICABLE** — adding columns is an index-schema change (README §"The served
table" + `tests/test_readme_schema.py`), not a scraper change, and nothing here argues for one.

---

## Where OURS is clearly ahead (one line each)

- **meta**: sitemap + JSON-LD reads the whole board with no browser; theirs returns `[]` without a stealth Chromium and never scrolls even with one.
- **google**: the full description, all locations and the brand come off the listing's `ds:1` payload — theirs pays ~3,200 HTML detail fetches for less.
- **apple**: no CSRF handshake, and the detail is a 1-call JSON endpoint rather than a 600 KB React page scraped with a hand-written JS-string walker.
- **amazon**: facets read from the working GET endpoint, cap breaches and CAPTCHA-HTML pages both surface as measured truncation instead of silence or a crash.
- **uber**: single page sized to `totalJobs` sidesteps the replica-ordering job loss their paginated walk would hit; ours also survives drift instead of raising.
- **bytedance/tiktok**: the envelope `code` check turns an HTTP-200 application error into a truncation mark rather than "the board ended" (theirs only does this on bytedance).
- **tesla**: `employment_type` resolved from `lookup.types`, `department` from `lookup.departments`, both off the one state document; a zero-listing capture is marked truncated rather than evicting 8k jobs.
- **everywhere**: `mark_truncated` / `mark_truncated_unless_negligible` / `report_detail_gaps` give every shortfall defined eviction semantics; upstream has no equivalent concept at all.
- **everywhere**: `url_shape` + the `verify-search-filters` harness keep the emitted job link honest; upstream asserts URL shapes only in comments.

## Recommended actions, in order

1. **apple** — derive `employment_type` from `standardWeeklyHours` (listing), then re-enable
   `needs_detail` and drop ~4,400 detail fetches per steady-state run. Re-measure `employmentType`
   over a wider slice first.
2. **uber** — join all `Locations[]`, preferring `Address` (27.9% of the board affected).
3. **amazon** — join the decoded `locations` array instead of `normalized_location` alone
   (39% of page-1 postings affected). Do **not** copy the row-per-location id scheme.
4. **salary.py** — add the `USD $X per year - USD $Y per year` range shape; 87 of 263 stated Uber
   ranges are lost to it today. Bump `DERIVATIONS_VERSION` in the same change.
5. **tesla** — re-test a batched in-page `Promise.all(fetch(...))` for `/cua-api/careers/job/{id}`
   from our existing pydoll session. It is the one variant our 429 measurement did not cover, and
   Tesla is our largest description-less Board. Do not adopt cloakbrowser or a residential proxy.
