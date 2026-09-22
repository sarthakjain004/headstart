# How HeadStart differs from LinkedIn on jobs

**2026-09-08.** The question behind the LinkedIn-scraper request: how much better or different is
HeadStart? This answers it without scraping LinkedIn, because the honest comparison does not need
LinkedIn's corpus — it needs the *upstream* one both products draw from.

## 1. The key structural fact: LinkedIn is downstream of us

LinkedIn's own [Job Posting API](https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview?view=li-lts-2026-03)
exists so **ATS vendors push their customers' jobs INTO LinkedIn**. LinkedIn describes itself as
"a passive conduit for the online distribution and publication of job listings." There is no
third-party read API, and new partnerships are closed.

So for any posting that originates in an ATS — which is most structured hiring — the flow is:

```
company ATS  ──push──>  LinkedIn        (LinkedIn's copy, wrapped, lagged)
     │
     └────── scrape ──>  HeadStart      (the source, canonical apply URL, ~62 min freshness)
```

HeadStart reads the source. That is the whole differentiation argument, and everything below is a
consequence of it.

## 2. Coverage measured against ATS market share, not against LinkedIn

Because LinkedIn is downstream, the *upstream* market share is the real ceiling — and it is
public. Two independent framings, both 2026:

**By employer adoption** ([ResumeGeni, 3,222 top-rated employers' career pages](https://resumegeni.com/research/ats-market-share-2026)):
Greenhouse 49.0%, Workday 21.8%, Lever 8.0%.

**By enterprise revenue** ([MarketsandMarkets](https://www.marketsandmarkets.com/ResearchInsight/applicant-tracking-system-market.asp)):
the top five — Workday, Oracle, SAP SuccessFactors, iCIMS, Greenhouse — are ~55–60% of enterprise
ATS revenue; enterprise postings split roughly Workday 32%, Greenhouse 18%, iCIMS ~10%.

**HeadStart scrapes all eight of those systems.** Live hiring boards per provider, from the
committed liveness ledgers (`data/validate/liveness/*.csv`, 2026-09-08):

| provider | hiring boards | market position |
|---|---|---|
| workday | 14,037 | #1 enterprise (32%) |
| greenhouse | 7,555 | #1 by employer adoption (49%) |
| smartrecruiters | 5,668 | top-10 vendor |
| zoho | 5,367 | SMB |
| workable | 4,233 | SMB |
| ashby | 3,893 | startup/scale-up |
| teamtailor | 3,822 | EU |
| jazzhr | 3,684 | SMB |
| recruitee | 3,559 | EU |
| icims | 3,061 | #3 enterprise (~10%) |
| personio | 2,597 | EU SMB |
| lever | 2,187 | 8% adoption |
| successfactors | 2,170 | top-5 enterprise |
| rippling | 2,107 | SMB |
| oracle | 991 | top-5 enterprise |
| freshteam, trakstar, keka, jobvite, darwinbox, zwayam, eightfold, ripplehire | 3,879 combined | long tail / India / GCC |
| **total (23 active providers)** | **68,810** | |

(`join` adds a further 18,802 hiring boards but is disabled — ADR/registry `DISABLED_ATS`, ~99.99%
non-tech.)

**The conclusion: ATS coverage is not the gap.** HeadStart already reads the systems behind the
large majority of structured job postings worldwide. What is incomplete is **tenant discovery
within each provider** — finding every company on Greenhouse, not adding Greenhouse. That is
exactly what CLAUDE.md's TODO queue and the `ats-gap-search` skill address.

Real provider-level gaps worth naming: **Bullhorn** (the dominant *staffing/agency* ATS, a
genuinely uncovered segment), **UKG**, **ADP**. Oracle Taleo is documented as a dead end — its
tenants migrated to Oracle Cloud HCM, which is covered.

## 3. Volume, stated honestly

LinkedIn: **~22 million open roles**, ~4 million new postings/month. That figure comes from
statistics-aggregator sites rather than a LinkedIn filing, so treat it as indicative, not audited.

HeadStart: **364,086 served rows** — but the two numbers are not like-for-like, and the difference
is deliberate, not a shortfall:

- LinkedIn's 22M is **all functions**; HeadStart indexes **tech roles only** (ADR-0017 recall-biased
  gate, ~20% keep rate of everything scraped).
- LinkedIn is **all languages**; HeadStart's index is **English-only** by design, with a
  language-detection gate before embedding.
- HeadStart's corpus before the tech gate is ~1.66M rows per run across those 68,810 boards.

Normalising roughly — tech is on the order of 10–15% of postings, English perhaps half — LinkedIn's
comparable slice is plausibly 1–2M against HeadStart's 364k. **That puts HeadStart somewhere near
20–35% of the addressable tech-English corpus. That range is an estimate built on an unaudited
denominator and two assumed ratios; it should be treated as a hypothesis to test, not a result.**

## 4. Where HeadStart is genuinely better

1. **Canonical apply URL.** HeadStart links to the company's own ATS. LinkedIn wraps it, and per
   `2026-09-08_open-source-scraper-landscape.md` that unwrapped URL is the single hardest field for
   any LinkedIn scraper to get — JobSpy's extraction of it is reported broken (issue #370). Direct
   apply means no LinkedIn account and no redirect chain.
2. **Ghost-job resistance.** This is the strongest one. HeadStart re-reads the *source* every ~62
   minutes and evicts against it, with ADR-0083's two-scrape grace period to avoid deleting on a
   transient miss. A posting closed in the ATS disappears. LinkedIn depends on the ATS pushing a
   closure and is widely criticised for stale listings.
3. **No login wall.** Most LinkedIn job detail requires an account.
4. **Semantic search.** HeadStart embeds `title + description` and does vector retrieval, with
   structured filters kept explicitly separate (CLAUDE.md's search-interface decision). LinkedIn's
   job search is keyword-plus-engagement ranking.
5. **No paid ranking distortion.** LinkedIn surfaces promoted jobs. HeadStart has no such incentive.
6. **Derived structured fields.** `min_years` (ADR-0018), period-normalised salary, remote,
   employment type — extracted uniformly across 23 providers. LinkedIn's equivalents are
   employer-supplied and inconsistent.

## 5. Where LinkedIn is genuinely better

1. **Raw volume**, per §3.
2. **Employers with no ATS at all** — small companies posting natively via Easy Apply. Structurally
   invisible to a source-scraping model. This is the real coverage gap and it is not closeable by
   adding ATS providers.
3. **Staffing and agency roles** — largely Bullhorn, uncovered.
4. **Non-English markets.**
5. **Network signal** — referrals, "you know someone here". Not a corpus question at all.

## 6. How to measure this properly, without scraping LinkedIn

The comparison in §3 rests on assumed ratios. A real measurement is available and needs no
LinkedIn access, because companies publish their postings as **schema.org `JobPosting` JSON-LD**
specifically so indexers can read them — Google for Jobs is built on it, and so is LinkedIn's own
ingestion of non-partner postings.

Proposed benchmark:

1. Sample N companies (stratified: enterprise / scale-up / SMB, and by region).
2. For each, fetch its careers page and parse the published `JobPosting` JSON-LD — an
   explicitly-published, machine-readable surface.
3. Count tech roles there; compare against what HeadStart serves for that company.
4. Report **recall per company** and, more usefully, **which failure mode** each miss is: no board
   discovered, board discovered but not scraped, scraped but tech-gated out, or genuinely absent.

That yields a defensible "we have X% of what this company actually published", which is the number
worth knowing — and unlike a LinkedIn diff, it isolates *our* failure modes rather than measuring
LinkedIn's syndication lag.
