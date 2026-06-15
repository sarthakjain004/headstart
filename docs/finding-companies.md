# Finding companies (ATS slug discovery)

How to discover the `(ats, slug)` pairs that go into `config/companies.toml`. The curated
list in that file *is* HeadStart's coverage, so growing coverage means finding more valid
slugs. This doc records what works, what doesn't, and why.

_Last updated: 2026-06-15._

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

**2. Common Crawl CDX mining (scalable DIY).** Scan the Common Crawl URL index for patterns
matching ATS domains (`boards.greenhouse.io/*`, `jobs.lever.co/*`, `*.ashbyhq.com/*`, …),
regex out the slug, dedupe across snapshots. Free, passive, and the engine behind most of
the lists above.

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

## India scoping tension

HeadStart targets India and the long tail of smaller employers first, but that's where this
is hardest. The clean public-JSON ATSes (Greenhouse/Lever/Ashby) skew US/global tech. Indian
SMBs lean on Zoho Recruit, Keka, Darwinbox, Freshteam, plus boards like Naukri / Instahyre /
Wellfound — which mostly lack tidy public JSON feeds and often mask the ATS behind a custom
domain. So the global slug lists are thin exactly on our target segment, and probe-from-seed
(method 4) against a hand-built India company list is the most reliable path there.

## Sources

- [6 ATS platforms with public APIs](https://fantastic.jobs/article/ats-with-api), [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html), [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api)
- CT mechanism: [wildcard blind spot](https://inventivehq.com/blog/subdomain-discovery-using-certificate-transparency-logs), [subdomain-via-CT guide](https://sidxparab.gitbook.io/subdomain-enumeration-guide/passive-enumeration/certificate-logs)
- CT per-provider results verified by live Cert Spotter queries on 2026-06-15.
