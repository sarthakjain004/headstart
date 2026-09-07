# iCIMS: how much India tech volume is actually readable?

**Measured 2026-09-07.** All figures below come from live requests made that day, not from docs
or inference. Raw captures: `docs/icims/artifacts/`.

## Verdict: do not build an iCIMS scraper

**591 unique India-located openings exist across the whole measured iCIMS surface. ~253 pass the
repo's tech gate, and only ~141 are genuinely software/data roles** — the rest are mechanical,
process, instrumentation and semiconductor engineering that `tech_filter`'s recall-biased
`\b(engineer)\b` rule lets through.

For scale: the Zwayam scraper (#320) landed **224 hiring boards**. iCIMS offers **41 India boards
and ~141 real software jobs** for a comparable or larger build, spread across 41 tenants that must
each be discovered by name because slugs are not derivable. That is roughly one-tenth the yield.

The CLAUDE.md line is **half right, and its stated reason is wrong**:

> *"iCIMS = opportunistic-only (alive but HTML/JSP-only, non-enumerable, India tenants are GCC
> boards not IT majors)"*

- **"non-enumerable" is false.** Wayback CDX enumerated **6,430 distinct tenant hosts** in about
  20 minutes (§1). Tenants are discoverable; they are just not *derivable* from a company name.
- **"HTML/JSP-only" is true but harmless.** It is clean, paginated, no-JS HTML with a stable
  structure and no bot protection (§2, §4). Parsing it is easy.
- **"India tenants are GCC boards not IT majors" is exactly right, and it is the whole story.**
  Every India board found belongs to a foreign multinational's India office. Zero
  Indian-headquartered employers (§5).

So the dead-end verdict **stands**, but should be re-filed under *volume*, not *reachability*.
The blocker is that there is almost nothing there, not that we cannot read it.

---

## 1. Tenant enumeration

### crt.sh (the supplied dump) is useless for this — it sees no tenants

`artifacts/2026-09-07_crt-sh-hosts_icims.txt`: the 1.27 MB / 4,138-record dump collapses to
**132 distinct hostnames, of which exactly one is a customer tenant**
(`www.careers-blarneycastleoil.icims.com`). Everything else is iCIMS corporate infrastructure
(`api-us-east-1`, `login`, `social-prod`, …).

The reason is structural, not a gap in the dump: iCIMS serves tenant boards under a **wildcard
certificate**. `*.icims.com`, `*.i.icims.com`, `*.dev.icims.com` and 11 more wildcards are all
present in the dump. **Certificate Transparency can never enumerate iCIMS tenants**, because no
per-tenant certificate is ever issued. This is a property of iCIMS, so it will not improve with a
larger CT pull.

### Wayback CDX does work — 6,430 tenants

Source: `web.archive.org/cdx/search/cdx?url=icims.com&matchType=domain`, filtered to
`original:.*icims\.com/jobs/.*`, paginated (`pageSize=5`). Common Crawl was **not** queried, per
the constraint.

- **6,430 distinct `{tenant}.icims.com` hosts** — `artifacts/2026-09-07_icims-tenants-wayback-cdx.txt`.
- This is a **floor, not a census**: I fetched roughly a third of the 320 CDX pages (all of the
  `a`–`c` range plus a stride across `c`–`w`). The full walk would yield more.
- Caveat: CDX only sees hosts the Wayback Machine has archived, so tenants that were never
  crawled are invisible. It is a far better source than CT, not a complete one.

**2,814 tenants were probed live.** Status of those probes
(`artifacts/2026-09-07_board-probe_slice-*.jsonl`):

| status | count | meaning |
|---|---|---|
| `HTTP_404` | 1,058 | tenant retired — a clean, unambiguous 404 |
| `OK` | 1,055 | live board with at least one posting |
| `NOT_BOARD` | 356 | 200, but a landing/redirect page, no job table |
| `EMPTY` | 309 | live board, explicit "no jobs were found" |
| `HTTP_403` | 20 | — |
| `HTTP_503` / `530` | 11 | — |
| `ERR` | 5 | connection failure after 4 retries |

Only **1,055 of 2,814 probed tenants (37%) are live boards with jobs**. The 404 rate is high
because Wayback surfaces historical tenants that no longer exist.

---

## 2. The board surface (verified, not assumed)

**Listing URL:** `https://{tenant}.icims.com/jobs/search?ss=1&in_iframe=1`

The `in_iframe=1` parameter is the important part. `?ss=1` alone returns the customer's *branded
wrapper* page with **zero job links** — for `career-celanese.icims.com` that is 80,633 bytes of
chrome. The wrapper embeds the real board in an iframe and, helpfully, declares it in a
`<noscript>` fallback:

```html
<iframe src="https://career-celanese.icims.com/jobs/search?ss=1&in_iframe=1"
        id="noscript_icims_content_iframe" ...>
```

Fetching that inner URL directly returns 62,666 bytes of plain, parseable HTML containing the job
table (`artifacts/2026-09-07_board-listing-inner-iframe_celanese.html`).

**There is no JSON endpoint.** The listing is server-rendered ASP.NET HTML (`WebResource.axd`,
`ScriptResource.axd`). It is, however, well structured:

```html
<ul class="container-fluid iCIMS_JobsTable">
<li class="iCIMS_JobCardItem">
  <a href="https://career-celanese.icims.com/jobs/23719/senior-director%2c-sales.../job?in_iframe=1">
    <h3>Senior Director, Sales - Auto Segment Leader</h3></a>
  <div class="col-xs-12 description">The Regional Auto Segment Leader is responsible for...</div>
  <dt class="iCIMS_JobHeaderField">Job ID</dt><dd ...>2026-23719</dd>
  <dt ...><span class="sr-only field-label">City</span></dt><dd ...>Auburn Hills</dd>
```

Each card carries title, a description snippet, Job ID and location — enough for a listing-only
scrape, with `/jobs/{id}/{slug}/job` for the detail page.

**Pagination:** `&pr=N`, zero-indexed, with `Page 1 of 5` in the body. **No cap.** Measured on
`ascensionjobs1-ascension.icims.com` (63 pages): `pr=0/10/40` return 50 jobs each, `pr=62` returns
the tail of 8, and `pr=63`, `pr=80`, `pr=200` all return HTTP 200 with 0 jobs and a stable 63 KB
body. Page size is per-tenant config (20 or 50).

### Two traps that will silently corrupt any measurement here

**Trap 1 — `searchLocation` is silently ignored when it does not resolve.** It is not a free-text
filter. On `career-celanese`, `searchLocation=India` correctly filtered 5 pages down to
"Sorry, no jobs were found", but `searchLocation=Texas`, `Auburn Hills` and `ZZZNONSENSE` all
returned the **unfiltered** 5 pages. Running `searchLocation=India` blindly across the corpus
produced **1,601 "India jobs" on 74 boards** — including US HVAC firms like
`careers-blazeair.icims.com`, whose first "India" result is located `US-NC-Wilmington`. The
parameter only filters when the value appears in *that board's own* location facet.

**Trap 2 — the location facet and the card location are each missing most of the time.**
- Only **426 of 837** live A–C boards render a location `<select>` at all.
- **405 of 561** boards with jobs render no location on their cards — 65.8% of jobs.
- Neither signal exists on 88 boards (21.6% of A–C jobs). Resolved by sampling **250 job detail
  pages** across those boards: every parsed location was US/CA/UK/AU/PH/JP/IE, **0 India**.

So India detection needs **facet ∪ card-location**, and the two disagree often. Using the facet
alone missed `indiacareers-docusign` (34 India jobs, 30 tech). Using card locations alone missed
`careers-consilio` (20 India jobs). Geo ids are also **not stable across tenants** — India is
`13228` on most boards but `14293` on `careers-centricbrands` — so label matching (`IN-…`,
`…IN`, `India-…`) is required alongside the id.

---

## 3. India volume

Method: detect India boards by facet ∪ card location, then enumerate each board's India postings
through the board's **own** facet values (verified applied, not ignored, by comparing against the
unfiltered total on every board). India matching reuses this repo's vetted gazetteer —
`headstart.geo.CITIES`, `STATES`, `INDIA_EXCLUDE` — plus the `IN-XX-` location code. Tech
classification is `headstart.tech_filter.classify()`, the authoritative gate.

| | measured |
|---|---|
| tenants enumerated (Wayback CDX) | 6,430 |
| tenants probed live | 2,814 |
| live boards with jobs | 1,055 |
| jobs read (A–C slice, fully crawled) | 41,471 |
| jobs on the spread slice (est. pages x page size) | ~58,000 |
| **India boards** | **41** (3.9% of live boards) |
| **India postings, raw** | **644** |
| cross-tenant duplicate copies | 53 |
| **India postings, unique** | **591** |
| **passing `tech_filter`** | **253** |
| **genuinely software/data** | **~141** |

India is **~0.6% of the iCIMS corpus**.

**Why the tech number needs discounting.** `tech_filter` is deliberately recall-biased, and iCIMS'
India tenant mix is heavy on engineering firms (SSOE, Walter P Moore, Nikkiso) and a semiconductor
company (MaxLinear). Of 278 raw tech positives: 141 clearly software/data, 15 hardware/semis
(`Principal ASIC Design Engineer`, `Senior IC Substrate Package Layout Designer`), and 122 generic
`…Engineer` titles that are mostly not software (`Process Engineer`, `Commissioning Engineer II`,
`Instrument & Control Design Engineer`, `Proposal Engineer`).

**Duplicate boards are real here**, exactly as CLAUDE.md warns: `allcareers-nikkiso` and
`incareers-nikkiso` serve the same 22 postings; likewise
`careers-collaborationbetterstheworldvi` / `jobs-collaborationbetterstheworld` (10),
`careers-appliedsystems` / `india-appliedsystems` (3), `careers-consilio` / `careers-consilio-skye`
(2), `careers-centricbrands` / `careers-centricbrandsasia` (1).

---

## 4. Difficulty: easy to read, hard to discover

**Reading — easy.** Plain HTTP, no JavaScript, no auth, no cookies required. Roughly **6,000
requests** were made across this measurement at concurrency 4–6 with `headstart/0.1` and there was
**no bot protection, no CAPTCHA, no challenge page and no rate-limit response** — 20 `403`s and 11
`5xx` out of 2,814 board probes, scattered rather than clustered, i.e. per-tenant configuration
rather than throttling. Pagination is uncapped.

One caution for anyone re-running this: an early pass at concurrency 6 with no retries produced
**827 `ConnectionError`s (53%)**. DNS resolved fine for all of them and a plain retry returned a
clean 404, so those were client-side connection churn, **not** blocks and **not** dead tenants.
Counting them as "no jobs" would have thrown the census out completely. Four retries with backoff
reduced them to 1.

**Discovery — the actual cost.** Slugs are not derivable: `abudhabi-nyu`, `acip-generalrv`,
`careershub-medpace`, `clarkcareers-ventrahealth`, `inemployees-acentra`. A scraper needs a
maintained tenant roster mined from Wayback CDX and re-validated, because **37.6% of enumerated
tenants are already 404** — and for the India-named tenants specifically the decay is worse:
**38 of 83 are dead (45.8%)**, including `in-amazon`, `india-aon`, `incareers-zs`,
`indiacareers-cvent`. The India segment of iCIMS is visibly shrinking.

The one useful discovery shortcut: India boards cluster under predictable *prefixes* —
`india-*`, `indiacareers-*`, `incareers-*`, `in-careers-*`, `indcareers-*`, `inemployees-*`,
`apac-*`, `asiacareers-*`, `careersintl-*`. 83 such tenants exist in the enumerated set, and all
were probed here.

---

## 5. Named Indian employers — all GCCs, no Indian companies

Top India boards by tech volume (`artifacts/2026-09-07_india-jobs-by-board.json`):

| board | employer | India | tech | cities |
|---|---|---|---|---|
| `indiacareers-symplr` | symplr | 47 | 37 | Bangalore |
| `indiacareers-docusign` | DocuSign | 34 | 30 | Bengaluru |
| `internalcareers-waters` | Waters Corp | 52 | 28 | Bangalore, Hyderabad, Chennai, Ahmedabad, Indore, New Delhi |
| `careershub-medpace` | Medpace | 57 | 26 | Hyderabad, Navi Mumbai |
| `careersintl-maxlinear` | MaxLinear | 26 | 25 | Bangalore (semiconductor) |
| `india-appliedsystems` | Applied Systems | 18 | 13 | Bengaluru |
| `in-careers-seismic` | Seismic | 14 | 12 | Hyderabad |
| `allcareers`/`incareers-nikkiso` | Nikkiso | 22 | 10 | Vadodara, Pune |
| `international-riverbed` | Riverbed | 12 | 10 | Bangalore, Gujarat |
| `apac-blackhawknetwork` | Blackhawk Network | 8 | 8 | Bengaluru, Kozhikode |
| `india-external-dish` | DISH | 9 | 8 | Hyderabad, Bengaluru |
| `incareers-jaggaer` | JAGGAER | 10 | 7 | Hyderabad |
| `indiacareers-paychex` | Paychex | 9 | 7 | Bengaluru |
| `careershub-shure` | Shure | 6 | 5 | Bangalore, Hyderabad |
| `careers-consilio` | Consilio | 17 | 4 | Bangalore, Hyderabad, Gurgaon |
| `inemployees-acentra` | Acentra Health | 9 | 4 | Bengaluru, Chennai |
| `clarkcareers-ventrahealth` | Ventra Health | 84 | 3 | Chennai, Coimbatore (medical billing) |
| `indcareers-ssoe` | SSOE Group | 45 | 2 | Mumbai (architecture/engineering) |
| `indiacareers-lennox` | Lennox | 22 | 2 | Chennai |
| `careersintl-hireright1` | HireRight | 19 | 1 | Bengaluru, Gurgaon, Mumbai, Nagpur |
| `indiacareers-hrblock` | H&R Block | 3 | 3 | Thiruvananthapuram |

Sample verified titles: `Senior Software Engineer` / `Staff Software Engineer` (symplr, Bangalore),
`GenAI Engineer` / `Lead Machine Learning Engineer` / `Senior Software Engineer Frontend`
(DocuSign, Bengaluru), `Lead Engineer – AI Platform` (Waters, Bangalore), `Lead Engineer - AWS
Cloud` (DISH, Hyderabad), `Senior Software Engineer II (Ruby on Rails)` (Seismic, Hyderabad).

**Every one is a foreign multinational's India office.** Not a single Indian-headquartered
employer appeared in 2,814 probed tenants — no Indian IT services firm, no Indian startup, no
Indian product company. This confirms the "GCC boards not IT majors" half of the CLAUDE.md line
precisely.

---

## 6. Recommendation

**Keep iCIMS on the do-not-build list, with the reasoning corrected.** ~141 real software openings
on 41 non-derivable, actively-decaying tenants does not justify a scraper plus a discovery
pipeline, when the same effort against PyjamaHR, Eightfold or TurboHire (per `experiment/
ats-provider-expansion/PLAN.md`) reaches far more.

If iCIMS is ever revisited, the cheap version is **not** a general scraper: take the ~20 named
boards in §5, pin them as manual slugs, and read them listing-only over plain HTTP. That captures
the great majority of the tech volume for a fraction of the work — the same "single-company
unlock" pattern CLAUDE.md already uses for Trakstar Hire and Skillate.

Two things worth recording regardless of the decision:

- **CT is structurally blind to iCIMS tenants** (wildcard cert). Don't spend another session on a
  crt.sh pull for this provider.
- **`searchLocation` is a trap.** Any future iCIMS work must validate that the filter was applied
  by comparing against the unfiltered total, or it will report confident, badly wrong numbers.

## Reproducing

Artifacts in `docs/icims/artifacts/`:

| file | contents |
|---|---|
| `2026-09-07_icims-tenants-wayback-cdx.txt` | 6,430 enumerated tenant hosts |
| `2026-09-07_crt-sh-hosts_icims.txt` | the 132 CT hostnames, showing the wildcard problem |
| `2026-09-07_board-probe_slice-a-c.jsonl` | per-tenant probe, A–C (837 live candidates) |
| `2026-09-07_board-probe_slice-spread.jsonl` | per-tenant probe, alphabet-spanning (1,977) |
| `2026-09-07_full-listing-crawl_slice-a-c.jsonl` | full listing crawl, 41,471 jobs |
| `2026-09-07_page1-location-check_slice-spread.jsonl` | page-1 card-location India check |
| `2026-09-07_india-jobs-by-board.json` | the 41 India boards with every posting + tech verdict |
| `2026-09-07_board-listing-inner-iframe_celanese.html` | a real inner-iframe listing page |
