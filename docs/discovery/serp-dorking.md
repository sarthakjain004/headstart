# Search-engine dorking as a fourth discovery feeder

Whether to add a SERP-API feeder beside the three that already run — Common Crawl
([`cc_miner.py`](../../scripts/discover/cc_miner.py)), Wayback
([`wayback_pages.py`](../../scripts/discover/wayback_pages.py)) and urlscan
([`urlscan_miner.py`](../../scripts/discover/urlscan_miner.py)) — and if so, on which provider.

[`technique-ranking.md`](./technique-ranking.md) ranked this idea #2 of 17 and said "Build.
Cheap, genuinely absent, hits the non-derivable-slug class." **That ranking was half right, and
this document is the arithmetic that splits it.** The enumeration half does not survive contact
with the result caps. The resolution half does, and is cheaper than the ranking assumed.

_Written 2026-08-12. All quota, price and parameter claims below are from the provider's own
documentation or a live probe; every one is cited in §Sources. Where a primary source is silent,
this document says NOT VERIFIED rather than guessing._

## The bottom line

**Do not build the enumeration door. Build the resolution door, narrowly, on Tavily.**

Enumeration — sweeping `site:job-boards.greenhouse.io` and friends for unknown tenants — is
dominated by Common Crawl on every axis that matters. The best free SERP tier available to a new
signup yields on the order of **20,000 result rows per month**; a single Common Crawl sweep of
`cc_miner.py` produced **31,835 `(ats, tenant)` rows** and cost nothing but 105 CDX page fetches.
The SERP rows are also worse rows: SERP ranking is popularity-biased, so the tenants it surfaces
first are precisely the ones already in the ledger, while CDX enumerates the crawl's whole sample
of a host indifferently. The marginal-new-tenant rate is therefore *below* the raw ratio, not
above it.

Resolution — company-directed lookup for slugs name-derivation can never guess (Dream11 →
`lever:dreamsports`, Zomato → `smartrecruiters:Zomato1`, Razorpay →
`greenhouse:razorpaysoftwareprivatelimited`) — is a different economic shape and does survive.
It needs the *top ten* results, not two hundred, so the result cap is irrelevant. One query
resolves one company across every ATS at once. At 1,000 free queries/month that is 1,000
companies/month for $0 and no credit card, and it reaches a class of Board that no host
enumeration reaches by construction.

## The number the decision turns on: the result cap

Every general web-search API caps how deep a single query can be paginated. This is the number
that kills enumeration, so it is worth stating precisely per provider.

| Provider | Results/request | Max pages | **Hard cap per query** | Requests to exhaust one query |
|---|---|---|---|---|
| Google Custom Search JSON | 10 (`num`, max 10) | `start` ≤ 91 | **100** | 10 |
| Brave Web Search | 20 (`count`, max 20) | `offset` ≤ 9 | **200** | 10 |
| Exa `/search` | 100 (`numResults`, max 100) | no pagination param | **100** | 1 |
| Tavily `/search` | 20 (`max_results`, max 20) | no pagination param | **20** | 1 |
| Marginalia | 100 (`count`, 1–100) | `page` | not verified | — |
| Mojeek | 10 / 40 / 100 by plan | not verified | not verified | — |

Google's is documented in the sharpest terms of the lot, and confirms the premise in the brief:

> "The JSON API will never return more than 100 results, even if more than 100 documents match
> the query, so setting the sum of `start + num` to a number greater than 100 will produce an
> error. Also note that the maximum value for `num` is 10."
> — [`cse.list` reference](https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list)

So `start` accepts any uint32 but `start + num > 100` is a **hard error**, not a silent empty
page. That is actually the friendlier failure: a checkpointed sweep gets an explicit signal
rather than mistaking truncation for exhaustion.

Brave's cap is the highest of the free-tier options at 200, but it is not free depth — `count`
maxes at 20 and `offset` maxes at 9, so **draining one query's 200 results costs 10 billable
requests**. Depth and breadth come out of the same budget. This is the detail that collapses the
enumeration arithmetic below, and it is easy to get wrong by reading "200 results per query" as
"200 results per unit of quota."

Tavily and Exa have no pagination parameter at all. Their cap *is* their page size: whatever
`max_results` / `numResults` returns is everything you will ever see for that query string.

## What the cap forces the design to look like

A cap this low means **no ATS host can be enumerated by one query**. `site:job-boards.greenhouse.io`
matches millions of job pages; 100–200 of them is a rounding error, and worse, it is a
*rank-ordered* rounding error, so repeated identical queries return the same popular tenants
forever. Any enumeration sweep must therefore partition the query space so that each partition's
true result count falls under the cap, and must treat "returned exactly the cap" as **evidence of
truncation, not completeness**.

This repo already has the canonical worked example of that discipline, and it is worth reading
before designing another one. `urlscan_miner.py`'s docstring:

> "**The cap is the whole trick.** The free search API silently truncates at 100 results per
> query, so a plain `domain:{d}` query looks exhausted while hiding most of the cohort. Slicing
> the same query by scan date walks past it: half-year buckets took one Zoho sweep from 117 to
> 434 hosts."

The available partition axes for a SERP door, cheapest first:

- **Time.** Brave `freshness` (`pd`/`pw`/`pm`/`py`, plus explicit `2022-04-01to2022-07-30`
  ranges); Google `dateRestrict` (`d[n]`/`w[n]`/`m[n]`/`y[n]`). This is the urlscan trick exactly.
  It is also the only axis that is *free* of recall loss — every document has a date.
- **Lexical.** Append a discriminating token to the dork (`site:jobs.lever.co "Bengaluru"`,
  `… "Staff Engineer"`, `… "Series B"`). Cheap and unbounded, but partitions overlap
  unpredictably and each token silently drops every document that lacks it.
- **Geographic / linguistic.** Brave `country` (2-char codes) and `search_lang` (ISO 639-1).
  Bounded and orthogonal, but the partitions are wildly unequal in size.
- **Alphabetic on the slug.** Not available — no general SERP API exposes a URL-prefix operator,
  and `inurl:` is not in Brave's supported-operator list.

Note what this costs. A partition scheme fine enough to keep each bucket under 200 for a host as
dense as Greenhouse needs *thousands* of partitions, and each partition costs up to 10 requests
to drain. That is the whole free budget spent on one ATS, for a slice of one host, at a
popularity bias that favours tenants already known. Which is the arithmetic below.

## Google Custom Search is closed — verify this before planning around it

The brief assumed Google CSE as the default option. **It is no longer available to new
customers**, and this is stated twice on Google's own overview page:

> "Note: The Custom Search JSON API is closed to new customers. Vertex AI Search is a favorable
> alternative for searching up to 50 domains. Alternatively, if your use case necessitates full
> web search, contact us to express your interest in and get more information about our full web
> search solution. Existing Custom Search JSON API customers have until January 1, 2027 to
> transition to an alternative solution."

> "The following pricing applies only to existing Custom Search JSON API customers until the
> service discontinuation on January 1, 2027. This API is not available for new customers."
> — [Custom Search JSON API overview](https://developers.google.com/custom-search/v1/overview)

The quota it *would* have offered, for the record: 100 queries/day free, $5 per 1,000 beyond
that, capped at 10k queries/day. HeadStart is not an existing customer, so none of it is
reachable.

There is a second, independent blocker even for an existing customer. A Programmable Search
Engine only searches the open web if configured to, and that configuration is itself closed:

> "You have the option to set your custom search engine to search the entire web (**no new
> creation supported**), similar to a normal search on Google.com."

> "May have a subset of results from the Google index if you include more than ten sites"
> — [PSE help](https://support.google.com/programmable-search/answer/4513751)

This is the configuration detail the brief flagged as easy to get wrong, and it is decisive. A
PSE built today is a *site-restricted* engine. Listing the ~30 ATS hosts from `cc_miner.py`'s
target table would exceed the ten-site threshold and, by Google's own wording, return only "a
subset of results from the Google index" — an unquantified haircut on top of the 100-result cap.
The `siteSearch` / `siteSearchFilter` parameters (`"i"` include, `"e"` exclude) are real and
first-class, but they restrict *within* the engine's configured scope; they cannot widen a
site-restricted engine back out to the whole web.

**Scraping `google.com/search` directly is not a workaround — it is a Terms of Service
violation**, and the two halves of the proof are both first-party:

- `https://www.google.com/robots.txt` line 3, under `User-agent: *`, is `Disallow: /search`
  (with narrow `Allow:` carve-outs for `/search/about`, `/search/howsearchworks`, and
  `search?*tbm=map`).
- Google's Terms of Service prohibit "using automated means to access content from any of our
  services in violation of the machine-readable instructions on our web pages (for example,
  robots.txt files that disallow crawling, training, or other activities)", alongside the general
  "You must not abuse, harm, interfere with, or disrupt our services or systems".

The compliant routes to Google-derived results are therefore: an official Google API (closed, per
above), or a licensed reseller that assumes the scraping liability itself — SerpApi being the
explicit example, discussed under §Providers.

## Provider comparison

Free tiers verified 2026-08-12. "CC?" = credit card required to obtain the free tier.

| Provider | Free tier | CC? | Beyond free | Cap/query | `site:` + quoted phrase | Store returned URLs? |
|---|---|---|---|---|---|---|
| **Tavily** | **1,000 credits/mo** (basic = 1 credit) | **No** | $0.008/credit PAYG | 20 | `include_domains` (max 300), `exclude_domains` (max 150) | ToS **silent** — no bar |
| **Brave** | $5 credits/mo ≈ **1,000 req/mo** | **Yes** | $5 / 1,000 req, 50 QPS | **200** (20 × 10) | Yes — `site`, `""`, AND/OR/NOT | **Prohibited** beyond "transient" |
| **Exa** | $20 signup + $10/mo ≈ **1,400 searches/mo** | NOT VERIFIED | $7 / 1,000 (standard) | 100 | `includeDomains` (max 1200); docs say use it *instead of* `site:` | **Prohibited** except browser cache |
| **SerpApi** | 250 searches/mo, 50/hour | NOT VERIFIED | $25/mo → 1,000; $75 → 5,000 | not verified | Passthrough to Google | ToS **silent** |
| **Google CSE** | **Closed to new customers** | — | $5 / 1,000, 10k/day max | 100 | `siteSearch` / `siteSearchFilter` | — |
| **DataForSEO** | None found; **$50 minimum deposit** | Yes | per-request price NOT VERIFIED | not verified | Passthrough | not verified |
| **Mojeek** | "Free trial version, with limited queries. Get in touch." | Sales contact | £2–3 CPM | 10/40/100 by plan | Yes — full operator set | not verified |
| **Marginalia** | Free non-commercial key, **by email application** | No | one-time paid tier | `count` 1–100 | **No** — see below | CC-BY-NC-SA 4.0 (attribution + non-commercial + share-alike) |
| **SearXNG** | Self-hosted, $0 | No | — | per upstream engine | per upstream engine | n/a |
| **DuckDuckGo** | Instant Answer API only | No | — | **returns no web results** | n/a | n/a |

### The four that are disqualified outright

**DuckDuckGo — no web-results API.** Live-probed 2026-08-12. `api.duckduckgo.com/?q=site%3Ajobs.lever.co+engineer&format=json`
returns `Results: 0`, `RelatedTopics: 0`, empty `AbstractText`, empty `Type`. A control query
(`q=python`) returns a Wikipedia abstract and 10 `RelatedTopics` but still `Results: 0`. The
Instant Answer API returns encyclopedic answers, not ranked links; there is no DuckDuckGo product
that returns a SERP. Dead for both jobs.

**Marginalia — no `site:` operator, and the wrong index.** Live-probed 2026-08-12 against the
public API. `GET api.marginalia-search.com/public/search/jobs.lever.co` returns 20 results;
`.../search/site%3Ajobs.lever.co` returns an **empty body**, as does a `special:site` form. Host
restriction is not reachable through that path. Independently, Marginalia's index is small and
deliberately biased toward non-commercial, low-JS, obscure pages — the inverse of a SaaS ATS
board farm. Results are also CC-BY-NC-SA 4.0, which attaches share-alike and non-commercial
conditions to anything derived from them. Not worth pursuing.

**SearXNG — no index of its own.** It is a metasearch aggregator that forwards to upstream
engines, so it inherits every upstream's cap rather than escaping any, and it adds a failure
mode: SearXNG's own documentation warns that "public instances without proper protection are more
vulnerable to abuse of the search service, which may cause the external service to enforce
CAPTCHAs or to ban the IP address of the instance." A self-hosted instance run from GitHub
Actions would be querying upstream engines from shared CI egress IPs at automated rates — the
exact profile that gets CAPTCHA'd. It also routes around, rather than satisfies, the upstream's
ToS. Not resumable in any meaningful sense, because the failure is silent degradation of result
quality rather than an error.

**DataForSEO — $50 minimum deposit**, which fails the "cheap or free, prefer no credit card"
constraint before any other question is asked. I did not verify its per-request SERP pricing or
depth options; both `dataforseo.com/pricing` and `/pricing/serp` render their pricing tables
client-side and the numbers were not in the fetched HTML. **NOT VERIFIED** — but the minimum
deposit is decisive regardless.

**Mojeek** has the best operator support of any independent index and offers up to 100 results
per request on higher plans, but has **no self-serve free tier** — the API page offers only
"Free trial version, with limited queries. Get in touch." A sales conversation is not a side
project's front door, and the £2–3 CPM paid tiers are the same order as Brave's $5/1,000 without
Brave's self-serve signup.

### The three real contenders

**Brave** has the highest cap (200), genuine operator support (`site`, `""`, `AND`/`OR`/`NOT`,
`intitle`, `inbody` — though Brave labels these "experimental and in the early stage of
development"), a generous 50 QPS, and a live, simple endpoint. Two things count against it. A
credit card is required even at $0 — Brave states plainly that "the credit card requirement serves
as an anti-fraud measure… For free plans, the card is only used to confirm your identity and will
not be charged," which is honest but still fails the stated preference. And the ToS is the
strictest of the contenders on exactly the thing this feeder does:

> "Customer shall not… (i) store, cache, or create a database of Search Results, in whole or in
> part, other than transient storage required for operation of Customer Applications"
> — Brave Search API Terms of Use §3(b)

Brave's own FAQ confirms this is intentional and gated behind a commercial upgrade: "If you would
like to store the API results in part or whole… you will need to subscribe to a plan that
explicitly grants storage rights." A discovery feeder's entire output contract is a **persisted
CSV of returned URLs** (`ats,tenant,url`). That is a stored database of Search Results. Brave's
free tier does not permit the artifact this feeder exists to produce. This is the decisive strike
against Brave, and it is easy to miss because the quota and cap both look best-in-class.

**Exa** offers the largest free allowance (~1,400 searches/month) and a 100-result cap with
`includeDomains` accepting up to 1,200 hostnames — mechanically attractive. But Exa's ToS bans
storage "except for temporary files that are automatically cached by your web browser", bans
scraping-shaped access, and takes a "perpetual and irrevocable license to… reproduce, transmit,
display, publish, distribute, and modify any User Input and Output". Same storage problem as
Brave, plus a licence grant over the query stream. Exa is also a neural/semantic index tuned for
relevance rather than exhaustive host coverage, which is a poor fit for enumeration even setting
the terms aside.

**SerpApi** is the only provider that assumes the legal risk of Google-derived results, which is
a real and unusual offer:

> "SerpApi will assume the liabilities of scraping and parsing search engine results for both
> U.S. and international companies and individuals, with up to $2 million in coverage ("U.S.
> Legal Shield")…" — SerpApi ToS §13

Two carve-outs make it much less useful here than it first appears: the Shield "except the Free,
Starter, and Developer plans" — so it covers neither the $0 tier nor the $25 nor the $75 tier —
and it is "limited to claims brought under U.S. state or federal law in a U.S. court." At 250
searches/month the free tier is ~8 queries/day, an order of magnitude below Tavily and Brave.
SerpApi is the right answer if Google-quality results are a hard requirement and there is budget;
it is not the right answer for a free side project.

## The arithmetic

### Enumeration, honestly

Take Brave — the best-case free tier for this job, since it has the highest cap.

- Free budget: $5/month of credits at $5 per 1,000 requests = **1,000 requests/month**.
- Each request returns at most `count` = 20 results.
- **Ceiling: 1,000 × 20 = 20,000 SERP result rows/month.** Pagination does not improve this;
  reaching a query's full 200 costs 10 of the 1,000 requests. Depth and breadth are the same
  budget, so 20,000 rows is the number whichever way it is spent.

Now discount it:

- A SERP row for `site:job-boards.greenhouse.io` is a *job page*, not a tenant. Multiple rows
  collapse to one tenant. Distinct-tenant density is not something I can measure without a key —
  **NOT VERIFIED** — but 30–50% is a generous assumption for a host where popular tenants have
  hundreds of indexed job pages each.
- At 40%: **~8,000 tenant candidates/month**, before dedupe against the existing ledger.
- Those candidates are rank-ordered by popularity. The ledger already holds 15,783 Greenhouse
  candidates, 27,440 Workday, 16,653 Workable, 25,003 Join, 12,644 SmartRecruiters, 10,023 Lever
  (≈148,500 rows total across 19 ATSes). The overlap between "what a search engine ranks highly
  for a board host" and "what three archive feeders already found" is severe. Marginal *new*
  tenants are a fraction of the 8,000, and I cannot bound that fraction without running it.

Against that, the baseline in this repo, measured:

- **One `cc_miner.py` sweep of a single crawl produced 31,835 `(ats, tenant)` rows across 19
  ATSes**, from 105 checkpointed CDX pages. No key, no card, no quota, no cap, and it is
  re-runnable every month against a new crawl and against every past crawl.
- urlscan contributed **127 of 269 new Zoho Boards that no other source found** (2026-07-27),
  which is the shape of a feeder that earns its place: uniquely-sourced rows, not more of the same.

So the enumeration door costs a credit card and a ToS violation to produce, optimistically,
**~25% of one free Common Crawl sweep's row count, biased toward rows already held.** There is no
reading of these numbers where it is the next thing to build.

### Resolution, honestly

Different arithmetic entirely, because the cap stops mattering.

A resolution query is `"Dream11" (careers OR jobs)` restricted to the ATS board hosts. The answer,
if it exists, is in the **top few results** — the search engine has already performed the
company-name → board-URL join. Twenty results is ample; two hundred is waste.

- Tavily free: **1,000 credits/month, basic search = 1 credit, no credit card.**
- `include_domains` accepts **up to 300 domains**, so one call covers *every* ATS host in
  `cc_miner.py`'s target table simultaneously — Greenhouse's six hosts, Lever's four, Ashby,
  SmartRecruiters, Workable, Rippling, Workday, Oracle, Zoho's four, Personio, Darwinbox,
  Eightfold, Keka, Recruitee, Teamtailor, Trakstar, SenseHQ, RippleHire. That list is ~40 hosts,
  well inside 300.
- **Therefore: 1 credit = 1 company resolved against all supported ATSes.**
- **Yield: 1,000 companies/month, $0, no credit card.** ≈33/day sustained, or a 1,000-company
  burst once a month.

That is a genuinely useful number against this repo's actual gaps. `CLAUDE.md` lists 22 Boards on
already-supported ATSes blocked purely on a careers-page/redirect scan (Darwinbox 11, Keka 6,
Workday 3, Greenhouse 2), plus the non-derivable-slug class. `technique-ranking.md` records that
the fingerprinter is bounded by a ~310-row seed and that **79% of 316 fingerprinted hosts were
opaque to no-JS curl** — meaning four in five companies need a headless render to resolve. A SERP
call is far cheaper than a headless Chrome render and answers the same question for the subset
where the search index already knows the answer. Run SERP resolution first, fall back to headless
only on misses.

The honest caveat: resolution is bounded by the **seed**, not by the SERP quota. 1,000
companies/month is only valuable if there are 1,000 companies worth resolving, and assembling
that global seed is `technique-ranking.md`'s rank-1 item, still unbuilt. **The SERP resolution
door is worth building, but it is strictly downstream of the seed.** Building it first would be
building a faster answer to a question nobody is yet asking.

## Recommended shape, if and when the seed exists

Tavily, for three reasons: 1,000 credits/month is the largest free allowance obtainable **without
a credit card**; `include_domains` is a first-class parameter that covers the whole ATS host list
in one call rather than one dork per host; and it is the only contender whose terms do not
affirmatively forbid persisting the returned URLs. Tavily's ToS bans building "a competitive
product or service" and models "that compete with the Company" — HeadStart is a job search over
ATS boards, not a search engine or a retrieval API, so that clause is not engaged. The terms are
**silent on caching and on redistribution of returned results** (the strings "cache"/"caching"
appear zero times), which is permissive by omission rather than by grant — worth re-reading before
any redistribution, though the feeder only persists URLs internally.

Verified request shape (endpoint live-probed 2026-08-12: `POST https://api.tavily.com/search`
without a key returns **HTTP 401**, confirming host and path):

```
POST https://api.tavily.com/search
Content-Type: application/json
Authorization: Bearer tvly-YOUR_KEY

{
  "query": "Dream11 careers",
  "search_depth": "basic",
  "max_results": 20,
  "include_domains": [
    "job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io",
    "jobs.lever.co", "jobs.eu.lever.co",
    "jobs.ashbyhq.com",
    "jobs.smartrecruiters.com", "careers.smartrecruiters.com",
    "apply.workable.com", "ats.rippling.com",
    "myworkdayjobs.com", "oraclecloud.com",
    "zohorecruit.com", "zohorecruit.in", "zohorecruit.eu",
    "darwinbox.in", "eightfold.ai", "keka.com", "recruitee.com",
    "teamtailor.com", "hire.trakstar.com", "sensehq.com", "personio.com"
  ]
}
```

`search_depth: "basic"` costs **1 credit**; `"advanced"` costs 2 and buys nothing here, since the
value is the URL not the snippet. Response URLs are then parsed with the per-ATS regexes already
in `cc_miner.py`'s `ATS_PATTERNS` — that table is the URL-shape registry this needs, and it
should be imported, not re-derived (the same argument `technique-ranking.md` makes at rank 4).
Output is the standard feeder contract, `ats,tenant,url`, appended and deduped, then handed to
[`check_liveness.py`](../../scripts/validate/check_liveness.py) like every other feeder. Precision
does not matter; liveness settles it.

Checkpointing is trivial and matches `urlscan_miner.py`: the unit of work is one company, so
write the resolved rows and the company name to disk after each call and skip already-done
companies on restart. A killed run costs one credit. Both a laptop and GitHub Actions can run it;
the only state is an API key and the done-set.

**Brave is the fallback** if the no-credit-card constraint is relaxed *and* the storage clause is
resolved — its 200-result cap and 50 QPS make it the only contender that could also serve
enumeration, if enumeration ever became worth doing. It is not, today.

## What would have to change for the enumeration door to be worth building

Stated concretely, so this can be re-tested rather than re-argued:

1. **A free tier two orders of magnitude larger**, or a result cap in the thousands. At 20,000
   rows/month the door cannot compete with a free CDX sweep. At ~2M rows/month it could.
2. **A URL-prefix or `inurl:`-style operator**, which would let a sweep partition the slug space
   alphabetically and *prove* coverage rather than sample it. No provider surveyed offers one.
3. **Common Crawl and Wayback going stale on a host we care about.** The freshness argument is
   the honest one for this door — CC is a monthly snapshot and describes itself as "a sample of
   the web", Wayback is historical — so a *newly launched* ATS or a host neither archive covers
   would flip the calculus. That is a narrow, testable condition: pick a host, compare CDX
   coverage against a handful of SERP probes, and only then build. Note the per-host cap figure
   (~150k URLs) cited in [`overview.md`](./overview.md) is **not confirmed** on Common Crawl's
   host-index post, which states only that the crawl "is a sample of the web"; treat the specific
   number as unverified.
4. **The seed reaching a scale where 1,000 resolutions/month is the bottleneck.** At that point
   paying for resolution volume is rational, and SerpApi's Legal Shield (on a $150+ plan) or
   Brave's $5/1,000 become reasonable purchases.

None of these hold today.

## What I could not verify

Recorded so the gaps are not mistaken for findings:

- **DataForSEO per-request SERP pricing and depth options** — pricing tables render client-side;
  only the "$50" minimum payment was in the served HTML. The minimum deposit disqualifies it
  regardless, so I did not pursue it further.
- **Whether Exa and SerpApi require a credit card** for their free tiers. Neither page contains
  the string "credit card". SerpApi's signup form shows no payment field, which is evidence, not
  a statement.
- **Mojeek's and Marginalia's exact pagination caps** — not documented on the pages fetched.
- **Distinct-tenant density per SERP page** for an ATS host dork. This needs an API key and a
  live run. The 30–50% figure used above is an assumption, flagged as such; the enumeration
  conclusion holds across that whole range and well beyond it, so the gap is not load-bearing.
- **Brave's rate-limit header names and 429 semantics** — the rate-limiting doc page did not
  render its content. Not load-bearing given the recommendation.

## Sources

Google Programmable Search / Custom Search JSON API
- [Custom Search JSON API overview](https://developers.google.com/custom-search/v1/overview) — closure to new customers, Jan 1 2027 discontinuation, 100 queries/day free, $5/1,000, 10k/day cap.
- [`cse.list` REST reference](https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list) — 100-result hard cap, `start + num > 100` error, `num` max 10, `siteSearch`, `siteSearchFilter` (`i`/`e`), `dateRestrict`, `sort`.
- [What is Programmable Search Engine?](https://support.google.com/programmable-search/answer/4513751) — "search the entire web (no new creation supported)"; "subset of results from the Google index if you include more than ten sites".
- [`https://www.google.com/robots.txt`](https://www.google.com/robots.txt) — `Disallow: /search` under `User-agent: *` (fetched 2026-08-12).
- [Google Terms of Service](https://policies.google.com/terms) — prohibition on "using automated means to access content… in violation of the machine-readable instructions on our web pages (for example, robots.txt files…)".

Brave
- [Brave Search API](https://brave.com/search/api/) — $5/1,000 requests, $5 free credits/month, 50 QPS, credit-card anti-fraud statement, storage-rights FAQ.
- [Web Search query parameters](https://api-dashboard.search.brave.com/app/documentation/web-search/query) — `count` max 20, `offset` max 9, `freshness`, `country`, `search_lang`.
- [Web Search get-started](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started) — endpoint `https://api.search.brave.com/res/v1/web/search`, `X-Subscription-Token` header.
- [Brave Search operators](https://search.brave.com/help/operators) — `site`, `""`, `AND`/`OR`/`NOT`, `intitle`, `inbody`; "experimental and in the early stage of development".
- [Brave Search API Terms of Use](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service) §3(b) — storage/caching, redistribution, AI-training restrictions; attribution optional.

Tavily
- [API credits](https://docs.tavily.com/documentation/api-credits) — "You get 1,000 free API Credits every month. No credit card required."; basic 1 credit / advanced 2; $0.008/credit PAYG.
- [Pricing](https://www.tavily.com/pricing) — "Researcher / Free / 1,000 API credits / month / No credit card required".
- [`/search` endpoint reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) — `max_results` maximum 20, `include_domains` max 300, `exclude_domains` max 150, `search_depth`, `time_range`; no pagination parameter.
- [Terms of Service](https://www.tavily.com/terms) §3.2, §6.6, §9.1 — competing-product restriction; silence on caching/result redistribution; customer owns Customer Input.

Exa
- [`/search` reference](https://exa.ai/docs/reference/search) — `numResults` "maximum public limit is 100 results", `includeDomains`/`excludeDomains` maxItems 1200, "Use this parameter for domain or path filtering instead of adding a `site:` operator"; no pagination.
- [Pricing](https://exa.ai/pricing) and [docs pricing](https://docs.exa.ai/reference/pricing) — $20 signup credits (~2,800 searches), $10/month Free Tier credits, $7/1k standard search.
- [Terms of Service](https://exa.ai/terms) (PDF) §1.2(c), §4.2(a)/(e)/(f)/(j) — browser-cache-only storage, redistribution ban, competitive-product ban, anti-scraping clause, perpetual licence over Input and Output.

SerpApi
- [Pricing](https://serpapi.com/pricing) — "Free / $0 / 250 searches per month / 50 throughput per hour"; Starter $25/1,000, Developer $75/5,000, Production $150/15,000, Big Data $275/30,000.
- [Legal](https://serpapi.com/legal) §13 U.S. Legal Shield (verbatim, incl. Free/Starter/Developer exclusion and U.S.-court limitation), §2, §12, §14, §22.

Others
- [DataForSEO pricing](https://dataforseo.com/pricing) — "$50" minimum payment; SERP per-request rates not present in served HTML.
- [Mojeek Web Search API](https://www.mojeek.com/services/search/web-search-api/) — "Free trial version, with limited queries. Get in touch."; £2/£3 CPM; results per request 10/40/100 by plan; "All API plans work with Mojeek's full set of advanced search commands and operators".
- [Marginalia API](https://about.marginalia-search.com/article/api/) — free non-commercial key by email, CC-BY-NC-SA 4.0, `count` 1–100, public key rate-limited.
- [SearXNG — why use a private instance](https://docs.searxng.org/own-instance.html) — "public instances without proper protection are more vulnerable to abuse… which may cause the external service to enforce CAPTCHAs or to ban the IP address of the instance".
- [Common Crawl — introducing the host index](https://commoncrawl.org/blog/introducing-the-host-index) — "It is a sample of the web."

Live probes run 2026-08-12 (primary evidence, this machine)
- `api.duckduckgo.com/?q=site%3Ajobs.lever.co+engineer&format=json&no_html=1` → `Results: 0`, `RelatedTopics: 0`, empty `Type`. Control `q=python` → 10 `RelatedTopics`, `Results: 0`.
- `api.marginalia-search.com/public/search/jobs.lever.co` → 20 results; `.../search/site%3Ajobs.lever.co` → empty body; `.../search/special%3Asite%20jobs.lever.co` → empty body.
- `POST https://api.tavily.com/search` (no key) → HTTP 401. `GET https://api.search.brave.com/res/v1/web/search?q=test` (no key) → HTTP 422.

Repo baselines measured 2026-08-12
- `data/discover/cc_ats_tenants.csv` — 31,835 `(ats, tenant)` rows from one crawl; `cc_miner_checkpoint.txt` — 105 pages.
- `data/validate/liveness/*.csv` — ≈148,500 candidate rows across 19 ATSes (workday 27,440; join 25,003; workable 16,653; greenhouse 15,783; smartrecruiters 12,644; lever 10,023; zoho 8,193; recruitee 8,120; ashby 7,403; …).
- `scripts/discover/urlscan_miner.py` docstring — the 100-result cap and the date-slicing workaround (Zoho 117 → 434 hosts); 127 of 269 new Zoho Boards uniquely sourced.
