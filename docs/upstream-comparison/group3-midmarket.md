# Group 3 (mid-market): bamboohr, gem, jazzhr, jobvite, personio, teamtailor

Ours: `origin/main` @ `8efb9a85`, worktree `scratchpad/wt-main`.
Theirs: `kalil0321/ats-scrapers` @ `6b44a1b`, clone `scratchpad/kalil`.
All 12 scraper files read in full, plus both sides' shared infra (`scrapers/base.py`,
`registry.py`, `models.py`; theirs' `fetch.py`, `client.py` surface, `enrichment/derived.py`).

Live probes run 2026-09-22 from this session, all single cheap requests, no bulk sweeps:
teamtailor `xefi-1721226094` / `lovisacareers` / `zunogroup` / `zynka` / `zyense`
(jobs.json + jobs.rss + robots.txt), personio `ztk` (`/xml`, `/search.json`,
`/api/careers/jobs/list/`), jazzhr `zoominfo` / `zotecpartners` / `zymochem` (listing + one
detail each), jobvite `zoomcare` / `zones` (search + one detail each), gem `zillow-group-ats`
and one fabricated slug, bamboohr `a3` (widget + one detail). Sample sizes are stated with each
claim; a 2-3 host probe is evidence, not a census.

---

## Findings, most valuable first

### 1. teamtailor — `/jobs.rss` carries `department` and `remoteStatus`; our `jobs.json` carries neither — **ADOPT** (additive join), CODE-EVIDENCED + live-measured

**Theirs** reads the RSS feed: `kalil/.../teamtailor.py:33` (`RSS_TEMPLATE`), `:117`
(`tt:department`), `:125` + `:159-170` (`_extract_remote` on `<remoteStatus>`), `:143-156`
(`tt:locations/tt:location` city/country).

**Ours** reads the JSON Feed: `wt-main/.../teamtailor.py:51` (`jobs.json`), and then
`:108` — `department=None,  # not exposed in the public feed` — and `:104` `remote=is_remote(location)`,
a substring check on the location string.

**Measured live 2026-09-22.** `jobs.json`'s `_jobposting` block really has no department and no
remote field: its keys on `xefi-1721226094` are exactly `@context, @type, baseSalary, datePosted,
description, hiringOrganization, identifier, jobLocation, title`. The RSS for the same board has
`<tt:department>` on 95 of 98 items and `<remoteStatus>` on 98 of 98; on `zunogroup`, 27/27 and
27/27. **The join key is exact**: RSS `<guid>` is byte-identical to the jobs.json `id` (UUID) —
set-equal on both boards (98/98 and 27/27).

Why this is the top item: `department` is what `tech_filter` rule 4 reads
(`src/headstart/tech_filter.py:389`, and the precedence list in its module docstring — rule 4 is
the recall booster that promotes a vague title sitting in a technical department). Teamtailor is
department-blind today across **3,832 hiring Boards / 54,811 jobs** in the committed ledger, so
every vague title there is decided on the title alone. `remoteStatus` also replaces a
location-substring guess with the ATS's own field.

**How to adopt, and the two traps:**

- **Join, do not switch.** Theirs keys `ats_id` on the URL's numeric id
  (`kalil/.../teamtailor.py:106-113`); ours keys on the feed UUID. Swapping the listing surface
  re-ids the entire Board and `index prune` evicts its whole population as off-Board (ADR-0023).
  Keep `jobs.json` as the list; fetch `jobs.rss` once per Board and join on `guid == id`.
- **RSS is capped at 100 and has no working page parameter** (measured: `lovisacareers/jobs.rss`
  and `jobs.rss?page=2` both return 100 items and byte-identical 445,713-byte bodies, while
  `jobs.json?page=N` does walk). So enrichment covers the first 100 items only — **136 of 3,832
  hiring Boards (3.5%)** are at or over 100. That is acceptable *because it is additive*: a
  partial join fills some departments and leaves the rest `None`, exactly today's state. It must
  never be allowed to shorten the list (ADR-0053), which is why RSS cannot be the listing.
- Cost: +1 request per Board. teamtailor has no detail pass today (`has_detail_pass` is not set),
  so this makes it 2 requests/Board for small boards rather than 1 — it does **not** become a
  per-Job detail ATS, and ADR-0050's description store is untouched (description still comes from
  `content_html`).
- **Map the remote vocabulary from the live values, not from theirs.** Theirs maps only `fully`
  and `none` (`kalil/.../teamtailor.py:166-170`). Measured vocab across 5 boards: `zunogroup` =
  2 `fully` / 4 `hybrid` / 8 `none` / 13 `onsite`; `xefi` = 97 `none` / 1 `onsite`;
  `lovisacareers` = 2 `hybrid` / 91 `none` / 7 `onsite`. Under their map `onsite` falls to
  `None`. The repo's existing convention (`ashby._remote`, `workday._remote_from`,
  `bamboohr._remote`) is: `fully` → True, `none`/`onsite` → False, `hybrid`/`temporary` → None.

`robots.txt` was checked for a third surface (the memory rule): it names only
`/app/, /messages/, /messenger/, /facebook/tab/, /jobs/internal/` and the sitemap — nothing with
richer fields.

### 2. bamboohr — the widget's own department blocks; ours takes department only from the detail JSON — **ADOPT** (small), CODE-EVIDENCED + live-measured

**Theirs**: `kalil/.../bamboohr.py:83-93` (`_DEPARTMENT_BLOCK_RE`, `_DEPARTMENT_NAME_RE`),
`:182-200` walks each `<li id="bhrDepartmentID_…" class="BambooHR-ATS-Department-Item">` block so
every job inherits its department name, `:202-211` then sweeps any positions after the last block
so legacy department-less tenants still parse.

**Ours**: `wt-main/.../bamboohr.py:152` collects ids from `_POSITION` alone and `:219` takes
`department=(opening.get("departmentLabel") …)` — detail-only.

**Measured live 2026-09-22 on `a3.bamboohr.com/jobs/embed2.php`**: 7 department blocks, 28
positions, both regex families matching 28/28. The detail JSON for job `577` returns
`departmentLabel = 'ASH IV'` — **the same string the widget header already carried**. So the
listing department is free and identical.

Two payoffs, both real under our constraints:

- **Recall protection.** Today a failed detail fetch nulls `department`, `posted_at`,
  `experience`, `employment_type`, `salary` *and* the canonical location at once. Department is
  the one of those that feeds the tech gate, so a lost detail page can demote a tech job out of
  the index. A listing-derived department survives that.
- **Cost.** bamboohr is the only ATS in the repo with `has_detail_pass = True` and **no**
  `tech_detail_wanted` gate (`grep`: the gate is used by apple, eightfold, gem, jazzhr, phenom,
  rippling, smartrecruiters, successfactors, trakstar, workday, zwayam — not bamboohr). Today it
  fetches a detail for every posting on every Board, every run. With title + department on the
  listing, `tech_detail_wanted(rows, _row_title, _row_department)` becomes available on exactly
  the pattern `gem.py:316` and `jazzhr.py:292` already use — and those two document the
  measured-tolerance caveat to re-run (`parse` lets the detail's department win, so the gate is
  an approximation and must be measured against the real `filter_tech` verdict before shipping).

**This is most of what their extra 109 lines buy.** The 346-vs-237 delta is: the department-block
walk (~40 lines, the only capability we lack), `_EMPLOYMENT_TYPE_MAP` (~20, enum normalisation we
deliberately don't do — `Job.employment_type` keeps the provider's phrasing,
`src/headstart/models.py:30`), `_apply_opening_to_job`'s field-by-field isinstance/not-already-set
guards (~57 lines doing what our single `Job(...)` construction does in one), the
`require_host_label` constructor (~18), the single-job `get_description` path (~12), and one dead
regex: `_DETAIL_DESCRIPTION_RE` (`kalil/.../bamboohr.py:109-112`) is defined and never referenced.

### 3. bamboohr — parse-drift guard: positions present, none parsed — **ADOPT** (2 lines), CODE-EVIDENCED

**Theirs**: `kalil/.../bamboohr.py:166-170` — if `bhrPositionID_` appears in the widget HTML but
`_parse_widget` returned nothing, raise.

**Ours**: `wt-main/.../bamboohr.py:144-151` only checks for the `BambooHR-ATS-board` wrapper. A
page that *has* the wrapper but whose row markup moved parses to zero jobs and returns
`{"page": page, "details": {}}` — downstream that is a live Board with nothing open, so ADR-0083
withholds its ids for one scrape and `sync` evicts the entire Board on the next.

Our own jazzhr (`jazzhr.py:266`) and jobvite (`jobvite.py:247`) already raise on precisely this
shape for their own listings; bamboohr is the gap. Ours' `_POSITION` is the looser regex (id only,
no class attribute), so drift risk is lower than theirs — but the guard is two lines and the
failure mode it covers is a silent full-Board eviction.

### 4. personio — their JSON listing surface exists, and is strictly poorer than our `/xml` — **THEIRS-IS-WORSE**, live-measured

**Theirs**: `kalil/.../personio.py:38` — `ENDPOINTS = ("/search.json", "/api/careers/jobs/list/")`,
then a per-job HTML detail fetch for descriptions (`:150-166`, scraping
`page_jobDescription` with BeautifulSoup).

**Ours**: `wt-main/.../personio.py:189` — `https://{host}/xml`.

**Measured live 2026-09-22 on `ztk.jobs.personio.com`** (this is the dimension-1 question, so it
was checked rather than reasoned about):

| | `/xml` | `/search.json` | `/api/careers/jobs/list/` |
|---|---|---|---|
| status | 200, 320,203 B | 200, 12,701 B | **404** |
| items | 30 `<position>` | 30 | — |
| description | `<jobDescriptions>` CDATA | `"description": ""` on every item | — |
| posted date | `createdAt` | absent | — |
| salary | `salaryInformation` (structured) | absent | — |
| experience | `yearsOfExperience` | absent (only `seniority`) | — |
| offices | `office` + `additionalOffices` | `office` + `offices` | — |

`/search.json`'s full key set: `category, department, description, employment_type, id, keywords,
name, office, offices, schedule, seniority, subcompany`. It is a strict subset that would cost a
per-Job HTML detail pass to recover descriptions and would still lose `posted_at`, the Tier-1
salary field (`ours personio.py:355`) and the `yearsOfExperience` range (`ours personio.py:106`,
whose own measurement says preferring that range over `seniority` corrects `min_years` on 36.33%
of positions). **Do not adopt.** The one useful confirmation: `offices` is a list on the JSON side
too, which independently corroborates that our `additionalOffices` join (`personio.py:59-103`) is
reading real multi-office data and not an XML artefact.

### 5. personio — the 429-that-is-not-a-throttle: theirs walks straight into it — **THEIRS-IS-WORSE**, CODE-EVIDENCED

The brief asked what they assume here. They assume nothing — they have no redirect handling at
all. `Fetcher` defaults to `follow_redirects=True` (`kalil/.../fetch.py:180`, `:229-234`), and 429
is in `_RETRYABLE_STATUSES` (`fetch.py:87`), retried 3× with exponential backoff and honoured
`Retry-After` (`fetch.py:339-355`) before raising `RetryExhaustedError`.

So a departed personio tenant — `307 → https://personio.com`, whose Vercel bot mitigation answers
429 to a non-browser fingerprint — reads to them as a rate limit, forever. Ours
(`wt-main/.../personio.py:258-285`) sends `allow_redirects=False`, classifies by *where the
Location points* (`_redirect_host`, `personio.py:27-48`), and emits an off-host redirect in the
exact shape `board_failures.is_gone` recognises so ADR-0058 quarantine can retire it after five
agreeing runs. Ours also deliberately refuses to build that message from origin-controlled text
(`personio.py:274-276`) — a subtlety theirs has no equivalent of.

Under our semantics theirs is the expensive failure: a 429 never ages a Board, so those tenants
would fail every run indefinitely. Nothing to take.

### 6. jobvite — their strict `?p=N` walk would raise on real boards — **THEIRS-IS-WORSE**, CODE-EVIDENCED

**Theirs**: `kalil/.../jobvite.py:102-146` builds `search?p={page}` directly and raises on: a
changed `total` (`:116`), a start-row gap (`:120`), a row count not matching the stated range
(`:125`), **any duplicate `ats_id`** (`:131-134`), and finally `len(jobs) != reported_total`
(`:142-146`).

**Ours**: `wt-main/.../jobvite.py:254-296` walks the board's own `jv-pagination-next` link,
scans for `/{slug}/job/{id}` paths only, de-duplicates, stops on a page that adds nothing new, and
marks truncated *only* at the `_MAX_PAGES` cap with a next link still offered.

Our own measurement (`jobvite.py:40-49`) falsifies their central invariant: `fprs` states 1,933,
the walk visits 1,933 slots across 39 pages, and **1,896 ids are distinct — 37 postings occupy two
slots**. Under theirs that Board raises `duplicate job id` and yields nothing. Their design does
encode one thing ours refuses — treating the counter as authoritative evidence of a shortfall —
and ours is right to refuse it, for exactly this reason (`jobvite.py:46-49`: a wrong
`mark_truncated` is permanent, ADR-0053 has no drain).

### 7. jobvite — listing-derived title/location would stop a lost detail page dropping the Job — **option, not a recommendation**, CODE-EVIDENCED

**Theirs**: `kalil/.../jobvite.py:244-299` parses `.jv-job-list` rows (bs4) for title + location,
so a detail-page failure keeps a usable row (`:188-195` logs and returns the listing-derived job).

**Ours**: `wt-main/.../jobvite.py:216` takes ids only; `:391` drops any Job whose page was lost,
and `:229` marks the whole Board truncated for that count. Since ADR-0053's exclusion has no
drain, a Board that loses a page on every run leaves the eviction scope permanently and serves its
closed postings indefinitely.

Against that: our own measurement (`jobvite.py:29-38`) is that five row templates are live and
each row-shaped parse tried returned **zero rows on a different 19%, 15% and 1.4% of Boards, and
did so silently**. A best-effort listing title that only ever *reduces* the truncation count (used
solely to emit a Job that would otherwise be dropped, never to gate anything) is safe in
direction, but it re-opens the template zoo that measurement closed. Put it in front of the user
as a choice; don't take it silently.

### 8. jazzhr / jobvite — `@graph`- and list-tolerant JSON-LD extraction — **ALREADY-SUFFICIENT**, live-measured (probe, not census)

**Theirs**: `kalil/.../jazzhr.py:412-434` and `kalil/.../jobvite.py:440-461` both walk
`@graph` and top-level lists, and match the tag with `<script[^>]*type=["']application/ld\+json["']`.

**Ours**: `wt-main/.../jazzhr.py:112` — `_LD = re.compile(r'<script type="application/ld\+json">…')`,
requiring that exact attribute spelling, and `_ld_of` (`:135-145`) only inspects a top-level dict.
`wt-main/.../jobvite.py:125` + `:310` is the same shape and reads the first blob only.

I expected this to explain part of our documented 69.7% JobPosting coverage on jazzhr, so I
checked. **It does not.** On `zoominfo`, `zotecpartners` and `zymochem` (one detail page each,
2026-09-22) every ld+json tag is literally `<script type="application/ld+json">` — no extra
attributes, no single quotes, no `@graph`, no top-level list. The pages without a JobPosting are
missing the *blob* (only the `Organization` one is served), which is exactly what our own
1,526-page figure says. So the tolerant parser is cheap hardening with **no measured recall gain**;
adopting it is optional and would need a real census (not 3 pages) to claim otherwise.

### 9. jobvite — `jobLocationType`, multi-`jobLocation`, lone-`value` salary — **NOT-APPLICABLE** (measured inert), live-measured

**Theirs**: `kalil/.../jobvite.py:354-358` reads `jobLocationType: TELECOMMUTE/REMOTE` into
`is_remote`; `:480-499` joins *every* `jobLocation` with `"; "`; `:529-530` falls back to
`value.value` when `minValue`/`maxValue` are absent.

**Ours**: `wt-main/.../jobvite.py:401` `remote=is_remote(location)`; `_location` (`:163-182`)
reads the first `jobLocation` only; `_salary_field` (`:424-428`) requires `minValue`/`maxValue`.

Measured 2026-09-22 on `zoomcare/od4IAfwj` and `zones/oVUKAfwT`: the JobPosting LD key set is
`@context, @type, baseSalary, datePosted, description, employmentType, hiringOrganization,
identifier, industry, jobLocation, title` — **no `jobLocationType` key at all**, no
`applicantLocationRequirements`, exactly one `jobLocation`, and `baseSalary` is the empty skeleton
ours documents (`currency:""`, `minValue:""`, `maxValue:""`, `unitText:""` on zoomcare; `PKR` +
`Annually` with both amounts empty on zones — a currency and a period with no figure, which ours
correctly declines). Two postings is a probe; if any of these three is ever wanted, census it
first. Note our jazzhr `_salary_field` *does* handle the lone-`value` shape
(`wt-main/.../jazzhr.py:399-401`) while jobvite's does not — an internal inconsistency worth a
cheap census if jobvite salary coverage is ever revisited.

### 10. gem — their `errors` → `CompanyNotFoundError` guard never fires — **THEIRS-IS-WORSE**, live-measured

**Theirs**: `kalil/.../gem.py:164-168` treats `result["errors"]` as board-not-found.

**Ours**: `wt-main/.../gem.py:234-242` branches on `data is None` and calls
`note_unreadable_board`; the docstring (`gem.py:26-33`) states the live/dead indistinguishability
and points liveness at the board page instead.

Measured 2026-09-22: `boardId = "this-slug-definitely-does-not-exist-xyz123"` returns **HTTP 200,
`errors: None`, `data` present, `jobPostings: []`** — identical in shape to `zillow-group-ats`
returning 6. Their guard is dead code on a bad slug; the departed board reaches them as a live
board with zero openings, which under our semantics is the shape that deletes a company.

### 11. gem — `requisitionId`, `teamDisplayName`, `startDateTs` — **NOT-APPLICABLE / THEIRS-IS-WORSE**, CODE-EVIDENCED

Theirs requests all three (`kalil/.../gem.py:80-98`) and fills `requisition_id`/`team`
(`:292-299`) plus `startDateTs` as a `posted_at` fallback (`:283-290`). Ours drops them
deliberately (`wt-main/.../gem.py:147-149`) on a measurement — `startDateTs` populated **0/159**
and structurally unreachable on a public listing (`gem.py:60-65`). Theirs also builds the
timestamp with a naive `datetime.fromtimestamp(ts)` (local time, no tz) where ours uses
`tz=UTC` (`wt-main/.../gem.py:433`).

`Job` has no `requisition_id` or `team` column, so nothing to take today. The forward-looking
note: `requisitionId` is exactly the cross-ATS dedup key CLAUDE.md's phenom entry says we lack
("widen it only if cross-ATS dedup is ever built"). If that ever gets built, gem states it for
free on the query we already send.

### 12. gem — detail batch size — **ALREADY-AHEAD**, CODE-EVIDENCED

Theirs caps at 20 and calls it conservative (`kalil/.../gem.py:43-46`). Ours measured 20 → 1,000
all succeeding (1,000 ops in 8.0s) and 2,000 failing with HTTP 500, and sits at 100
(`wt-main/.../gem.py:35-40`, `:121`) — 5× theirs, three requests for the largest observed board.
Ours also verified batch response *ordering* (7/7 runs incl. shuffles and a repeated id,
`gem.py:42-48`), which theirs relies on (`zip(batch, results)`, `:217`) without checking.

### 13. Shared fetch layer: their retry/backoff/`Retry-After` + declared httpcloak escalation — **mixed, mostly NOT-APPLICABLE**, CODE-EVIDENCED

`kalil/.../fetch.py:274-358` is a clean one-place retry layer: 3 attempts, exponential backoff,
numeric `Retry-After` honoured, 404 → `CompanyNotFoundError`, 403/406 escalated once to an
httpcloak TLS-impersonation engine when the scraper declares it (`:311-338`), and
`MalformedJSONError` for a 200 whose body isn't JSON (`:137-144`).

Ours has retry/backoff in `headstart.http` plus ADR-0063 spare-egress rotation keyed on a declared
per-scraper `egress_fallback_on` (`wt-main/.../base.py:~206-228`), which is the
per-origin-budget mechanism theirs has no analogue of. The only idea without a counterpart is
**declared TLS-fingerprint escalation** — and this group supplies the counter-example: at
`curl_cffi impersonate="chrome"` the personio marketing host *still* refused us
(`wt-main/.../personio.py:211-218`), and three verified-distinct WARP addresses were refused too.
Per CLAUDE.md's "a status code doesn't imply its mechanism", there is no measured blocking case in
this group that escalation would fix. Their jazzhr `client_kind="auto"` path also **skips detail
enrichment entirely** once it falls back (`kalil/.../jazzhr.py:171-177`), which under our
semantics silently halves a Board's fields without marking anything.

One concrete regression in theirs worth naming: bamboohr details pass
`handled=frozenset(range(300, 600))` (`kalil/.../bamboohr.py:59`), which disables retry **and**
discards the cause — every failure becomes an early `return`. Ours records the cause per request
(`note_detail_exception`, `wt-main/.../base.py:432`) and surfaces it in the Board's gap line via
`loss_breakdown` — the mechanism `base.py:~25-45` says took five runs to find a UA denylist
without.

### 14. Their `enrichment/derived.py` — **THEIRS-IS-WORSE**, CODE-EVIDENCED

`kalil/.../enrichment/derived.py:41-55` infers `is_remote` from title keywords, asserting only
`True`; `:101-120` `parse_salary_range` is a generic two-number regex with no currency
normalisation, no period handling and no plausibility bound. Ours has `headstart.salary` (Tier-1
per-ATS parsers, period normalisation, plausibility bounds, per-ATS registration — e.g. jazzhr on
`_field_range_currency_interval`, gem on `_field_gem`) and `headstart.experience`, both versioned
through `DERIVATIONS_VERSION` so a fix reaches already-indexed rows. Nothing to take.

---

## Registry note (the brief's premise is stale)

The brief says jazzhr and jobvite are in `registry.DISABLED_ATS`. At `8efb9a85` they are **not**:
`wt-main/src/headstart/scrapers/registry.py:116` is `frozenset({"join"})`. Both were re-enabled
2026-09-16 under ADR-0158 after a full-pool sweep (`registry.py:97-113`).

Nothing in either upstream implementation would change that call. The decision was storage/tech-
yield arithmetic (~13.2-14.2 GB for ~7,868 tech Jobs), and theirs offers no cheaper surface: their
jazzhr fetches the same per-job detail pages, their jobvite fetches the same per-job pages, and
neither has a listing-level description — so the byte cost is identical or worse. Their jobvite is
actively worse on yield, because it **drops** any job whose detail page omitted a description
(`kalil/.../jobvite.py:199-205`).

---

## Where ours is clearly ahead (one line each)

- **bamboohr**: `locationType == "2"` is Hybrid → `None`; theirs calls it remote
  (`kalil/.../bamboohr.py:332`) — 9.3% of 1,508 sampled postings mislabelled wholesale.
- **bamboohr**: no 25,000-char description cap (2.1% of postings exceed it); theirs truncates
  (`kalil/.../bamboohr.py:293`).
- **bamboohr**: reads `minimumExperience`, populated on 97.9% of sampled jobs; theirs never reads it.
- **bamboohr**: dead-vs-live-but-empty keyed on the `BambooHR-ATS-board` wrapper, not on a status
  code that is 200 either way.
- **gem**: 100-op batches, response order verified 7/7, `firstPublishedTsSec` only, tz-aware
  timestamps, and a dedicated Tier-1 `salary._field_gem` (97% of 136 sampled comp strings parse).
- **jazzhr**: `/apply/jobs` embed table + a theme-agnostic div-counting description extractor
  (400/400) vs theirs' JSON-LD-first (69.7%) with a bs4 `job_description` fallback.
- **jazzhr**: the HTML `resumator-job-{employment,type,experience}` attributes give a finer
  employment label than the schema enum, plus an `experience` field theirs has no equivalent for.
- **jazzhr**: departed-tenant guard on the `jobs_table` shell (`jazzhr.py:266`); theirs has none,
  so a parked tenant reads as an emptied Board.
- **jazzhr**: anchoring rows on `<tr>` avoids the mobile-layout double-count (60 elements for 30
  postings on `10pearls`) rather than papering over it with a `seen` set.
- **jobvite**: `/search` rather than `/jobs` (139 of 434 live Boards are short on `/jobs`), id-only
  scanning across five live row templates, and `allow_redirects=False` so a 302'd dead tenant
  cannot read as empty.
- **jobvite**: company name from the board `<title>` (97.7% of 434 Boards) vs theirs' slug.
- **personio**: `/xml` with the per-language description backfill (recovers 187 of 191 empty
  descriptions, fill-only so it can never destroy the 1,159 a blanket `?language=en` would),
  structured `salaryInformation`, `yearsOfExperience` preferred over `seniority`, the
  `additionalOffices` join, and off-host-redirect-means-gone.
- **teamtailor**: the `?page=` walk of `jobs.json`; theirs is a single request and inherits the
  100-item cap that hid 26.4% of one sample's corpus.
- **Everywhere**: ADR-0053 truncation semantics travelling beside the Jobs, per-cause detail-loss
  tallies, ADR-0048/0050 description store and detail skipping, the ADR-0017 tech gate, and stable
  `board_key`/`job_id` composition. Theirs has no notion of an unauthoritative Board at all — a
  short list and a complete one are the same object.
