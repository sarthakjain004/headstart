# ADR-0246: A Radancy career front is a Board keyed by its host, scraped in full

**Status:** accepted · **Date:** 2026-09-26 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a short Board's eviction scope), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the enable bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate), [ADR-0201](0201-a-scraper-states-its-detail-request-once-and-the-base-runs-the-pass.md) (the Detail pass seam), [ADR-0245](0245-an-avature-board-is-its-tenant-host-read-through-its-portal-sitemaps.md) (Avature, a Backing ATS)

## Context

**Radancy is not an ATS.** It is a recruitment-marketing vendor, and TalentBrew is its career-site
CMS. A TalentBrew site is a **Career front** (CONTEXT.md): a branded job site mirroring one or more Boards on the
company's real ATS, whose Apply button hands off to that ATS. It stores no applications. For some
companies it is the only public listing — `jobs.intuit.com` mirrors an Avature tenant that lists
nothing itself — and until now the repo met these fronts only from the other side, as sites that
probe `live` as fake SuccessFactors Boards (CLAUDE.md's SuccessFactors landing rule).

Measured live on 2026-09-26 (`docs/radancy/2026-09-26_sitemap-and-job-page-measurement.md`: 1,078
candidate hosts, the 188 live fronts' sitemaps against their stated totals, 7,141 job pages from
185 fronts, and a rate ramp to 128 concurrent requests):

- every front CNAMEs its own host into `talentbrew.com`, and a vanity or country host redirects to
  a canonical front (36 of 224 hosts listing jobs);
- `/sitemap.xml` names every job URL, but is capped at 500 on 12 fronts and 10,000 on one, and
  `robots.txt` disallows the paginated results endpoint (`/search-jobs/`) on 151 of 188;
- every field lives on the job page, as JSON-LD on 177 of 185 fronts and as TalentBrew's own meta
  tags on the other 8;
- 62.9% of sampled postings (58.3% weighted by Board size) apply on a Board the repo already
  scrapes.

## Decision

**A front is a Board keyed under the vendor by its host** — `radancy:jobs.intuit.com` — exactly as
Phenom, the other career-front vendor, is. A front carrying several TalentBrew company ids (23 of
227) is still one Board: the applicant sees one site, and job ids are TalentBrew-wide, so the
native id is the job id alone. Only job URLs on the Board's own host count, so an alias host reads
none of its own and probes DEAD; the pool holds canonical hosts.

**The listing is the sitemap; a capped one is marked short.** A job URL is recognised by its shape
(`[/{lang}]/{word}/{place}/{title}/{companyId}/{jobId}`), since `{word}` is localised (`emploi`,
`banen`, CJK). One GET of the front's `/search-jobs` page — not the `/search-jobs/` endpoint
robots.txt names — reads its stated total, and a sitemap short of it marks the Board truncated
unless negligible (ADR-0121).

**One job page per posting, no tech gate, no held-detail skip.** The page's JSON-LD `JobPosting`,
else `gtm_tbcn_*` meta tags and the `ats-description` block; department from
`gtm_tbcn_jobcategory`; every place kept, "; "-joined; period of an amount-bearing `baseSalary`
from magnitude. A gate on the URL's title slug — the only pre-page signal — dropped 51 of 337 tech
postings (15.1%), all vague titles promoted by a technical category it cannot see. The held-detail
skip would blank every other field, since all of them come from the page.

**Every front is scraped in full, and Front duplication is measured every run, not gated** — the
owner's decision of 2026-09-26, the opposite of Phenom's landing rule on purpose. Each posting's
apply URL is resolved to a Scrapable Board through that ATS's own `slug_from` (Workday, Taleo
Enterprise, SmartRecruiters, Greenhouse, Lever by URL shape; host-keyed Boards — iCIMS,
SuccessFactors RMK, Eightfold, Phenom, Oracle — by host; Avature by tenant label), and one INFO line per Board
states `Front duplication k/n postings apply on a Scrapable Board`, with `front_duplicated` and
`front_postings` in the Board's telemetry. The decision is to be revisited from those numbers. A
front and its Backing Board are **not** joined in `company_directory`: both copies are served, so
a joined entry would count twice.

**Radancy's QA hosts and employee-only fronts are not landed.** `*.runmytests.com` and
`*.runmytests.eu` mirror real fronts (Barclays' 802 postings) and are dropped from the pool. Five
internal fronts (`internal.commonspirit.careers` and four more) are live but parked in
`excluded_and_parked.PARKED_BOARDS`: 40–88% of their postings are the public front's under other
job ids, and the rest cannot be applied to from outside.

**It lands active.** ~170 KB fetched per posting at a 14.1% tech share is ~1.2 MB per tech Job,
under ADR-0158's ~2 MB bar (within 2x of it).

## Alternatives considered

- **A Board per TalentBrew company id.** Rejected: the id is not what a user sees, a front's ids
  share one sitemap and one job-id space, and splitting them would mint Boards no ledger row names.
- **Gate the front against its Backing Board, as Phenom does.** The owner declined it for now:
  Front duplication is logged instead, so the cost of serving both copies is known before it is
  cut.
- **Read `/search-jobs/results` past a sitemap cap.** It is the complete surface, and 7 of the 12
  capped fronts do not disallow it; left as a follow-up rather than a per-front robots branch in
  the first build. The caps hide ~29,700 stated postings today.
- **A pre-page tech gate on the URL slug.** Rejected at 15.1% recall loss, past Avature's accepted
  9.6% (ADR-0245); the tech filter is recall-biased by design.

## Consequences

- 13 capped fronts are out of eviction scope every run (ADR-0053's undrained scope exclusion), so
  their closed postings can linger until the results-endpoint follow-up lands.
- Front duplication is served: ~58% of Radancy's postings have a second copy under their Backing
  Board's key. The per-Board log line is the measurement the owner revisits.
- Four SuccessFactors ledger rows are the same hosts as Radancy fronts (`careers.alexion.com`,
  `careers.chevron.com`, `careers.moodys.com`, `jobs.stemcell.com`), which the SuccessFactors
  scraper misreads (6 of 6 such fronts,
  `docs/discovery/2026-09-23_indeed-sweep-landing.md`). Left for the SuccessFactors ledger's owner
  rather than edited here.
- Discovery has no vendor namespace: `scripts/discover/mine_radancy.py` reads urlscan.io's scans
  that loaded `tbcdn.talentbrew.com`, and a DNS sieve for `*.talentbrew.com` CNAMEs over derived
  careers hosts found the rest.
