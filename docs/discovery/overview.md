# Finding companies (ATS slug discovery)

How to discover the `(ats, slug)` pairs that go into `config/companies.toml`. The curated
list in that file *is* HeadStart's coverage, so growing coverage means finding more valid
slugs. This doc records what works, what doesn't, and why.

_Last updated: 2026-06-18._

## Scope: companies worldwide, engineering roles

HeadStart targets companies **globally**, not one country, and within them
**software-engineering / tech roles**. The geographic long tail (India among others) is just
where the LinkedIn gap is widest, so it gets specific attention below — it is not the whole
target.

The aim is **wider coverage of engineering roles than LinkedIn** — catching the boards
LinkedIn never syndicates or lists late. So the tech focus narrows the *dimension* (eng roles
only), but within that dimension breadth is the whole point: the filter must cut *work*, never
reach.

The tech focus has to *reduce* discovery and scraping work, not merely trim the output. A
filter that runs *after* every job is already scraped saves nothing, so apply it at the two
upstream layers instead:
- **Company selection** — don't seed companies that don't hire engineers. A design studio or a
  retail chain with no engineering org is dropped before it ever costs a fetch.
- **Source query** — where the ATS feed supports a server-side facet, ask only for engineering
  roles: Lever `?department=`, Workday's `jobFamilyGroup` facet, Greenhouse's per-department
  board endpoints. The board returns fewer rows, so you scrape less.

Post-hoc keyword filtering over an already-fetched dump is the wrong layer — a last resort only
for ATSes whose feed exposes no department facet.

## The core constraint

Every major ATS serves jobs **per tenant**: you call a public endpoint with a company's
board token / slug already in hand (e.g. `boards-api.greenhouse.io/v1/boards/stripe/jobs`).
**No ATS publishes a "list all our customers" endpoint** — that's deliberate. So discovering
the universe of slugs is a separate problem from scraping a known one.

## Discovery methods, ranked

**1. Reuse existing crawled slug lists (fastest).** Several open repos already maintain and
refresh slug lists, mostly built from Common Crawl. Filter them to companies worth tracking
and verify before adding.
- [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) — ~95k slugs across Greenhouse/Lever/Ashby/Workday, updated daily.
- [outscal/OpenJobs](https://github.com/outscal/OpenJobs) — `companies_v2.json` + a `probe-ats` script.
- [plibither8/jobber](https://github.com/plibither8/jobber), [adgramigna/job-board-scraper](https://github.com/adgramigna/job-board-scraper).

**2. Crawl / URL-index mining (scalable DIY).** [Common Crawl](https://commoncrawl.org) is a
free, monthly, ~2.5–3B-page crawl of the open web. You don't download the petabytes of page
data — you query its **URL index** (CDXJ or the columnar Parquet index, via Athena/DuckDB)
for ATS host patterns (`boards.greenhouse.io/*`, `jobs.lever.co/*`, `jobs.ashbyhq.com/*`, …),
regex out the slug, and dedupe. Free, passive, and the engine behind most of the lists above.

Two caveats keep recall below 100%: Common Crawl is a *sample*, not a complete crawl, and it
[caps URLs per host](https://commoncrawl.org/blog/introducing-the-host-index) (~150k even for
top domains). All three clean ATSes are path-based under a *single* host
(`boards.greenhouse.io/{slug}`), so any one monthly snapshot only captures a slice of all
slugs before hitting that cap — which is why the public lists union *many* snapshots. Improve
recall by combining sources rather than leaning on Common Crawl alone:
- **Union snapshots + Wayback** with [`cdx_toolkit`](https://github.com/commoncrawl/cdx_toolkit), which knits Common Crawl's monthly indexes *and* the Internet Archive Wayback CDX (a separate free crawl with different blind spots) into one virtual index.
- **Mine job aggregators / Google for Jobs**, where companies' apply links *are* ATS URLs — a jobs-specific source has a far higher slug hit-rate than a generic crawl, and it's fresher.

Note this whole family skews US/global tech and is thin on India (see the India section); for
that segment, method 4 is the stronger lever. To build a *focused crawler* over these sources
rather than consume them ad hoc, see [`crawler-design.md`](./crawler-design.md).

The Wayback feeder, concretely: the Internet Archive's CDX API indexes every URL it ever
archived, so one request per ATS host enumerates its tenants. For a subdomain ATS,
`https://web.archive.org/cdx/search/cdx?url=freshteam.com&matchType=domain&fl=original&collapse=urlkey&output=text`
returns archived URLs like `acme.freshteam.com/...` — take the subdomain → tenant. For
path-based ATSes (Greenhouse/Lever/Ashby) query the board host and take the first path
segment instead. Which hosts are worth sweeping lives in one table,
[`scripts/discover/wayback_feeder.py`](../../scripts/discover/wayback_feeder.py)'s
`ATS_HOSTS` — 18 ATSes over 34 hosts, derived from the scrapers' own URL construction and
cross-checked against `data/validate/liveness/{ats}.csv`. Both harvesters read it and sweep every
host an ATS serves from, writing one `data/wayback-ats/{ats}.csv` each. That last part matters,
because an ATS is rarely one host: Zoho spreads 8,197 known slugs over 8 TLDs of which
`zohorecruit.com` holds 6,101; Greenhouse's EU pods carry 824 rows (497 live); Lever's EU host 154
(92 live); and Workable serves two *shapes* at once — 15,238 rows on `apply.workable.com/{slug}`
and 1,623 on `{slug}.workable.com` — so style is per host, not per ATS. It's a second
feeder beside the Common Crawl miner
(the actual run is written up in [`common-crawl-mining.md`](./common-crawl-mining.md)): a
different archive with different gaps (Wayback found ~2,210 Freshteam tenants vs the miner's
~1,925, only partly overlapping), so union the two. Output is candidate-grade — historical,
so it includes dead boards and noise; validate before trusting.

A flat capped fetch silently truncates dense ATSes — use page-based harvesting for those.
The original PowerShell implementation (`wayback_feeder.ps1`, retired 2026-08-13 and unrelated to
today's `wayback_feeder.py`) capped each ATS at 50,000
URLs, which only covers the first few CDX index pages. An ATS Wayback crawled
*deep-but-narrow* (thousands of archived pages per board) loses most of its slugs past the
early alphabet. Detect it with `?showNumPages=true`:
a large page count (Zoho had 1,372) means the flat harvest got only a sliver. The fix is
[`scripts/discover/wayback_pages.py`](../../scripts/discover/wayback_pages.py), which fetches every CDX page
directly via `&page=N` (concurrent, resumable, deduping onto the existing CSV). Verified
corrections: **zoho 13 → 5,262, recruitee 496 → 7,083, ripplehire 86 → 167** — while shallow
providers with high page counts (freshteam, peoplestrong, greythr) were already complete, so
`showNumPages` only *flags* candidates and the re-harvest *confirms*. What does **not** work on
dense domains: CDX filters and `collapse=urlkey:N` both time out (they force a full server-side
scan), and resume-key pagination works but grinds sequentially through the deep pages —
page-based random access is the method.
[`wayback_paginate.py`](../../scripts/discover/wayback_paginate.py) keeps the resume-key walk for
the one case that needs it: a `--filter` that skips a dense apex sorting ahead of the subdomains.

Three things the extractor gets right that are easy to get wrong. **A slug is not lowercase.**
Hosts are case-insensitive, so the host half is lowered, but a path slug belongs to the ATS:
8,737 of SmartRecruiters' 12,706 ledger slugs are mixed-case
(`careers.smartrecruiters.com/RedBullGmbH`), and lowercasing them yields slugs that resolve to
nothing — the emitted slug keeps its casing, and dedup uses a lowered key. **A Workday board is a
*site* on a host, not a host**: `WorkdayScraper.slug_from` keeps the whole careers URL and its
`_URL_PATTERN` demands a site segment, so `extract` emits `{company}/{site}` and a URL that carries
both — a bare host would be a slug the scraper raises on. **A path slug may contain
a dot or an underscore** — Ashby and Lever let a Company use its domain as its slug
(`jobs.ashbyhq.com/adept.ai`), Greenhouse and Rippling have underscored ones
(`boards.greenhouse.io/edged_infrastructure`), and rejecting both cost 1,703 ledger rows and 199
live boards. Neither character is legal in a hostname label, so subdomains validate more strictly.
Widening the path rule means a file served from the board root (`ads.txt`, `manifest.json`) is no
longer distinguishable by shape, so those are rejected on a trailing file extension — a closed
set, unlike the open set of their filenames.

A fourth: **some ATSes are keyed by the whole host, not the label.** Eightfold and SuccessFactors
hand their scraper the board host as its slug (`https://{slug}/careers`), so `10xgenomics` names a
board their scraper cannot fetch while `10xgenomics.eightfold.ai` names one it can — every one of
Eightfold's 109 live ledger boards is stored the second way, and all 138 bare-label rows are dead.
That is the `host` style, and it is why style is a property of the host rather than of the sweep.

How complete is the table? Replaying every liveness-ledger URL through `extract` reaches all but
**198 live boards** outside the two known exclusions (`join`, 25,310, deliberately out; and
SuccessFactors' ~2,100 vanity hosts, unsweepable by design). Ten of the eighteen ATSes have no live
board out of reach at all: darwinbox, freshteam, keka, lever, personio, ripplehire, rippling,
workable, workday, zoho. Two live Personio rows carry no URL at all and are counted neither way;
they are the only reason that list says ten rather than nine.

The 198 are worth naming, because "unreachable" means three different things — teamtailor 158,
greenhouse 27, eightfold 5, smartrecruiters 4, recruitee 2, ashby 1, trakstar 1.
Teamtailor's 158 are a ledger artifact rather than a gap: the ledger stores a referral link
(`www.teamtailor.com/?utm_content=acme.teamtailor.com`) whose real board host,
`{slug}.teamtailor.com`, is exactly what the sweep targets. Greenhouse's 27 and the handful from
Ashby, Trakstar, SmartRecruiters and Recruitee are the ledger's own noise — rows whose stored URL
is an `api.greenhouse.io` endpoint or a bare `/home`, `/docs`, `/robots.txt`. Only Eightfold's 5 are a
true namespace gap: `careers.qualcomm.com`, `jobs.nvidia.com` and kin are vanity hosts that no
domain sweep can enumerate, which is why that ATS's comment says so.

Then validate: [`scripts/validate/check_liveness.py`](../scripts/validate/check_liveness.py) checks each
harvested board (the JSON feed for greenhouse/lever/ashby/recruitee, an HTTP liveness check
otherwise) and writes the live subset to `data/wayback-ats/active/{ats}.csv` — a real filter,
since only ~66% of archived boards are still live.

**3. Tech-stack lookup databases.** [BuiltWith](https://builtwith.com/greenhouse.io),
Wappalyzer, SimilarTech, Datanyze track "which sites use Greenhouse/Lever." BuiltWith Pro
exports the full company list (paid); some show free samples.

**4. Probe-from-a-seed (best fit for a curated tool).** Start from a list of *companies we
actually care about*, derive candidate slugs from each name, and hit each public ATS
endpoint — a 200 with live postings confirms the board. This is the natural next module for
HeadStart: input company names, output confirmed `(ats, slug)` rows for `companies.toml`.
Watch for slug collisions (two firms wanting the same slug) — confirmed hits still need an
eyeball pass.

## Certificate Transparency logs — investigated, does NOT work here

A tempting idea: subdomain-style ATSes give each customer a subdomain
(`{co}.recruitee.com`), and every TLS cert is logged publicly, so a crt.sh query like
`%.recruitee.com` should enumerate all tenants. **Tested 2026-06-15 — it doesn't.** Every
provider fronts its tenant space with a single **wildcard cert** (`*.recruitee.com`), which
appears as one CT entry and hides all tenant names.

| Provider | Tenant domain | In CT logs |
|---|---|---|
| Freshteam | `{co}.freshteam.com` | wildcard only — no customer names |
| Zoho Recruit | `{co}.zohorecruit.com` | wildcard only — 417 hosts, all Zoho's own |
| Recruitee | `{co}.recruitee.com` | wildcard + Cloudflare hash certs |
| Workable | `{co}.workable.com` | only Workable's own subdomains |
| Breezy | `{co}.breezy.hr` | only Breezy's own infra |
| Teamtailor | `{co}.teamtailor.com` | only Teamtailor's own subdomains |

Custom domains (`careers.acme.com` → CNAME to the ATS) do get per-domain certs, but those
live under the *customer's* apex, not the provider's, and often go through Cloudflare as
opaque `*.sni.cloudflaressl.com` hashes — so they don't yield a provider's tenant list
either.

CT still has one narrow use: given a company we already know (`acme.com`), a `%.acme.com`
query can surface `careers.acme.com`; resolving that CNAME tells us which ATS they're on.
That's per-company routing, not mass discovery.

Verify any future provider with:

```powershell
$d="recruitee.com"
(Invoke-RestMethod "https://api.certspotter.com/v1/issuances?domain=$d&include_subdomains=true&expand=dns_names").dns_names | Sort-Object -Unique
```

## Public endpoint reference

The clean-JSON ATSes (the ones worth supporting first):

| ATS | Endpoint | Identifier | Auth |
|---|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | board token | none |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` | slug | none |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` | board name | none |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{id}/postings` | company id | none (published) |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{slug}` | slug | none |
| Recruitee | `https://{slug}.recruitee.com/api/offers/` | subdomain | none |
| Personio | `https://{slug}.jobs.personio.de/xml?language=en` | subdomain | none (XML) |

Workday is the outlier (tenant + data-center + site path, POST to a `cxs` endpoint) and is
not cleanly enumerable — skip it.

**India-specific ATSes (the next tier to support).** These cover most Indian employers but
lack the clean public feeds above. Confirm each per provider before building a scraper —
don't assume a JSON endpoint exists.

| ATS | Career host | Public job feed | Status / note |
|---|---|---|---|
| Zoho Recruit | `{co}.zohorecruit.com` | API exists, but not a clean unauth JSON like Greenhouse | verify per-tenant public access |
| Keka | `{co}.keka.com/careers` | none documented — render + LLM-extract | HR suite; ATS is one module |
| Darwinbox | custom careers site | none documented — render + LLM-extract | HR suite; enterprise / GCC |
| Freshteam | `{co}.freshteam.com` | n/a — **being sunset** (renewals end 2026-03-07) | deprioritize; shrinking base |

SmartRecruiters and Workday (both in the table above) are the enterprise / GCC path for India.

## Pipeline for India coverage

India isn't the target — the target is global (see Scope) — but it's the **hardest,
widest-gap segment**, so it gets its own write-up. The same two-stage shape works for any
region: **seed a list of employers, then resolve each to an ATS + slug.** The company→slug
step is *resolution*, not semantic search: a slug is an exact string, so you find and confirm
it deterministically. Semantic search helps *grow the seed*, not map the tenant.

**Stage 1 — seed of Indian tech employers (not "all companies").** The full
[MCA register](https://www.data.gov.in/catalog/company-master-data) is 2–3M active entities,
almost all dormant / tiny / non-tech — wrong altitude, and resolving it is mostly dead ends.
Seed instead from companies that actually hire engineers online:
[Wellfound/AngelList India](https://wellfound.com/startups/location/india) (~5,900 actively
recruiting), the Startup India registry, Tracxn, NASSCOM members, YC's India companies, GCC
(Global Capability Center) lists, or a Diffbot KG query filtered to India + software (which
returns firmographics *and* the website domain stage 2 needs). Order of magnitude: thousands
to low tens of thousands — tractable.

A starter seed lives in [`config/seed_india.csv`](../config/seed_india.csv)
(`name,domain,sector,source`): ~108 hand-curated employers plus ~200 India-region YC
companies pulled from the public [yc-oss/api](https://github.com/yc-oss/api) (daily-updated,
filtered to `regions` = India — a legitimate front door, no scraping). ~310 rows, accurate
but not exhaustive. Grow it from the bulk sources above; further open options include the
[DPIIT/Startup India open data](https://www.data.gov.in/catalog/startup-recognized-dpiit)
(mostly aggregate counts, not per-company) and published company CSVs on GitHub. Note: GCCs /
India dev-centers of global firms resolve to the *parent's* global ATS, not an India-specific
one.

**Stage 2 — resolve company → ATS + slug.** For each seeded domain: fetch the careers page,
detect the outbound ATS link / embed, extract the slug; fall back to probing derived
candidate slugs against the public APIs (method 4). A search API (Exa / SerpAPI) helps
*locate* a buried careers/ATS URL; an AI renderer (Crawl4AI / Firecrawl) handles JS-heavy or
messy pages. Output confirmed `(ats, slug)` rows for `companies.toml`.

**Yield caveat — the hole is India-shaped.** Indian SMEs and startups skew to Zoho Recruit,
Freshteam, and Keka; enterprises and GCCs to Darwinbox and Workday — mostly the ATSes
*without* clean public JSON. Only a minority of (newer, VC-backed, global-facing) Indian firms
run Greenhouse/Lever/Ashby. So the cleanly-scrapable slice is small; real India coverage means
supporting the India-specific tier above, some of which needs render + LLM-extract rather than
a JSON call. The seed list doesn't remove that work — it tells you which messy sites are worth
it.

## Sources

- [6 ATS platforms with public APIs](https://fantastic.jobs/article/ats-with-api), [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html), [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api)
- CT mechanism: [wildcard blind spot](https://inventivehq.com/blog/subdomain-discovery-using-certificate-transparency-logs), [subdomain-via-CT guide](https://sidxparab.gitbook.io/subdomain-enumeration-guide/passive-enumeration/certificate-logs)
- CT per-provider results verified by live Cert Spotter queries on 2026-06-15.
- India: [MCA Company Master Data](https://www.data.gov.in/catalog/company-master-data), [Wellfound India](https://wellfound.com/startups/location/india), [best ATS in India](https://asanify.com/blog/human-resources/best-applicant-tracking-system-india-2025/); [Freshteam sunset (renewals end 2026-03-07)](https://www.peoplematters.in/news/business/freshworks-to-end-freshteam-hr-product-stop-renewals-from-march-2026-47939).
