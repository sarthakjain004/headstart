# Breezy HR JSON API — measurement log

Live measurement of `{slug}.breezy.hr` before building `headstart.scrapers.breezy`. The write-up
is `docs/breezy/2026-09-23_json-api-measurement.md`; the decisions are ADR-0181. This log is the
run record; raw captures are in `artifacts/`, probe scripts beside it.

## Hypotheses (step 1, 2026-09-23) — each confirmed or killed below

Sources: `kalil0321/ats-scrapers` `src/ats_scrapers/scrapers/breezy.py` (314 lines, MIT) and its
seed list `ats-companies/breezy.csv` (1,384 rows); `experiment/ats-scraper-candidates/LOG.md`
(third-party snapshot: 29,294 jobs, 7.0% tech, 888 companies); the existing pool
`data/ats-tenants-merged/breezy.csv` (4,794 rows, source `harvest`); orchestrator recon (one
request to `fathom.breezy.hr/json`). The repo already keys the provider as `breezy`
(`consolidate_harvested_lists.py`, `fingerprint_careers.py`).

- H1. Slug = the tenant subdomain label of `{slug}.breezy.hr`; one host, no regional pod.
- H2. `GET https://{slug}.breezy.hr/json` returns the whole Board in one response, no pagination.
- H3. The listing carries no description; the body is only on the detail page
  `/p/{friendly_id}` in `<div class="description">` (upstream fetches every detail page).
- H4. A dead tenant answers `/json` with a 302 to `https://breezy.hr/` (the marketing site).
- H5. A live Board with zero openings answers 200 `[]`.
- H6. A shared CF/Akamai-style edge 403s bursty traffic at ~14 req/s across tenants
  (upstream: 685-tenant pass, cross-tenant conc 8 × per-tenant 6); upstream detail
  concurrency is 4 for that reason, and it retries the listing's 403 with backoff.
- H7. Listing fields: `id`, `friendly_id`, `name`, `url`, `published_date`, `type{id,name}`,
  `location{city,state,country,is_remote,name}`, `locations[]`, `department`, `salary`
  (free text), `company{name}`. Upstream also reads `category`, `experience`, `education`,
  `tags` "if present".
- H8. `type.id` values: `fullTime`, `partTime`, `contract`, `intern`, `internship`, `temporary`
  (upstream `_TYPE_MAP`).
- H9. `location.is_remote` is the remote flag (upstream reads it as a bool, primary location only).
- H10. `company.name` names the employer (upstream uses it as the company name).
- H11. Upstream's 25,000-char description cap is upstream's own choice (bamboohr precedent), not
  Breezy's.
- H12. Upstream sends `User-Agent: Mozilla/5.0`; unknown whether the shared UA matters.
- H13. `robots.txt` / `sitemap.xml` exist per tenant (to check what surfaces they name).

## 2026-09-23 — first probes

- `fathom.breezy.hr/robots.txt`: disallows only asset paths (and AhrefsBot); names no feed.
- `fathom.breezy.hr/sitemap.xml`: 200, a urlset of `/` plus one `/p/{friendly_id}` per posting
  with `<lastmod>` at day precision — a listing surface, but it states nothing the JSON does not.
- Detail page `/p/{friendly_id}`: 18.9 KB server-rendered HTML carrying a `JobPosting` JSON-LD
  (description, `employmentType`, `datePosted`, `baseSalary{currency, unitText, min, max}`,
  `jobLocationType: TELECOMMUTE`, `hiringOrganization.name`) and `<div class="description">`.
- **`/json?verbose=true` carries the description** (kills H3 as the only route): same rows as
  `/json`, plus a `description` HTML key. Found by trying query flags; the portal's own
  `index.js` (8.8 KB) never calls `/json` at all. Other guesses: `?description=true` and
  `?format=rss` are ignored (plain listing); `/p/{id}/json`, `/p/{id}.json`, `/json/{id}`,
  `/api/positions`, `/rss` all 302 to the tenant root; `/feed` 200 with a 25-byte body.
- **Dead is a 404, not a 302** (kills H4 on this sample): 100 random pool tenants at conc 2
  (a 2.3 MB capture kept in the session scratchpad, superseded by the census below) → 45 200 non-empty, 32 200 `[]`, 23 404
  `text/html` "Career portal not found | Breezy HR" (3,265 bytes). An invented slug
  (`zzqq-nonexistent-xyz123`) gives the byte-identical 404 on `/json`, `/json?verbose=true`
  and `/`. No 302 seen. Host case is irrelevant (`FATHOM.breezy.hr/json` == `fathom`).

## 2026-09-23 — full-pool census and field measurement

- **Census** (`ramp.py`, pool shuffled with seed 7): every one of the 4,794 pool tenants fetched
  once at `/json?verbose=true`, redirects not followed. **3,877 200 (2,174 hiring, 1,703 `[]`),
  917 404, zero of anything else** — no 3xx, no 403, no 5xx, no timeout. Every 200 was
  `application/json` and a list; every 404 the same 3,265-byte "Career portal not found" page;
  every empty Board exactly 2 bytes (`[]`). 38,314 postings, mean 17.6 / p50 4 / p90 29 per
  hiring Board. The 227 MB raw capture is kept out of git (the probe scripts read it from
  `$BREEZY_CENSUS`, default `artifacts/2026-09-23_pool_census_ramp.jsonl`); the per-tenant table is
  `artifacts/2026-09-23_pool_census.csv`.
- **The census doubled as the cross-tenant ramp** (`artifacts/2026-09-23_pool_census_ramp.log`):
  250 tenants each at conc 4 / 8 / 16 / 32 / 64 → 9.8 / 19.1 / 35.7 / 66.0 / 93.9 req/s, p50
  0.35–0.53 s, zero non-200/404; the remaining 3,544 at conc 16, 35.0 req/s, zero. Upstream's
  "403 at ~14 req/s across tenants" (H6) is not reproduced — killed for this surface.
- **Single-tenant ramp** (`single_tenant_ramp.py salmonjobs`, 185 postings, 600 KB listing):
  listing conc 1→128 up to 113 req/s, detail pages conc 1→128 up to 109 req/s; 832 requests,
  every one 200. No knee found; latency flat to conc 64.
- **User-Agent**: `headstart/0.1`, none (curl default), `python-requests/2.32`, `Mozilla/5.0` all
  200 with identical bytes.
- **Listing fields** (38,314 rows): every row carries all 12 keys (`id`, `friendly_id`, `name`,
  `url`, `published_date`, `type`, `location`, `locations`, `department`, `salary`, `company`,
  `description`). Non-null: department 20,407 (53.3%), salary 19,167 (50.0%), description 38,314
  (114 of them image- or empty-tag-only, no text). Upstream's `category`/`experience`/`education`/
  `tags` (H7) appear on 0 rows. `id` is 12 or 14 hex chars, never contains `:`; no duplicate id
  within a Board; `url` == `https://{slug}.breezy.hr/p/{friendly_id}` on 38,314 / 38,314.
- **Description = the page's** (`description_listing_vs_detail.py`/`description_lines_on_page.py`, 204 postings over 120 random
  hiring Boards): `html_to_text` of the listing's `description` equals `html_to_text` of the
  detail page's JSON-LD `description` on 180 / 180 pages that carry JSON-LD; the other 24 pages
  have no JSON-LD (pool/"general interest" postings) and every line of the listing text is on
  the page, 24 / 24. Raw HTML differs by a few attributes (listing ≥ detail on all 133 raw
  diffs), never in text. H3 killed: no detail pass is needed.
- **No hidden or missing rows** (`json_vs_sitemap_vs_root.py`, the 10 largest + 60 random hiring + 20 random
  empty Boards): the page root and the sitemap never list an id the JSON lacks (0 on 90 Boards).
  The JSON lists ids the sitemap omits on 28 Boards (the no-JSON-LD postings) and the root omits
  on 2 — those pages all answer 200 with the posting. Largest Board
  `american-logistics-authority`: JSON 2,760 = root 2,760, one response. No pagination (H2 holds).
- **Remote** (`remote_flag_vs_jsonld.py`, 12 per class against the page's JSON-LD `jobLocationType`):
  `location.is_remote` is what the page publishes — True → TELECOMMUTE 46/46 with JSON-LD, False or
  absent → none 57/57, including `remote_details` stale at "remote"/"remote-location"/"hybrid" on
  307 rows whose `is_remote` is False. `remote_details.value == "hybrid"` with `is_remote` True on
  995 rows. `is_remote` absent on 2,194 rows. Per-location `is_remote` never disagrees within a
  posting's `locations` (0 rows).
- **Locations**: `locations` has 0 / 1 / 2 / 3 / 4 / 5 entries on 2,970 / 33,327 / 882 / 356 / 220 /
  559 rows; `location` (primary) is null on 1 row and has a `name` on 38,312.
- **Company**: `company.name` is one value per Board on 2,174 / 2,174 hiring Boards, and
  `company.friendly_id` == the slug on 2,174 / 2,174. H10 holds.
- **Type**: `type.id` fullTime 25,626 / contract 5,501 / partTime 5,026 / other 1,658 / temporary
  503; `type.name` is localised (Повна зайнятість, Vollzeit, A tiempo completo …). No intern id
  observed (H8's intern/internship: 0 rows).
- **Dates**: `published_date` stable across two fetches ~40 min apart, 293 / 293 (40 Boards);
  only 34 / 38,314 end `.000Z`. JSON-LD `datePosted` is ≤ `published_date[:10]` on 180 / 180
  (equal 68, earlier 112) — the listing date is the latest publish, not the first.
- **Salary**: 19,167 strings, 87 templated shapes, all `{sym}{n}[ – {sym}{n}| +][ / period]` or
  `Up to {sym}{n} / period`; periods hour / year / week / month / biweekly / day. Currency by
  symbol vs the detail JSON-LD `baseSalary.currency` (`salary_symbol_vs_jsonld.py`, 499 postings): bare `$`
  is USD on 141 / 141 US postings, CAD on 179 / 188 Canadian (USD 9), USD on 41 / 47 elsewhere
  (MXN 2, SGD 1, COP 1, none 2). Every other symbol names one code except `kr` (SEK 3, DKK 1,
  by country).
- **Tech share**: `tech_filter.is_tech(name, department)` keeps 3,502 / 38,314 (9.1%) on 713
  Boards; country US 1,677, CA 193, UA 186, IN 145, GB 108.
- **Language** (`langdetect` over title + text, 2,000 random rows): 94.5% en (uk 31, es 22, de 14,
  fr 11); 600 random tech rows 90.2% en (uk 27).
- **Response size**: hiring Board listing p50 22 KB, p95 336 KB, max 23.6 MB (`everstar`, 372
  postings with inline base64 images; 2.9 s). All hiring Boards: 216.9 MB for 38,314 postings,
  5,660 B per posting (without descriptions ~980 B).
- **Vendor roster**: `breezy.hr/sitemap.xml` (2,640 URLs) lists only marketing pages — no tenant
  list. The upstream seed list (1,384) is a subset of the pool (0 new).
- **Salary forms**: range 16,079, floor (`N+`) 1,732, ceiling-only (`Up to`) 37; period `/ hour`
  8,205, `/ year` 6,682, `/ week` 2,824, `/ month` 1,108, `/ biweekly` 161, `/ day` 97, none 90.
- **Discovery started**: `wayback_feeder.ATS_HOSTS["breezy"]` (`sub`, `breezy.hr`) and
  `cc_miner.ATS_PATTERNS["breezy"]` (`label`) added; `wayback_pages.py breezy` queued behind the
  shared discovery lock.

## 2026-09-23 — scraper, liveness pass, ledger

- **Scraper over the census** (every captured row through `BreezyScraper.parse`): 38,314 Jobs;
  location 38,312, remote stated 37,317 (True 5,258 / False 32,059 / hybrid None 997), department
  20,407, description text 38,200, salary emitted 18,969 of 19,167 stated. Of those,
  `salary.extract(field, None, ats="breezy")` reads 18,483 (96.4% of stated) against 6,484
  (33.8%) the generic parser read off the raw strings. The 40 rows the generic parser read and
  this does not are all its misreads (a PKR/PHP monthly figure read as an annual floor, a
  tenant's "$80,000 – $100,000 / hour" read as annual); the 352 yearly strings that do not parse
  are tenant labelling errors ("$20 – $23 / year") the plausibility floor rejects.
- **`verify_scraper.py breezy 20`**: 20 pool tenants, 17 reachable, 7 hiring, 94 jobs, 94 with a
  description, 3 dead (404).
- **Largest Board through `fetch()`** (`american-logistics-authority`): 2,760 Jobs, every field
  but department/experience populated, salary on 2,543 and parsed on 2,521.
- **First liveness pass wrote 41 live Boards as dead.** `check_liveness.py` runs 432 workers,
  every Board is its own hostname, and the local resolver fails under that: the pass logged
  `connect-refused` and timeouts (github.com refused connections from this machine at the same
  moment), and `p_breezy` read curl's code 6 as NXDOMAIN → DEAD. The 41 were alphabetical
  clusters (`kimmel-associates` 445 postings, `nexo` 24, …) and all answered 200 on a re-fetch.
  Replay (`burst_404.py`, 1,500 live Boards at 432-wide,
  `artifacts/2026-09-23_burst_432_live_boards.jsonl`): 1,379 200, **100 DNSError**, 21 timeouts,
  zero 404. `*.breezy.hr` is a wildcard record (an invented label resolves to the same
  CloudFront addresses), so no tenant is ever NXDOMAIN — `p_breezy` now reads a DNS failure as
  UNKNOWN. The forced re-probe: 4,794 settled, 0 unknown after four passes.
- **Ledger** `data/validate/liveness/breezy.csv`: 4,794 rows, 3,876 live, 918 dead, 2,173
  hiring, 38,266 postings. Against the census an hour earlier the only disagreement is
  `sandbox` (a real Virginia org, 3 postings), killed before any probe by ADR-0034's nonprod
  name rule — left as is.
- **Duplicate checks**: 4,794 rows = 4,794 unique `board_key`s; no redirects (0 3xx in the
  census), no casing variants. One Breezy company can run several portals under different
  labels, and the same posting id is then served on each: 190 ids on 26 Boards (e.g.
  `lumio-dental` / `lumio-dental-practice-locations`, 62), 191 extra rows of 38,314 (0.5%), 7 of
  them tech. Not collapsed: each is a separate Board with its own URLs, and `evict_duplicate`
  groups within a Board.
- **Spot check** (random, seed 8): live `ct-united-fc` 12, `grill-hero` 0,
  `gustav-technologies-inc` 0, `bond-pro-inc` 2, `coeur-d-alene-resort` 63 — each equal to a
  fresh fetch; dead `seabound`, `amitruck`, `beek`, `casa`, `dozens` — each 404.

## 2026-09-23 — discovery

- **Common Crawl: not measured — index unreachable.** `index.commoncrawl.org` answered every
  request with an empty reply (curl 52) on 2026-09-23, from this machine and the orchestrator's,
  so a `cc_miner.py` sweep would return zero while looking like "nothing found". It was not run;
  the `ATS_PATTERNS["breezy"]` entry is wired for a later sweep. This is not a count of zero.
- **Wayback sweep** (`wayback_pages.py breezy`, run under the shared discovery lock): 194 CDX
  pages, 9,828 labels (16 `%2F`-glued rows pruned by the feeder). `merge_wayback_into_tenants.py`:
  +5,413 new to the pool, 4,415 re-tagged → pool 10,207 (all `[a-z0-9-]` labels).
- **Liveness over the grown pool** (TTL skips the 4,794 already probed): 10,207 rows → 5,156 live,
  5,038 dead, 13 unknown; 2,907 hiring, 47,193 postings. Wayback-only: 5,413 rows, 1,280 live, 734
  hiring, 8,927 postings. The 13 unknown are vendor infrastructure hosts (`assets-cdn`,
  `avatar-cdn`, `gallery-cdn`, `static-cdn` → 200 488-byte HTML; `hirelearning` → 301
  `breezy.hr/blog`; `resources` → 301 `help.breezy.hr`; `test-app`, `test-sys` → 302 `/signin`;
  `test-onboarding-app` → 200 HTML; `job-queue-console`, `test-onboarding`, `test-onboarding-cmp`,
  `test-status` → no answer) — left UNKNOWN, since no real tenant gave any of those.
- **Checks**: 10,207 rows = 10,207 unique `board_key`s, 0 mixed-case; spot check (seed 12) live
  `sacramento-business-brokers` 1, `ing-creatives-marketing-department` 1, `synergy` 0,
  `mobo-shop` 0, `darwins` 2 — each equal to a fresh fetch; dead `sana-benefits`, `acretrader`,
  `roche-pharmaceutical-company`, `wave-3-consultants`, `maropost` — each 404.
- **Remote check re-run for its capture** (`remote_flag_vs_jsonld.py`, same seed, output now in
  `artifacts/2026-09-23_remote_flag_vs_jsonld.log`): identical to the first run — flag true →
  TELECOMMUTE 46/46 with JSON-LD, flag false or absent → none 57/57 (12/12 of the flagless class).
