# ATS discovery crawler — design

A purpose-built crawler that discovers ATS tenant boards **directly**, instead of inheriting
whatever Common Crawl's general crawl happened to capture. This is the "specialize on ATS
data" build. It complements [`overview.md`](./overview.md): that doc
catalogs *what discovery sources exist*; this one designs *how to build your own focused
crawler* on top of them.

_Last updated: 2026-06-18._

## Core principle: not a general spider

The single most important design decision: this is **seed-driven and shallow**, not an
open-web spider. You crawl the careers pages of a *company list* and follow known ATS
signatures; you do not crawl the web at large. A generic spider hits the frontier-bootstrap
problem (no seed → wander the whole web → mostly non-ATS pages), which is exactly how generic
crawling loses to a seeded, signature-driven one. The crawl depth is tiny: company homepage →
"Careers" link → ATS board. Everything else is targeted expansion.

## Dataflow

```
SEEDS (company domains: config/seed_india.csv, YC, MCA-IT-filtered, jobhive)
  │                                            ┌──────────────────────┐
  ▼                                            │  DISCOVERY FEEDERS    │
[Frontier] ◄───────────────────────────────── │  (find tenants you    │
  │  prioritized host/URL queue                │   have no company for)│
  ▼                                            │  • CC WAT link-graph  │
[Fetcher]  polite: robots.txt, per-host        │    (who links to      │
  │        rate-limit, cache, real UA          │     *.freshteam.com)  │
  ▼                                            │  • Search API         │
[ATS Detector]  signature match                │    (site:ats-host)    │
  │   host / path / embed-script / redirect    │  • Wayback CDX        │
  ├── no match ─► [Link Extractor]             │  • passive DNS / CT   │
  │               careers + aggregator links ──┤    (subdomain ATSes)  │
  │               + new company domains ───────┘                       │
  ▼ match                                                              
[Tenant Extractor] → (ats, tenant_id, board_url)
  │
  ▼
[Registry]  dedup; (ats, tenant, board_url, first_seen, last_seen, status)
  │
  ▼
[Validator]  hit the job feed → live? open_roles>0? → confirmed
```

## Components

**1. Frontier / seed manager.** A prioritized queue of hosts/URLs to check. Seeds are company
domains (from `config/seed_india.csv`, YC, an MCA-IT-filtered list, the `data/ats-companies/`
pools). The expander feeds new domains/hosts back in. Priority favors company homepages and
likely-careers URLs over arbitrary links.

**2. Polite fetcher.** Honors `robots.txt`, rate-limits per host, caches responses (never
refetch within a TTL), retries with backoff, sets an honest identifiable User-Agent, and caps
concurrency per host. This is also the legality boundary — see Guardrails.

**3. ATS detector.** The heart of the system: a signature registry that classifies a fetched
page as a board for a specific ATS (or not). Signals are URL host/path patterns, embedded
iframe/script `src`, redirect targets, and page markers. See the signature table below.

**4. Tenant extractor.** Given a detected ATS, pull the tenant id — a path slug for
path-based ATSes (`jobs.lever.co/{slug}`) or the subdomain for subdomain-based ones
(`{slug}.recruitee.com`).

**5. Focused link expander.** From a fetched page, extract only links worth following:
careers/jobs links, aggregator/listing pages, and outbound company domains. Push those into
the frontier. "Focused" = ignore the 99% of links that won't lead to an ATS board.

**6. Registry / store.** Dedup and persist every tenant with provenance and lifecycle (schema
below). This is the durable asset.

**7. Validator / liveness.** Confirm a discovered tenant resolves to a real board with a live
job feed and at least one open **engineering** role — reusing the Stage-2 probe. The tech
focus lives here and at the source query (Lever `?department=`, Workday `jobFamilyGroup`), not
as a post-hoc pass over already-scraped jobs. In resolve mode the seed domain disambiguates
slug collisions — keep the board that matches the company you started from.

**8. Discovery feeders.** How you find tenants with *no company in hand* — the right column of
the dataflow. The CC miner in `data/` (producing `india_ats_tenants.csv`) is already one
feeder. Add the others incrementally.

## ATS detector — the signature registry

This is the compounding IP. One entry per provider; grow it as you meet new ATSes.

| ATS | URL signature | Embed / script tell | Tenant id |
|---|---|---|---|
| Greenhouse | `(job-)?boards.greenhouse.io/{slug}` | `greenhouse.io/embed/job_board?for={slug}` | path slug |
| Lever | `jobs.lever.co/{slug}` | Lever postings widget | path slug |
| Ashby | `jobs.ashbyhq.com/{slug}` | `api.ashbyhq.com/posting-api/...` | path slug |
| Workday | `{tenant}.{dc}.myworkdayjobs.com/{site}` | Workday JS app shell | subdomain + site |
| Recruitee | `{slug}.recruitee.com` | `recruitee.com` widget / `/api/offers/` | subdomain |
| Workable | `apply.workable.com/{slug}` · `{slug}.workable.com` | Workable widget script | path / subdomain |
| Zoho Recruit | `{slug}.zohorecruit.{com,eu,in}` | Zoho careers embed | subdomain |
| Keka | `{slug}.keka.com/careers` | Keka careers iframe | subdomain |
| Darwinbox / GreytHR / Jobsoid | `{slug}.{darwinbox,greythr,jobsoid}.com` | provider careers iframe | subdomain |
| PeopleStrong / Qandle / RippleHire / TurboHire / Beehive | provider host patterns | provider careers iframe | subdomain |

Path-based ATSes (Greenhouse/Lever/Ashby) skew US/global; the subdomain-based ones are the
India long tail your CC miner already surfaced. Note CC found these where Certificate
Transparency could not — wildcard certs hide their tenants from CT, but CC indexed the
actual crawled subdomain pages (see `overview.md`).

## Two modes, and build order

**Resolve mode** — frontier = company domains, find each one's board. High precision, bounded
by your company list, directly grows coverage. This is the MVP and it reuses the Stage-2
probe almost entirely: homepage → find "Careers" link → fetch → detect → extract → validate.

**Discover mode** — the feeders surface tenants you have no company for. Open-ended, catches
the long tail, lower precision, needs a link source.

Build order: ship **resolve mode + the detector registry first** (80/20, reuses existing
code). Then bolt on feeders one at a time — your CC miner already exists; add Wayback CDX and
a search-API `site:` feeder next. Do not build an open spider.

## Discovery feeders (detail)

- **CC WAT link-graph** — Common Crawl's WAT files hold the page→link graph; query for pages
  that link to an ATS host (`*.freshteam.com`, `boards.greenhouse.io`) to get both new tenants
  and new linking company domains. Goes beyond the URL-index mining the current miner does.
- **Search API `site:` feeder** — query a search API (SerpAPI / Brave / Bing) for an ATS host
  pattern to enumerate indexed tenant pages. Fresh, but bounded by what the engine indexed.
- **Wayback CDX** — a second, independent crawl with different blind spots; same host/domain
  query interface as CC, unioned via `cdx_toolkit`.
- **Passive DNS / CT** — minor: works only for ATSes that mint per-tenant or custom-domain
  certs/records; useless for the wildcard-cert subdomain ATSes (most). Keep as a low-priority
  feeder.

## Registry schema

One row per discovered tenant:

```
ats          provider key (greenhouse, lever, recruitee, zoho, keka, ...)
tenant       slug or subdomain label
board_url    canonical board / careers URL
feed_url     resolved job-feed/API endpoint (null until an adapter resolves it)
source       how found (cc, careers-crawl, search, wayback, seed-probe)
first_seen   date
last_seen    date last confirmed live
status       new | live | dead | no-feed
open_roles   last validated count
```

`status` + `last_seen` are what make this better than a crawl snapshot: you prune dead hosts
and track freshness, which CC cannot give you.

## Guardrails (non-negotiable)

Honor `robots.txt`, rate-limit per host, cache to avoid refetching, send an honest
User-Agent, and **never circumvent anti-bot protections**. You are reading public careers
pages and public job feeds — the same surface a browser hits — so stay on that side of the
line: no challenge-solvers, no protection bypass. Discovery is legitimate; evasion is not.

## Honest limits

No focused crawler reaches 100%. You capture what is linked, indexed, or probable, via a
*union* of feeders — there is no oracle for the full tenant set, because no ATS publishes its
customer list. Two real costs to plan for: a meaningful fraction of any mined host list is
dead/parked (especially older CC snapshots), so liveness validation is mandatory before you
trust counts; and most India ATSes have no clean JSON feed, so per-ATS extraction adapters
(render + parse) are the bulk of the remaining work — and the part that differentiates this
from every generic aggregator.

## How it fits the repo

- **Seeds** come from `config/seed_india.csv` and `data/ats-companies/`.
- **Resolve mode** generalizes the Stage-2 probe; confirmed rows flow into
  `config/companies.toml` (the curated live-scrape list) via the existing
  `ats = greenhouse|lever|ashby` model, extended per new ATS adapter.
- **Discover mode** is fed by the CC miner already writing `data/discover/india_ats_tenants.csv`.
- New ATS support = a detector signature entry + a feed adapter in
  `src/headstart/scrapers/`.

## Related

- [`overview.md`](./overview.md) — discovery sources, the CT-logs dead end,
  public endpoint reference, and the India coverage pipeline.
