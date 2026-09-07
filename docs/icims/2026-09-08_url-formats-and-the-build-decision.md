# iCIMS: the URL formats, and why the scraper got built after all

**Measured 2026-09-07/08.** Every figure below comes from live requests or from the archive, not
from docs or inference. Sweep scripts: `experiment/icims-scraper/`. Raw captures:
`experiment/icims-scraper/artifacts/`.

## The prior verdict was right about its numbers and wrong about its denominator

`2026-09-07_india-volume-measurement.md` concludes **"do not build an iCIMS scraper"**, on the
grounds that iCIMS offers *"41 India boards and ~141 real software jobs"* against Zwayam's 224
hiring boards — about one-tenth the yield.

Its measurements hold up. Its scope does not. CLAUDE.md's first scope rule is explicit:

> **Companies: global, not India-only.** India is a strong sub-segment and where coverage started,
> but the target is companies worldwide — don't scope discovery, scrapers, or data to India.

India is ~0.6% of the iCIMS corpus, by that document's own measurement. Judging the provider by
its Indian software volume measures the 0.6% and discards the rest. Measured globally, through the
surface the scraper actually reads:

| | measured 2026-09-08 |
|---|---|
| tenants probed (full Wayback roster) | 6,430 |
| non-tenant hosts filtered out (vendor infra + `.i.` mirrors, all `jobs=0`) | 107 |
| **live boards** | **2,040** |
| **Hiring Boards** (`min_jobs>=1`) | **1,583** |
| live but empty | 457 |
| **jobs reachable** | **143,964** |
| jobs per board | median 9, p90 133, max 8,477 |

For the comparison the prior document chose: Zwayam shipped at **224 Hiring Boards**. iCIMS has
**1,583**. The dead-end verdict is withdrawn on scope, not on any disputed measurement.

Two of its incidental findings were also superseded, both because it never checked `robots.txt`:
it reports *"There is no JSON endpoint"* and treats the paginated HTML as the listing surface,
having missed the sitemap that 85% of boards declare. Its `/jobs/search` pagination analysis is
correct but describes a surface this scraper does not use.

## URL formats

Censused over **481,000 archived URLs** spanning the whole Wayback CDX index for `icims.com`
(320 pages, sampled at stride 10 — the index is ordered by SURT key, so a contiguous run of pages
is one slice of the alphabet rather than a sample).

### Host formats

| shape | URLs | is it a board? |
|---|---|---|
| `{tenant}.icims.com` | 448,560 | **yes — the only board suffix** |
| `{tenant}.i.icims.com` | 32,708 | no — archival mirrors |
| `www.{tenant}.icims.com` | 5,311 | yes, but the same board |
| `icims.com` / `www.icims.com` | 84+ | no — the vendor's own site |
| `{x}.{customer}.icims.com` | ~250 | no — per-customer subdomains |

`.i.icims.com` looks like a second tenant namespace and is not: the shape is
`{id}-{date}-{customer-domain}.i.icims.com` — `1051-20120703-www-netcentrics-com.i.icims.com` is a
2012 snapshot of netcentrics.com. These are iCIMS' own site mirrors. A single spot-check of
`career-celanese.i.icims.com` returned 404 and was nearly read as "that suffix doesn't exist";
the census is what showed 32,708 URLs there and what they actually are.

`www.{tenant}.icims.com` is rare but real (`www.careers-blarneycastleoil.icims.com`). It must
collapse to the same Board or it becomes the duplicate-Board class CLAUDE.md documents.

### Path formats

| shape | URLs | note |
|---|---|---|
| `/jobs/{id}/{slug}/job` | 37,841 | the canonical job page |
| `/jobs/{id}/{slug}/login` | 10,530 | job-scoped, not a board |
| `/jobs/{id}/{slug}/referral` | 3,243 | |
| **`/jobs/{id}/job`** | **2,731** | **slug-less job URL** |
| `/jobs/search` | 2,064 | the board |
| `/jobs/intro` | 1,648 | board landing page |
| `/connect/*` | ~3,000 | talent network, never a job |
| `/jobs/{id}/{slug}/gtm.js` | 193 | an asset under a job path |

The slug-less `/jobs/{id}/job` form matters because the obvious regex
(`/jobs/\d+/[^/]+/job`) does **not** match it. It is a discovery concern only:
**0 of 6,682 sitemap `<loc>` entries across 40 boards are slug-less** — the form appears in
archived crawl data, where the examples carry `?utm_source=indeed_integration`. So the scraper
(sitemap-only) is unaffected; the miners (archive-fed) need the looser pattern.

All sitemaps sampled carry at least one non-posting URL — `/jobs/intro` on 15 of 35,
`/jobs/search` on the rest — so the job-path match, not a name-based exclusion, is what separates
them.

## `datePosted`: fabricated on 22% of boards, real on the rest

The first version of this scraper discarded `datePosted` entirely, on the strength of **one**
board (`career-celanese`) whose value moved between two fetches three seconds apart. That
generalisation was wrong, and it shipped: every Job was served the sitemap's `lastmod` instead.

Re-measured over **54 random hiring boards**, two fetches 3.5s apart each:

| | boards |
|---|---|
| `datePosted` moves between fetches (fabricated `now - 2y`) | 12 |
| `datePosted` stable — a real posting date | **42** |

The two are separable in a **single fetch** by the millisecond field alone: **42/42 real values end
`.000Z`** (midnight- or hour-anchored), **0/12 fabricated ones do** (they carry live sub-second ms).
`_stated_date` encodes exactly that, and `posted_at` falls back to `lastmod` only where the board
fabricates.

The fallback is not equivalent, which is why it is second. Where both a real `datePosted` and a
`lastmod` existed, they diverged by 0–2,437 days:

| board | real `datePosted` | sitemap `lastmod` | error if lastmod is used |
|---|---|---|---|
| `careers-goaheadlondon` | 2020-01-02 | 2026-09-04 | 2,437 days |
| `careers-greenstreet` | 2026-07-01 | 2026-09-02 | 63 days |
| `careers-hk-merlinentertainments` | 2026-07-15 | 2026-08-26 | 42 days |
| `australia-kleinfelder` | 2026-08-25 | 2026-08-25 | 0 days |

`validThrough` is fabricated on every board measured and stays out of `_LD_KEEP` entirely.

## The tenant discriminator

`icims.com` domain-matching returns the vendor's ~120 infrastructure hosts alongside real
tenants, and `cc_miner.tenant_from`'s `host` branch does not apply `BLOCK`. Two measured filters:

- **The label must contain a hyphen.** All 2,040 live boards in the ledger do — zero exceptions —
  while infra is overwhelmingly single-word (`login`, `dev`, `social`, `api`, `staging`,
  `marketplace`, `webservices`, `talent`). This cannot be a word list: the bare words are exactly
  the ones a real tenant prefix extends (`careers-acadiahealthcare`, `jobs-collaborationbetterstheworld`).
- **Not a hyphenated infra prefix.** 42 infra hosts do carry a hyphen (`api-us-east-1`,
  `social-test`, `login-community`, `postman-tools`…). Every excluded prefix was checked against
  the live ledger and drops **0** live boards.

Against 200,000 real archived URLs the resulting pattern extracted **487 tenants** — 146 known
live, 249 known dead, **92 not in the ledger at all** — with zero malformed captures and zero
vendor infra.

`wayback_feeder.py` cannot apply the hyphen rule: its `extract(url, host, style)` takes no ATS and
widening that shared signature for one provider is not worth it. `valid()` plus the `"." in label`
guard reject the INFRA words and every deeper subdomain; roughly 70 single-label infra hosts still
get harvested, each costing one probe and landing as a dead ledger row. Bounded and
self-correcting, not silent — and verified to behave exactly that way rather than assumed.

## Reproducing

| script | what it measures |
|---|---|
| `experiment/icims-scraper/url_format_census.py` | the URL-shape census above |
| `experiment/icims-scraper/probe_surfaces.py` | per-board surfaces, agreement, pagination, field coverage |
| `experiment/icims-scraper/capture_har.py` | the browser HAR that settles "is there a JSON API" |
| `experiment/icims-scraper/ratelimit_probe.py` | concentrated single-host load |
| `scripts/validate/probe_icims.py` | the liveness ledger, through the scraper's own surface |
