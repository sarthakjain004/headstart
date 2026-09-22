# Group 4 (SMB): darwinbox · keka · join/join_com · recruitee · rippling

1:1 read of both implementations in full, plus ~20 single live requests to real hosts
(no bulk probes). Paths below are relative to:

- OURS: `scratchpad/wt-main/src/headstart/…` (origin/main `8efb9a85`)
- THEIRS: `scratchpad/kalil/src/ats_scrapers/…` (`kalil0321/ats-scrapers` @ `6b44a1b`)

Live probes run 2026-09-22. Every "measured" claim below names its sample size.

---

## Ranked findings

### 1. RIPPLING — our `department` parser reads a key the API never ships. ADOPT. CODE-EVIDENCED + MEASURED.

**Theirs:** `scrapers/rippling.py:129-134` —
`dept.get("label") or dept.get("id")` when `department` is a dict.

**Ours:** `scrapers/rippling.py:24-33` — `_department_of()` does
`dept = dept.get("name")` when `department` is a dict, else the bare value.

**Measured** (`GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs`,
3 boards — `10fitness` 9 items, `15-lightyears-careers` 1, `aalo-atomics` 66; 76 items total):
the listing item has exactly 5 keys — `uuid`, `name`, `department`, `url`, `workLocation` —
and `department` is **always** `{"id": …, "label": …}`. `name` appears in **0 of 76**.
So `_department_of()` returns `None` for every rippling posting, always.

Two consequences, both real:

- `Job.department` is permanently null for rippling (`scrapers/rippling.py:174,180`).
- `fetch_raw` passes `_department_of` as the department accessor to
  `tech_detail_wanted` (`scrapers/rippling.py:100-102`), so the ADR-0017 pre-detail gate is
  **title-only** for rippling. `base.py:449-490` warns about exactly this: on Oracle a
  title-only gate dropped 61.5% of a board's tech postings because `tech_filter` rule 4
  promotes a vague title on a technical department. `aalo-atomics` ships `Engineering`,
  `Nuclear Operations`, `Manufacturing Staff` as labels — the exact input rule 4 wants. In a
  recall-biased pipeline this is a silent recall defect, not cosmetics.

Also: our own in-code justification is a misdiagnosis of our own parser —
`scrapers/rippling.py:96-99` says *"`department` is empty on every one of the 1,515 rippling
postings in the 2026-09-17 corpus, so that fallback recovers nothing"*. It is empty because we
read `name`. Fix the accessor and re-run the measurement before re-asserting that sentence.

Fix is one line in `_department_of`; no `DERIVATIONS_VERSION` bump (department is a raw
FACT_FIELD, re-observed on rescrape). Add a regression test with the real
`{"id": "Club", "label": "Club"}` shape.

---

### 2. KEKA — they carry a `salaryPeriod` label map; our docs closed that question as "undecodable". ADOPT (as a measurement pass, not a blind copy). NEEDS-LIVE-CHECK.

**Theirs:** `scrapers/keka.py:48-59`
```
_SALARY_PERIODS   = {1: "HOUR", 3: "MONTH", 4: "YEAR"}
_SALARY_PERIOD_LABELS = {0: "Not Available", 1: "Hourly", 2: "Bi Weekly", 3: "Monthly", 4: "Annual"}
```
consumed at `scrapers/keka.py:431-450` (`salary_period` on the Job) and
`scrapers/keka.py:246-250` (label into `raw`).

**Ours:** `scrapers/keka.py:146-161` and `salary.py:320-332` (`_field_keka`) deliberately emit
no period. `docs/salary-extraction/keka.md:15-27` and `:328-330` close it as *"confirmed
UNDECODABLE … not a gap this or any future pass can close without new information."*

An independent implementation asserting a concrete map **is** new information, so the verdict is
worth reopening. My own single-tenant probe is consistent with it
(`GET https://100.keka.com/careers/api/embedjobs/default/active/{uuid}`, 95 jobs):

| `salaryPeriod` | n | magnitudes seen |
|---|---|---|
| 0 | 85 | mixed (25,000 / 300,000 / 600,000 INR) |
| 3 | 3 | 17,000-21,000 ✓monthly, 30,000-40,000 ✓monthly, 350,000-480,000 ✗ |
| 4 | 7 | 250,000-300,000 … 1,800,000-2,200,000 — all annual-scale (6/6 plausible) |

That is **one tenant** — this repo's own "One sample generalised into documented fact" lesson
applies, so do not ship the map off this. But note the shape of the payoff: if `0 = Not
Available`, the map changes nothing for the 89% that state 0 (we already omit the period there),
and adds a period only for the 1-4 rows. Our doc's stated counter-argument — *"the same enum
value spans LPA-shorthand and absolute-rupee scales"* — is an argument about magnitude
ambiguity, and does not refute the label map itself.

Recommended: a real multi-tenant pass (sample 1-4 rows across ≥50 boards, check magnitude
against the asserted label) before adopting. Reading a period into `Job.salary` needs no
`DERIVATIONS_VERSION` bump — the raw field string changes, so `salary_inputs_moved` reprocesses.

**Related, and this one is clearly THEIRS-IS-WORSE:** `scrapers/keka.py:432-433` returns an
*empty* salary whenever `salaryPeriod == 0`, discarding real `minimum`/`maximum`. On the tenant
above that is 85 of 95 jobs, many with genuine amounts. Do not copy that branch.

---

### 3. JOIN — our `city` parser reads keys the API never ships; every join location loses its city. ADOPT (latent). CODE-EVIDENCED + MEASURED.

**Theirs:** `scrapers/join_com.py:344-363` `_flatten_location` reads
`cityName`/`city` + `countryName`/`country` off the city object.

**Ours:** `scrapers/join.py:195-206` reads `city.get("label") or city.get("name")`.

**Measured** (`GET https://join.com/api/public/companies/{id}/jobs?locale=en&page=1&pageSize=5`,
2 tenants — `01informatica` (id 82672), `1komma5grad` (id 30107), 10 items): the `city` object
carries `cityName` and `countryName` (plus `lat`/`lng`/`countryCode`/`googlePlaceId`);
`label` and `name` are absent/None in **10 of 10**. So our `location` collapses to the bare
country ("Spain", "Germany") for every join posting.

Latent only because `join` is in `registry.DISABLED_ATS` (`scrapers/registry.py:116`) — but it
is the first thing to fix if join is ever re-enabled.

---

### 4. KEKA — they support non-`default` career portals; we hardcode `default`. ADOPT (pending population check). NEEDS-LIVE-CHECK.

**Theirs:** `scrapers/keka.py:159-184, 292-332` — the slug is a full
`https://{host}/careers[/{portal}]` URL, and both API calls are portal-parameterised:
`/careers/api/organization/{portal}/careerportalinfo` and
`/careers/api/embedjobs/{portal}/active/{identifier}`.

**Ours:** `scrapers/keka.py:66-72` hardcodes `default` in both paths and derives the host from a
bare tenant slug.

This is the only genuine **listing-surface** gap in the group. A tenant running its board under a
named portal (multiple brands/entities on one Keka host) is either unreadable or partially read
by us today. I found no evidence of one in our own ledger: 47 of the `keka.csv` rows carry a path
after the host, but they are careers/job-page URLs (`/careers`, `/careers/jobdetails/4260`), not
portal segments. **Measure the population before building it** — if it is zero, this is a
NOT-APPLICABLE, and the change is not free (the slug contract and `board_key()` identity both move).

---

### 5. DARWINBOX — they read a board total (`job_counts`) as a terminator; we have no shortfall detector at all. ADOPT if the field is real. NEEDS-LIVE-CHECK (blocked).

**Theirs:** `scrapers/darwinbox.py:182-184` — `total = _coerce_int(payload.get("job_counts"))`,
then `if total is not None and len(jobs) >= total: break`.

**Ours:** `scrapers/darwinbox.py:226-236` terminates only on a short page, and
`mark_truncated` fires only on the 99-page cap.

Why this matters here specifically: under ADR-0053/ADR-0121 an unmeasured shortfall is a real
defect — a board that quietly returns fewer than its own stated total stays in eviction scope and
its missing ids get evicted. A stated total is exactly the input
`mark_truncated_unless_negligible` (`base.py:340`) wants, and darwinbox currently gives it none.

**I could not verify `job_counts` exists.** A direct `POST
/ms/candidateapi/job/alljobs?companyId=main` against `1mg.darwinbox.in` with a full browser UA +
`Origin`/`Referer`/`X-Requested-With` returned **HTTP 403** with a Cloudflare interstitial — the
exact wall ADR-0056 / `docs/darwinbox/cloudflare-wall.md` describe. Verify through the existing
`browser_http` path before acting; do not build the guard on their code alone.

Same file, same call, two things **not** to copy: their `PAGE_SIZE = 10`
(`scrapers/darwinbox.py:29`) against our 100 is 10x the requests through a Cloudflare-walled
origin, and their job URL `{base}/ms/candidate/careers/{id}`
(`scrapers/darwinbox.py:295`) is the legacy route our `job_url` documents as a 2.4 KB stub that
redirects to the v2 careers *home*, dropping the job (`scrapers/darwinbox.py:107-118`).

---

### 6. KEKA — company name is a structured field on a response we already fetch. ADOPT (cheap). CODE-EVIDENCED + MEASURED.

**Theirs:** `scrapers/keka.py:344-358` — `careerportalinfo.shortName or .name`.

**Ours:** `company_name.py:118` uses the `Careers at {Name}` / `{Name} Careers` `<title>`
wrapper on `board_page()`, measured at **~11% (92 of 819)** coverage
(`company_name.py:25`), and `scrapers/keka.py:51-65` justifies a separate GET for it.

**Measured** (`GET https://100.keka.com/careers/api/organization/default/careerportalinfo`, 1
tenant): the response carries `name = "Bright Future"` and `shortName = "Bright Future"` — and
`fetch_raw` already fetches this exact response (`scrapers/keka.py:75`). Free name, no extra
request, no HTML parsing. Worth a ≥100-board presence/quality check against ADR-0114's bar
before wiring, and keep the title path as a fallback.

(Same probe also confirmed our UUID extraction: `careersBackgroundPath` really does carry the
tenant uuid — `/ats/documents/7e2f830e-…/careersportalbackground/….png`.)

---

### 7. KEKA — listing fields we leave on the floor. Mixed. CODE-EVIDENCED + MEASURED.

Measured key set on the 95-job `100.keka.com` payload:
`departmentIdentifier, departmentName, description, excerpt, experience, id, jobLocations,
jobNumber, jobType, ogImagePath, publishedOn, publishedSinceDays, salaryRange,
salaryRangeFormat, skillNames, title`.

| Field | Theirs | Ours | Verdict |
|---|---|---|---|
| **multi-`jobLocations`** | joins all with `"; "` (`keka.py:390-422`) | takes `[0]` only (`keka.py:115-124`) | **ADOPT** — measured 3 of 95 jobs are multi-location; we silently drop the rest |
| `jobType` (1/2) | maps 1→PART_TIME, 2→FULL_TIME (`keka.py:44-47`) | left unmapped, explicitly (`keka.py:139-140`) | **ADOPT-candidate, NEEDS-LIVE-CHECK** — measured distribution 2×94 / 1×1 is consistent with their map; unsourced in their code, so measure across tenants first |
| `salaryRangeFormat` | `salary_summary` | unused | NOT-APPLICABLE — no column, and it carries no period (measured: `"INR 3,00,000.00 - 3,60,000.00"`) |
| `jobNumber`, `skillNames`, `publishedSinceDays`, `departmentIdentifier` | into `raw` | unused | NOT-APPLICABLE — no columns in our `Job` (`models.py:13-32`) |
| `experience` | `_minimum_experience()` → first int (`keka.py:497-504`) | raw string → `experience.py` cascade | **THEIRS-IS-WORSE** — measured values `"3 - 5 years"`, `"0-1"`, `"5 to 15 years"`, `"16 - 20 year"`; their `^\s*(\d{1,2})` throws the range away and our Tier-1 field parser does not |
| `country_iso`/`region` | hand-maintained 31-country map (`keka.py:60-125`) | n/a | NOT-APPLICABLE — no column |

---

### 8. JOIN — a tenant-mismatch guard on company-id resolution. ADOPT (small, latent). CODE-EVIDENCED + MEASURED.

**Theirs:** `scrapers/join_com.py:135-176` — after resolving the company id it checks the
embedded `domain` against the requested slug and raises `CompanyNotFoundError` on mismatch,
documenting join.com serving placeholder/cached pages for new tenants (`"company":{"id":233,…}`
= greenteg) that would attribute another tenant's jobs to the slug being scraped.

**Ours:** `scrapers/join.py:64-80` reads the structured
`props.pageProps.initialState.company` object — immune to their *"first numeric id is a
department"* trap, but **not** to the placeholder-page one: we'd take whatever company object the
page served, including its `name` (`scrapers/join.py:191`).

**Measured** (`join.com/companies/01informatica`): the structured company object carries
`domain: "01informatica"` alongside `id: 82672` and `name`. So the guard is a two-line addition
on the read we already do. Latent — join is disabled.

---

### 9. RECRUITEE — custom-domain slugs. ADOPT-lite. CODE-EVIDENCED, one live check negative.

**Theirs:** `scrapers/recruitee.py:44-82` accepts a full careers URL and calls
`{url}/api/offers`, so a Recruitee tenant on a vanity domain is scrapable without knowing its
`{slug}.recruitee.com` host.

**Ours:** `scrapers/recruitee.py:72-73` only ever builds `{slug}.recruitee.com/api/offers/`.

This is a discovery/reachability capability we lack, and it is cheap. Two caveats:

- It does **not** unlock the company CLAUDE.md names. Measured: `https://hiring.lenskart.com/api/offers/`
  **302s to `https://ainterviews.com/job_board/lenskart_ho/`** and serves HTML (`<title>Job Board</title>`),
  not a Recruitee offers payload — that tenant is a white-label front, not a Recruitee custom domain.
- It must not change `job_url`. Our `_offer_url` (`scrapers/recruitee.py:16-31`) deliberately
  builds links on the tenant host because 9 of 25 sampled custom hosts were dead, and
  `url_shape` (`:70`) asserts it. Fetch-from-vanity / link-to-tenant-host would need the tenant
  slug from somewhere else.

---

### 10. RIPPLING — detail `workLocations` shape handling. ADOPT (defensive, one line).

**Theirs:** `scrapers/rippling.py:205-214` handles a `workLocations` entry that is a **dict**
(`label`/`displayName`) as well as a string; `_extract_location` (`:217-226`) also tries
`displayName`/`city`/`country` on the listing's `workLocation`.

**Ours:** `scrapers/rippling.py:36-41` — `wls[0] if wls else None`, which would put a raw dict
into `Job.location` if the array is ever object-shaped. Measured: the *listing*'s `workLocation`
is `{"label": …, "id": …}` on 76/76 items and we read `label` correctly; I did not measure the
*detail*'s `workLocations`, which is the array this concerns. Cheap guard either way.

---

### 11. DARWINBOX — fields they populate that we null. Mostly NOT-APPLICABLE.

`country_iso`/`region` from a 52-country map (`scrapers/darwinbox.py:59-112`), `team` from
`functional_area_name`, `requisition_id` from `internal_job_code`, `employment_type` mapped to an
enum from `emp_type_name` (`:242-322`) — our `Job` (`models.py:13-32`) has no country, region,
team or requisition columns, and we keep `employment_type` as the provider phrases it by design.

One genuine field difference: they read `experience` from
`experience_from` / `experience_from_num` (`:313-315`); we read `j.get("experience")`
(`scrapers/darwinbox.py:281`). Which key the payload actually carries I **could not verify** —
the Cloudflare 403 above blocked it. NEEDS-LIVE-CHECK through `browser_http`; if both exist,
`experience_from` is a numeric lower bound and our free-text field may be the richer input for
the `experience.py` cascade (which wants the range, not just the floor).

Their tenant handling is **not** a discovery trick: `_resolve_tenant` (`:216-240`) just parses
slug/TLD/URL the caller already supplies and defaults to `.in`. It does nothing for CLAUDE.md's
"Darwinbox — 11 curated companies … only a careers-page/redirect tenant scan is missing" gap.
Ours is strictly better here anyway — we probe both TLDs live (`scrapers/darwinbox.py:199-206`).

---

## Where THEIRS is broken (do not port)

- **join_com: `pageSize=100` (`join_com.py:79`) is rejected outright.** Measured:
  `pageSize=5` → 200 with items; `pageSize=50` and `pageSize=100` → `[{"param":"pageSize",
  "msg":"Invalid value","value":"100"}]`. Their `payload.get("items")` on that **list** raises
  `AttributeError` — their join scraper cannot complete a single board. Our `_PAGE_SIZE = 5`
  comment (`scrapers/join.py:34`) is correct and still current, and our list-shaped-response
  branch (`scrapers/join.py:99-105`) handles it as a truncation.
- **join_com: wrong pagination key.** Theirs reads `pagination.totalPages`
  (`join_com.py:87-88`); measured, the object is
  `{'rowCount': 154, 'pageCount': 31, 'page': 1, 'pageSize': 5}`. With the default
  `pagination.get("totalPages", page)` they would break after page 1 — a 154-job board read as 5.
  Ours reads `pageCount` (`scrapers/join.py:107`).
- **join_com: wrong job URL.** `{BASE}/companies/{slug}/jobs/{idParam}` (`join_com.py:198-200`)
  → measured **308**; ours `{BASE}/companies/{slug}/{idParam}` (`scrapers/join.py:61-62`) → **200**.
- **rippling: listing `createdAt` is dead code.** `scrapers/rippling.py:156-158` reads
  `item.get("createdAt") or item.get("created_at")`; measured, the listing item has exactly
  `uuid, name, department, url, workLocation` on 76/76 items. Their `posted_at` comes only from
  the detail's `createdOn`, same as ours (`scrapers/rippling.py:181`). ALREADY-HAVE.
- **rippling: no salary at all.** `payRangeDetails` is kept in `raw` and never parsed
  (`join_com`-style, `scrapers/rippling.py:174-178`). Ours does majority-(currency,frequency)
  grouping across entries (`scrapers/rippling.py:194-242`).
- **rippling: `commitment` from `employmentType.id`** (`scrapers/rippling.py:196-199`) — ours
  documents `.id` as tenant free text with **347 distinct spellings, 130 singletons** measured
  live, and uses `.label` (`scrapers/rippling.py:44-55`).
- **keka: `raise` on a duplicate job id** (`scrapers/keka.py:206-210`) fails the whole board.
- **keka: no soft-404 handling.** Keka answers **HTTP 200** with an HTML `Invalid Tenant` /
  `Forbidden Access` page; theirs would fail in `_extract_identifier`. Ours detects and calls
  `note_unreadable_board` (`scrapers/keka.py:74-97`).
- **recruitee: a payload with no `offers` key** yields `[]` silently (`recruitee.py:72-73`) — in
  our pipeline that lands the board in `boards_ok` and evicts its rows two runs later. Ours
  calls `note_unreadable_board` (`scrapers/recruitee.py:82-98`).
- **recruitee: `posted_at` prefers `created_at` over `published_at`** (`recruitee.py:123`);
  measured on `113zelfmoordpreventie`, those differ (`created_at 2026-08-11 11:10:17 UTC` vs
  `published_at 2026-08-11 13:32:45 UTC`). Ours prefers `published_at`.
- **recruitee: their `_parse_iso` buys us nothing.** They handle the
  `"2026-08-11 13:32:45 UTC"` form (`recruitee.py:218-239`) — measured, that is the real shape —
  but our `posted_at_comparable` is `posted_at LIKE '____-__-__%'`
  (`ingest/index.py:269`), which that string satisfies, and date-prefix ordering against an ISO
  cutoff is correct. NOT-APPLICABLE.
- **darwinbox: `PAGE_SIZE = 10`** and the legacy `/ms/candidate/careers/{id}` job URL (above).
- **Whole-suite:** no `mark_truncated` equivalent anywhere. Their only shortfall signal is a
  `ScraperError` at a page cap (`darwinbox.py:187-191`). Under ADR-0053/0121 a silent short read
  is an eviction bug, so nothing in their pagination design transplants without our accounting.

---

## Where OURS is clearly ahead (one line each)

- **darwinbox**: browser escalation through the measured Cloudflare wall (ADR-0056) vs. their single `cloak` engine that my probe shows returns 403.
- **darwinbox**: v2-vs-legacy portal detection via `companyinfo.new_careers`, so job links actually resolve.
- **darwinbox**: structured `salary_min/max/currency/timeframe` with a documented 14-currency fallback; theirs parses no salary.
- **darwinbox**: `tool_tip_locations` recovery of the real city list behind "Multiple Locations", and the embedded-`\r` strip (31/67 sampled jobs).
- **keka**: `Invalid Tenant` / `Forbidden Access` soft-200 detection, and the "alive but uuid-less" board marked truncated rather than reported empty.
- **keka**: `_format_num` — the `:g` scientific-notation bug that discarded 27% of rejected salary values.
- **keka**: `description or excerpt` fallback; theirs drops the posting body when `description` is empty.
- **join**: reads `/api/public/jobs/{id}` JSON for the description (with `intro`/`tasks`/`requirements` fallback) instead of their HTML+JSON-LD detail page.
- **join**: salary read off the *listing* item, verified live against the detail — no dependence on a detail fetch succeeding.
- **recruitee**: the structural remote-sentinel detector (~12% of ~19k offers, 8 markers across 7 locales) that keeps a real city instead of a localized "Remote job" string.
- **recruitee**: job links pinned to the tenant host after measuring 9 of 25 custom hosts dead, with `url_shape` asserting it.
- **recruitee**: `description + requirements` concatenated so experience extraction and the embedding see the qualifications text (theirs does this too — we also feed `experience_code` through).
- **rippling**: majority-unit `payRangeDetails` salary (the real journaltech USD/CAD mislabel case), `is not None` on `rangeStart`/`rangeEnd`.
- **rippling**: ADR-0017 pre-detail tech gate + ADR-0048 alignment-safe `attach_details`, and `companyName` from the detail rather than the slug.
- **all five**: per-cause detail-loss accounting (`note_detail_loss` / `note_detail_unattempted` / `report_detail_gaps`) — theirs silently `return`s on every detail failure.

---

## Explicit answer: does anything in their `join_com` change our `DISABLED_ATS` call?

**No.** Their implementation uses the same two surfaces we do — the `/companies/{slug}` page for
the company id and `/api/public/companies/{id}/jobs` for the listing
(`join_com.py:69-97`) — over the same corpus, and reaches *less* of it than we do (the
`pageSize=100` crash and the `totalPages` first-page-only break, both measured above). Nothing
there widens the board, changes its composition, or bears on the ~99.99%-non-tech measurement in
`scrapers/registry.py:92-95`. Keep `join` disabled.

---

## Things I could not verify (stated as open, not as fact)

- **darwinbox `job_counts`** and **`experience_from` vs `experience`** — blocked by a live 403
  Cloudflare interstitial on `1mg.darwinbox.in` (full browser UA + `Origin`/`Referer`/
  `X-Requested-With`). Needs the `browser_http` path.
- **keka `salaryPeriod` / `jobType` maps** — one tenant, 95 jobs. Consistent with their maps; not
  proof. Needs a multi-tenant pass.
- **keka non-`default` portals** — no instance found in our own ledger; population unmeasured.
- **rippling detail `workLocations`** element shape — listing measured, detail not.
