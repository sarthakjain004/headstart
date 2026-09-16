# BambooHR: widget listing + detail API, measured live

**Measured 2026-09-16.** Every figure below comes from real requests against real tenants, not
from kalil0321/ats-scrapers' own implementation (`raw.githubusercontent.com/kalil0321/
ats-scrapers/main/src/ats_scrapers/scrapers/bamboohr.py`), which was read for structure but
treated as an unverified claim throughout — the same discipline `phenom.py`'s own build applied
to its own upstream. Seed data: `experiment/ats-scraper-candidates/artifacts/parquet/
bamboohr.parquet`, a 19,365-job/2,745-company snapshot from a third-party scrape, used to find
real high-volume tenants and real field values to check against — not trusted as ground truth on
its own (its own scraper has the four defects below).

## Listing surface and pagination

`GET https://{slug}.bamboohr.com/jobs/embed2.php` — static, server-rendered HTML. No pagination
parameter exists in the response or was found by probing (`page`, `offset`, `limit`, etc. — none
tried, because there is no "load more" affordance anywhere in the markup to suggest one).

Live-tested against the highest-volume tenants the parquet sample named (job counts as of
2026-09-16, drift from the sample's date is expected and observed — companies open/close roles):

| tenant | parquet count | live widget count |
|---|---|---|
| theweitzcompany | 160 | 158 |
| armstrongfluidtechnology | 151 | 152 |
| 401auto | 118 | 115 |
| ems | 99 | 99 |
| arcetyp | 95 | 95 |
| gminingventures | 90 | 90 |
| ri | 83 | 85 |
| aceelectric | 79 | 78 |

Every count is a full, single-response read — no truncation marker, no repeated ids, nothing
resembling Zoho's 750-job embed ceiling (`zoho.py`) or Freshteam's hard 1,000-job cap
(`freshteam.py`). That tracks the mechanism: this page has no client-side hydration step to cap —
it's plain HTML BambooHR renders server-side and ships whole. Not proof for a tenant above ~160
(none was found in the sample to test against), but nothing here — from the API's own shape —
suggests one.

## Dead vs. live-but-empty (both 200)

BambooHR's `*.bamboohr.com` wildcard resolves for any subdomain, so DNS/404 never distinguish a
real tenant from a fake one. What does:

| probe | status | body |
|---|---|---|
| 3 confirmed-live, zero-job tenants (`1global`, `1inch`, `22bet`) | 200 | ~1.2 KB `BambooHR-ATS-board` wrapper, `BambooHR-ATS-blankState` "We currently have no open positions" |
| 5 fabricated slugs (`totallyfakeslugabc123`, `anothernonexistentxyz`, `zzzznotarealcompany999`, `doesnotexist12345`, and one more) | 200 | **0 bytes** |

`fetch_raw()` keys on `"BambooHR-ATS-board" in page` for exactly this reason, and
`check_liveness.py`'s `p_bamboohr` uses the identical check rather than the status code (which
is 200 either way).

## Detail API

`GET https://{slug}.bamboohr.com/careers/{id}/detail` — the JSON XHR the page's own SPA hydrates
from. `{"meta": {...}, "result": {"jobOpening": {...}}}`. No auth, no CSRF token, no session
cookie, no Referer — a bare `GET` with just a `User-Agent` succeeded on every one of ~1,500 calls
made during this measurement (0 non-200s).

Fields present on `jobOpening` (from a 200-job sample, 20 tenants):

| field | non-null rate | notes |
|---|---|---|
| `description` | 100% | HTML; max observed 41,214 chars, 2.1% (31/1,508) over 25,000 |
| `employmentStatusLabel` | ~100% | free text: "Full-Time", "Full-Time Permanent", "Contractor", "Temporary staff", "Internship", "Full Time - Hourly Status", "Staff", "Employed" |
| `employmentType` | 0% (0/627 sampled) | a second, enum-shaped field that was always `null` — not used |
| `compensation` | 33.4% (503/1,508) | free-text prose, see below |
| `datePosted` | ~100% | bare `YYYY-MM-DD`, stable across a 4s re-fetch on 5 sampled jobs (not fabricated the way iCIMS's is — see `icims.py`) |
| `minimumExperience` | 97.9% (614/627 sampled) | seniority-tier label, see below |
| `departmentLabel` | matches the widget's own department grouping exactly (spot-checked) |
| `location` (dict: city/state/postalCode/addressCountry) | populated for on-site/hybrid jobs; all-`None` for remote ones |
| `locationType` | `"0"`/`"1"`/`"2"` — see below |

### `locationType`: "2" is Hybrid, not remote

Measured over 1,508 live detail fetches across 20 tenants:

| value | count | % | meaning |
|---|---|---|---|
| `"0"` | 1,256 | 83.3% | onsite |
| `"1"` | 112 | 7.4% | remote |
| `"2"` | 140 | 9.3% | **hybrid** |

kalil0321's upstream treats `'1'`/`'2'`/`'true'` as `is_remote=True`, which would mislabel all
140 of the `"2"` jobs. Cross-checked against the widget's own terse location text, which
independently states "(Hybrid)" on every `"2"` sampled (10/10 spot-checked, e.g. "Toronto, ON
(Hybrid)", "Egham, Surrey (Hybrid)"). `Job.remote` is tri-state in this codebase specifically for
Hybrid — `ashby.py`'s `_remote` and `workday.py`'s `_remote_from` both resolve it to `None` — so
`bamboohr.py`'s own `_remote()` follows that convention rather than guessing.

### `minimumExperience`: a real native field upstream never reads

614 of 627 sampled jobs (97.9%) carry one of: "Entry-level", "Mid-level", "Experienced",
"Manager/Supervisor", "Senior Manager/Supervisor", "Executive", "Senior Executive". This maps
straight onto `Job.experience`'s documented example ("e.g. ... Mid-Senior level"), and
`headstart.experience.extract()`'s seniority tier already knows how to turn most of these words
into a numeric floor downstream. kalil0321's `_apply_opening_to_job` never reads this field at
all — pure upside recovered here, not present in the upstream this was adapted from.

### The 25,000-char description cap is upstream's own, not BambooHR's

31 of 1,508 sampled descriptions (2.1%) exceed 25,000 chars; the longest observed is 41,214.
kalil0321's scraper truncates at 25k "to match the Job schema doc" — a self-imposed limit with
no basis in what BambooHR actually returns. This scraper does not reproduce it.

### `compensation` is prose, not structured

Sample values (503 non-null, 20 tenants): `'£28,090'`, `'$160,000 - $190,000 + based on
experience'`, `'$110,000 - 140,000+ based on experience'`, `'Negotiable'`, `'Day Rate'`,
`'30-35 (DOE)'`, `'80,000 - 110,000 CAD'`, `'19.00 - 21.00'`. Currency symbol usage: 426/503 (USD
sign), 5 GBP (£), 2 EUR (€), 1 INR (₹) by symbol; several state a 3-letter ISO code (CAD) instead
of/alongside a symbol; a meaningful minority (60+) state neither.

`headstart.salary.py`'s `_field_generic` already extracts a range/single figure plus whatever ISO
currency code or period phrase is present, and declines ambiguous shapes ("Negotiable", bare
numbers with no currency or period marker) rather than guess — checked directly:

```
'£28,090'                                     -> min=28090,  max=None,   currency=None
'$160,000 - $190,000 + based on experience'   -> min=160000, max=None,   currency=None  (see note)
'Negotiable'                                  -> None
'80,000 - 110,000 CAD'                        -> min=80000,  max=110000, currency='CAD'
'19.00 - 21.00'                               -> None  (no currency/period marker; correctly declined)
'30-35 (DOE)'                                 -> None  (correctly declined)
```

Note: `_RANGE`'s regex requires the second number to immediately follow the dash — a repeated `$`
on both sides of the range ("$X - $Y") breaks the range match and the value falls back to a
single-value read (the floor only, no ceiling). This is a pre-existing `_field_generic` behaviour
that predates this scraper and is shared by every ATS with no dedicated parser — out of scope
here. No `_field_bamboohr` parser was added: the field is genuinely free text with no consistent
shape across tenants, exactly the case `_field_generic` exists for.

## Rate limit

None found. Four separate sweeps, all clean:

| what | concurrency | n | result |
|---|---|---|---|
| detail fetch | 4 / 8 / 16 / 32 | 80 each | all 200, p50 latency ~0.5s flat across all four |
| detail fetch | 32 / 64 / 128 | 300 each | all 200, up to 122 req/s at conc=128 |
| detail fetch, full sweep | 32 | 1,508 | all 200, ~50 req/s sustained over 30s |

No 429, no Retry-After, no latency blowup at the highest concurrency tried (128). `detail_workers`
is set to 16 in the scraper — comfortably under everything measured clean, not the ceiling.

## Company name

`board_page()` is not implemented. `/careers` was checked on 4 tenants (`theweitzcompany`, `350`,
`4thdimension`, `ems`) and none serves a server-side `<title>` tag — it's a client-rendered SPA
shell, the same exclusion `company_name.py`'s own module docstring already gives for darwinbox
and freshteam. `self.company` stays the slug, the documented fallback.

Noted but out of scope: `theweitzcompany`'s `/careers` shell does embed a company display name in
a JSON blob (`poData.site.logo.alt: "The Weitz Company"`) — but `company_name.py`'s mechanism is
title-tag extraction only (`board_page()` + `from_title()`), and adding a second, JSON-blob-based
extraction path for one ATS is a new abstraction this task didn't ask for and the module wasn't
designed to hold. Left for a future pass if this field turns out to be as reliably present as the
widget's own well-populated fields.

Downstream check: `headstart.experience.extract()`'s seniority tier reads 6 of the 7 values
cleanly ("Entry-level" -> 0, "Mid-level" -> 3, "Experienced"/"Executive"/"Senior Executive"/
"Senior Manager/Supervisor" -> 5) and misses one ("Manager/Supervisor" -> `None`). Noted, not
fixed here — `experience.py`'s pattern set is its own measured pass (CLAUDE.md), not something
this scraper's own field mapping should reach into.

## Discovery and liveness

- Seed: `https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/
  bamboohr.csv` — 5,632 `name,slug,url` rows.
- Wayback CDX (`http://web.archive.org/cdx/search/cdx?url=bamboohr.com&matchType=domain`),
  filtered to `/careers` paths: 307 distinct `*.bamboohr.com` hosts in a 3,000-row sample (the
  cap on that probe query, not the true total) — confirms the single-host, no-regional-pod shape
  already assumed by the seed CSV and used to wire `wayback_feeder.py`'s `ATS_HOSTS["bamboohr"]`
  as `("sub", "bamboohr.com")`.
- `cc_miner.py` (Common Crawl `CC-MAIN-2026-34`, `bamboohr.com` subdomain pattern): 1,317
  tenants found, +992 net new to the seed pool.
- `wayback_pages.py bamboohr` (full 299-page CDX sweep, run to completion): 23,132 unique
  slugs, +19,216 net new to the pool after re-tagging 3,916 already-known rows.
- Merged pool (`data/ats-tenants-merged/bamboohr.csv`, seed ∪ Common Crawl ∪ Wayback):
  **25,840 tenants** — Wayback is the dominant source by a wide margin once run in full, not the
  seed CSV or Common Crawl.
- `check_liveness.py bamboohr`, run to completion (four passes at `workers=432`, then a
  low-concurrency cleanup pass at `workers=48` for the residual `unknown` set — see below):
  **16,255 live, 9,574 dead, 11 unknown**; **10,425 Hiring Boards** (`jobs>=1`); **68,819 jobs
  reachable**. Ledger: `data/validate/liveness/bamboohr.csv` (git-tracked, ADR-0012).

**A concurrency note, not a BambooHR one.** At `workers=432` (this repo's liveness-prober
default), a large majority of failed probes came back `connect-refused` rather than a timeout or
an HTTP error — e.g. 4,446 of 4,524 failures in one pass, scaling in lockstep with the worker
count across every pass. That is the shape of local outbound-connection exhaustion, not a
BambooHR-side rate limit (BambooHR itself never returned 429 or Retry-After anywhere in this
measurement, matching the "no rate limit found" conclusion above). A second pass over just the
leftover `unknown` rows at `workers=48` (`LIVENESS_WORKERS=48 check_liveness.py bamboohr
--unknown-ttl 0`) cleared 25,829 of 25,840 boards to a settled live/dead verdict in one pass; the
last 11 answered `http-403`/`http-401`/`http-204`/timeout consistently across retries and are
left `unknown` for the ledger's normal TTL-based re-probe rather than chased further here.
