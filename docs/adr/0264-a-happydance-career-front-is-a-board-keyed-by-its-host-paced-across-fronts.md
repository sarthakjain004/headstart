# ADR-0264: A Happydance career front is a Board keyed by its host, paced across fronts

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) (Radancy, the first career front), [ADR-0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) (parking a front over held Boards), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a short Board's eviction scope), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the enable bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

Happydance is Ph.Creative's career-site platform. A Happydance site is a **Career front**
(CONTEXT.md): it mirrors a Board on the company's real ATS and hands Apply off to it, or takes the
application itself. For Cognizant it is the only public listing: its 13 `cognizant.taleo.net`
sections probe `unknown` because Taleo's search redirects to `careers.cognizant.com`, and the
front's Apply goes back to Taleo or `talent.cognizant.com`. Nothing served Cognizant's 2,040
postings.

Measured live on 2026-09-28 (`docs/happydance/2026-09-28_sitemap-and-job-page-measurement.md`: 40
fronts found by CNAME, every sitemap, 736 job pages requested and 579 read from 34 fronts, a rate ramp):

- every front CNAMEs its host to `careers.happydance.website` or a `phcreative-*.azurefd.net`
  Front Door;
- `/sitemap.xml` names every posting once per locale, as `/[{prefix}/]{word}/{req}/{slug}/` with a
  localised word;
- every field is on the job page as JSON-LD, its type often written `application/ld&#x2B;json`;
- Cloudflare meters job pages across all fronts at once, and a tripped meter walls every front's
  pages for about an hour;
- 31 of the 34 fronts that list postings apply on, or mirror, a Board the repo already scrapes.

## Decision

**A front is a Board keyed under the vendor by its host** — `happydance:careers.cognizant.com` —
as Radancy's are. The req in the job URL is the native id; the first URL the sitemap lists per
req is the one read (an English locale on every front measured). A `<sitemapindex>` (the Next.js
template, 8 fronts) is read one level down, on the front's own host only.

**One JSON-LD job page per posting, no tech gate, no held-detail skip.** `job_posting_jsonld` now
reads the escaped type too, for every scraper. `industry` is the department; a Place carrying a
list of addresses (6 fronts) is read as one Place per address. A 200 page with neither JSON-LD
nor the `JobIdentifier` meta tag is a closed posting the sitemap still lists (17 of 108 Next.js
pages) and does not mark the Board short; a page with the tag and no JSON-LD is a loss. A gate on
the URL's title slug lost 30 of 117 tech postings (25.6%).

**Every request waits on one process-wide pacer at 2 requests/s, and a 429 moves the Board to the
spare egress.** 16 concurrent requests to one front drew the 429 challenge after ~225 pages; it
then held on every front's pages, including fronts never requested, for 60 minutes, while the
spare egress answered 200. 8 concurrent (15 req/s) ran clean; 2 req/s ran clean over 504 pages
of 29 fronts. The root sitemap was never walled.

**Front duplication is logged every run, as for Radancy** (`front_duplication`, now shared by
both scrapers; it also resolves Workday's `wdN.myworkdaysite.com/recruiting/{tenant}/{site}`
spelling). **A front whose Backing Board is held stays parked** — Phenom's rule, not Radancy's, by the
owner's decision of 2026-09-28: every sampled share below is at or near 100%, so landing them
would serve the same postings twice and add almost nothing. The 31 held-backed fronts are probed
into the ledger and parked in `PARKED_BOARDS` (the Jibe precedent, ADR-0189), so a front whose
Backing Board dies can be landed by deleting its entry. Three fronts land:
`careers.cognizant.com` (2,040 postings, Backing Boards unscrapable Taleo sections and an
unsupported host), `www.fidelitytalentsource.com` (79, Fidelity's staffing arm, which takes the
application in a Beamery form on the front; 3 of 24 sampled titles are also on Fidelity's
Workday, all a generic "Full Stack Engineer") and `jobs.sap.com` (4, applying on
`smartrecruiters:SAPITBusinessSysteme`, which no ledger holds).

**Held-backed fronts parked, with their measured duplication** (postings from the ledger; share
from sampled apply URLs unless noted):

| Front | Postings | Backing Board | Duplication |
|---|---:|---|---|
| careers.prismahealth.org | 2,190 | workday:prismahealth/prismahealthcorporate | 18/18 open pages |
| www.careers.jnj.com | 1,704 | workday:jj/jj | 3/3 |
| careers.richemont.com | 1,445 | workday:richemont/richemont | 15/15 |
| mycareer.verizon.com | 1,035 | workday:verizon/verizon-careers | 25/25 (no JSON-LD; unreadable) |
| careers.caterpillar.com | 938 | workday:cat/caterpillarcareers | 25/25 |
| careers.hilti.group | 827 | avature:hilticareers | 22/22; 811 of 815 job ids on its listing |
| jobs.fidelity.com | 764 | workday:fmr/fidelitycareers (735) | by employer; applies on the front |
| www.coupang.jobs | 702 | greenhouse:coupang | 702/702 job ids |
| careers.regeneron.com | 526 | workday:regeneron/careers | 21/21 |
| careers.ingrammicro.com | 504 | workday:ingrammicro/ingrammicro | 14/15 |
| careers.mgmresorts.com | 467 | workday:mgmresorts/mgmcareers | 19/19 |
| www.grab.careers | 446 | smartrecruiters:grab | 25/25 |
| careers.thrivent.com | 339 | workday:thrivent/external | 25/25 |
| jobs.centene.com | 291 | workday:centene/centene_external | 25/25 |
| www.careers.astemo.com | 282 | workday:astemo/global_career_site | 9/13 (4 on unsupported hrmos.co) |
| careers.equifax.com | 231 | workday:equifax/external | 24/25 |
| careers.aristocrat.com | 206 | workday:aristocrat | by employer (no JSON-LD; unreadable) |
| www.pinterestcareers.com | 150 | greenhouse:pinterest | 150/150 job ids |
| jobs.nationalgrid.com | 137 | successfactors:jobs-ats.nationalgrid.com | 11/11 (no JSON-LD; unreadable) |
| www.bairdcareers.com | 123 | workday:baird/careers | 25/25 |
| careers.box.com | 101 | greenhouse:boxinc | 101/101 job ids |
| careers.draftkings.com | 83 | workday:draftkings/draftkings | 25/25 |
| careers.criteo.com | 79 | workday:criteo/criteo_career_site | 14/15 |
| careers.mimecast.com | 79 | workday:mimecast/mimecast-careers | 15/15 |
| careers.warburtons.co.uk | 52 | successfactors:jobs.warburtons.co.uk (51) | by registrable domain; unmeasured |
| www.dropbox.jobs | 42 | greenhouse:dropbox | 42/42 job ids |
| careers.rjet.com | 33 | workday:rjet/external_career_site | 21/21 open pages |
| careers.coupa.com | 24 | lever:coupa | 8/8 |
| careers.fluttergroup.com | 16 | workday:flutterbe/group_external | 16/16 |
| retailcareers.paddypower.com | 13 | workday:flutterbe/ppretail_external | 13/13 |
| careers.flutteruki.com | 4 | workday:flutterbe/flutteruki_external | 4/4 |

**It lands active.** A full scrape of Cognizant through the scraper read 2,040 of 2,040 pages
with no loss, 1,486 of them tech (`is_tech(title, department)`); at 73,657 bytes a page that is
~0.1 MB per tech Job, far under ADR-0158's ~2 MB.

## Alternatives considered

- **Land every front and log duplication (Radancy's rule).** Rejected by the owner on
  2026-09-28: the measured shares are near 100%, unlike Radancy's partial overlap.
- **Leave held-backed fronts out of the pool (Phenom's rule).** Loses the re-probe that would show
  a Backing Board dying, as Hilti's Avature tenant once looked dead to a survey that missed its
  portal sitemaps.
- **Read the Next.js template's React payload.** Aristocrat, National Grid and Verizon write no
  JSON-LD; all three are held-backed, so it buys nothing today.
- **Pace per front.** The meter spans fronts, so a per-front pace multiplies by the fronts a shard
  reads at once.

## Consequences

- A scrape of Cognizant alone took 1,082 s at the pace.
- Six fronts list postings in shapes the scraper does not read (`/jobs/job/{slug}/` on
  `jobs.gartner.com`, `jobs.assurant.com`, `www.tipico-careers.com`; `careers.intuitive.com`,
  `jobs.uber.com`, `www.lloydsbankinggrouptalent.com`) and probe `unknown`.
- Discovery is a DNS sieve (`scripts/discover/mine_happydance.py`) over urlscan, Indeed and crt.sh
  names: 40 fronts against a vendor claim of 250+.
