# Landing the Indeed ATS fingerprint sweep (2026-09-23, #576)

How the sweep's Boards reached the ledgers. The fingerprinter it ran is measured in
[2026-09-22_indeed-fingerprinting.md](2026-09-22_indeed-fingerprinting.md).

This covers the first landing from the company-level ATS fingerprint sweep over the Indeed harvest:
44,122 companies behind 679,687 postings. The sweep ran to a staged snapshot of its candidates,
taken at 19:46 while its render stage was still running, and #576 added **1,398 ledger rows** from
that snapshot across 22 ledgers. It changed no existing row. Those rows add 1,107 Unique Boards, of
which 1,091 are Hiring Boards. Anything the sweep finds after the snapshot lands later through the
same path.

## How a candidate became a row

- **Identity.** A candidate is new only if no ledger row, at any status and including the alias
  ledgers, has the same `board_key` once the row is read through its scraper's `slug_from(tenant,
  url)`. Matching on the raw tenant spelling instead would have called 247 of 254 known Workday
  Boards new. The Workday ledger keys a Board by a label plus a URL, while the sweep keys it by the
  URL.
- **Spelling.** Each new row is written in its ledger's majority `tenant`/`url` form, measured per
  ledger. This matters because `check_liveness.py` keys a ledger on the raw tenant, so a second
  spelling of a held Board lands as a second row.
- **Verdicts.** Every verdict comes from the ATS's own prober (`check_liveness.py --dir`, or
  `probe_icims.py`). No ledger was edited by hand.

## What was excluded, and why

| Class | Count | Evidence |
|---|---:|---|
| SuccessFactors host that is not an RMK site | 112 hosts | See "SuccessFactors look-alikes" below |
| URL-encoding junk | 139 candidate rows | `2fwww` was attributed to 60+ employers; `22app`/`22ext`/`22mcp`/`22tt` arrive together from one page's JS strings. `2f` + a real slug is decoded instead (16 rows) |
| SAP shared SuccessFactors hosts | 13 | `careerN.sapsf.*`, `careerN.successfactors.*`, `api17.sapsf.com` |
| Taleo UI paths read as career sections | 13 | `iam`, `theme`, `application.jss`, versioned resource paths |
| A held Board under another host | 8 | See "Duplicates the identity rule cannot see" below |
| Vendor or test host | 2 | `app.eightfold.ai`, an Oracle `-test-` pod |

### SuccessFactors look-alikes

`check_liveness.p_successfactors` calls a host live whenever `/sitemap.xml` is a `<urlset>`. Without
a further check, 36 corporate sites landed as `live,0`, including `www.rwe.com` (investor
relations), `www.nouryon.com` (news) and `jobs.porsche.com`. At least 12 Radancy career fronts also
landed as hiring: Halliburton, NetApp, G4S, TUI, Munich Re, Cargill and others.

On those fronts, the scraper reads 0 jobs, or it reads the site's own title as a job.
`careers.tuigroup.com` gave 4 "jobs" titled "Working at TUI | Jobs & Careers at TUI" with no
description. 6 of 6 tested hosts behaved this way; two genuine RMK hosts read cleanly.

`scripts/validate/confirm_successfactors_boards.py`, which tells RMK from look-alikes by the
`/job/{slug}/{id}/` URL shape, confirms all 4 Radancy fronts it was run on as `rmk`. It returns the
same junk counts the scraper produced.

What separates the two is RMK's own page assets. A `/job/` page taken from the host's sitemap
(urlset, RSS or sitemap index) carries `rmkcdn` or `j2w` on every genuine host tested, and on none
of the Radancy fronts. A bare "successfactors" string is no evidence, because Radancy's apply links
carry it too.

Two practical points about the check:

- **Negatives need a retry.** SAP's shared hosting throttles bursts, and a throttled fetch can
  answer 200 with a page that is not the sitemap, which reads like a sitemap with no jobs.
  `jobs.inglescareers.com` flipped this way under load, so a negative is believed only after a retry
  on the spare egress.
- **Result.** All 205 SuccessFactors rows that landed are Hiring Boards.

### Duplicates the identity rule cannot see

`board_key` compares spellings, not hosts. These candidates had unique keys but serve a Board
already held:

- **Oracle.** `iawmqy.fa-origin.us-phoenix-1…`, `iawmqy.vanity.fa…` and
  `fa-sufxtvfz.fa.us-phoenix-1…` sit in one CNAME chain with the held
  `iawmqy.fa.ocs.oraclecloud.com`. All four return `TotalJobsCount` 467 with the same first
  requisition ids.
- **SuccessFactors.** `www.jaguarlandrovercareers.com` is where two held rows 301 to.
  `jobs.eon-uk-careers.com` and `jobs.eon.se` list `jobs.eon.com` jobs. `jobs.esta.vic.gov.au`
  redirects to `jobs.triplezero.vic.gov.au`. `talents.brf.com` redirects to `talents.mbrf.com`,
  which is not held, so it landed as `talents.mbrf.com`.
- **Taleo Enterprise.** `textron.taleo.net/careersection/textron` (671 postings) holds all of the
  held `tabbu` section's postings. It is left out: replacing `tabbu` with it would change a ledger
  row, not add one.

## iCIMS and Jibe

The sweep saw 424 iCIMS hosts. 278 of them are vanity career sites, and every one runs on **Jibe**,
iCIMS's own career-site layer. A Jibe site's `GET /api/jobs` returns `apply_url`s on the employer's
`*.icims.com` tenant, and that is how 274 of the 278 were traced to their tenants.

- **Probing.** `probe_icims.py` over the new tenant hosts gave 2 live and 322 dead. The dead results
  are real 403s: 30 of 30 sampled serve `robots.txt` `Disallow: /`.
- **What landed.** 225 rows: the 2 live Boards plus 223 dead hyphenated tenant hosts.
- **Left out.** 99 single-word `{customer}.icims.com` hosts. 96 of the 99 redirect to the `icims2`
  recruiter-login servlet, and all 99 return 403 on the sitemap.
- **The finding.** Across the 271 iCIMS-backed Jibe sites, `/api/jobs` lists **145,555 open
  postings**. 99.7% of them sit on tenants that `Disallow: /`, where the sitemap-only iCIMS scraper
  cannot read them. The Jibe sites themselves serve `Allow: /` with `crawl-delay: 5` (3 of 3
  checked). A Jibe scraper would reach them, but it needs a decision on reading a front whose
  backing tenant opts out.

## Phenom

The gate is CLAUDE.md's rule: land only the skins whose backing Board we don't already hold. Of 227
candidates, 213 answer as Phenom.

- **Excluded as already held.** 143 candidates holding 133,150 postings, each measured live. 129 are
  Workday, matched by listing total plus 6 sampled apply links each. The rest were matched by exact
  id joins against Greenhouse, Ashby and iCIMS, or by title overlap through the repo's own
  SuccessFactors and Taleo code.
- **Landed.** 63 candidates, all live, 34,937 postings.
- **Collisions measured and passed:** 20 in all, listed below.

| Case | Boards |
|---|---|
| The held Board reads 0 today, so Phenom is the only live listing (the `careers.ucb.com` pattern). The postings would serve twice if the held listing recovers. | Workday: QBE (its row still says 274), MITRE, Genmab, thredUP. SuccessFactors: Givaudan and Pods (same host; the SuccessFactors prober counts Phenom's `/job/` URLs, so both count twice as Hiring Boards), Royal Mail, Battelle, Newmont, Lion. Other ATSes: Advocate Construction (Greenhouse), Jakson (Darwinbox), Newhomestar (Workable), Quest Global (RippleHire) |
| Measured as a different Board | Five Guys (0 of 1,403 titles on its Teamtailor Board), Mace (0 of 489 on Recruitee), Serco NA (0 of 447 on `apply-now.serco.com`) |
| The held row is dead | Howard Hughes (Workday) |
| A sibling Workday site | US Venture (reads `phenom_external_site`, 239, not in the ledger; the held `usvexternal` reads 0) |
| Small overlap, under the 5% bar | Spencer's (13 of 7,363 postings on a held iCIMS Board) |

- **Truncated reads.** Six landed Boards read under 99% of their own `totalHits`: fiveguys,
  sevitahealth, spencersandspiritjobs, vituity, werkenbijdirk and octapharmaplasma. ADR-0053
  therefore excludes them from eviction scope.

## Unsupported ATSes the sweep found

Companies resolved to an ATS HeadStart has no scraper for, counted at 20:51 on 2026-09-23 with the
render stage still running (these counts grow as it resolves more):

| ATS | Companies |
|---|---:|
| Breezy | 418 |
| ClearCompany | 348 |
| Pinpoint | 326 |
| ADP | 317 |
| Hireology | 159 |
| Cornerstone | 158 |
| Recruiterflow | 77 |
| Avature | 63 |

Breezy was built the same day (#579), after this snapshot was staged. Its 418 companies landed
separately: the sweep had recorded every one under Breezy's CDN host `assets-cdn`, so their tenants
were re-derived from the `{slug}.breezy.hr` apply URLs (405 labels; 6 companies carried only CDN or
`feed.breezy.hr` links and have none). 348 were already in `breezy.csv`; the other 57 were added,
all hiring.

SenseHQ has a registered scraper but no ledger and no liveness probe, so the 12 SenseHQ Boards the
sweep found cannot land.

## Follow-ups

- **Phenom tenants to redo:**
  - Unreachable from the machine that ran this: Healius, Manulife, Merck and Electrolux.
  - `jobs-au.pwc.com`: its postings sit under `/experiencedhires/`, a sub-path `PhenomScraper`
    doesn't read.
  - `jobs.ascension.org`: 0 postings at the root.
  - Royal Enfield and Cadila Pharma: backing ATS unresolved.
- **4 iCIMS candidates** found after the iCIMS resolution ran.
- **Textron:** replace `tabbu` with the `textron` umbrella section.
- **`p_successfactors` and `confirm_successfactors_boards.py` should both check RMK assets.** The
  existing SuccessFactors ledger holds 35 `live,0` rows that are probably the same false positive.
- **Rejected Phenom tenants are recorded nowhere committed,** so every sweep re-measures them.