# Resolving the opaque careers pages

**Measured 2026-09-07.** Sample: the 316 Indian companies that `scripts/resolve/fingerprint.py`
recorded as `-` on a 396-company seed (`data/scratch/fp_all.txt`). Tooling:
`scripts/discover/fingerprint_careers.py`. Results:
`data/discover/2026-09-07_india_opaque_careers_ats.csv`.

## The question, and why the first pass could not answer it

The first pass fetched `/` and `/careers` for each company and regexed the HTML against a table of
21 ATS host shapes. 80 of 396 resolved; 316 came back `-`. Read as coverage that says HeadStart
supports 69 of the 71 identified boards — excellent, over 18% of the list. **The number that
matters is in the other 82%**, and `-` there does not mean "no ATS". It means one signal saw
nothing.

Three structural reasons, confirmed live rather than assumed:

1. **The board is on a careers subdomain, not a path.** Eight of the twelve largest boards found
   here sit behind a `careers.` / `jobs.` host that CNAMEs straight at the provider. Nothing on
   `{domain}/careers` reveals that, and on several sites the path 200s with a marketing page — so
   the old scan saw a clean, successful, empty result.
2. **The careers page is an SPA.** `countrydelight.in/careers` returns byte-identical HTML to
   `countrydelight.in/` (74,034 B, an Angular shell with zero careers links). A regex over the
   document is looking at the wrong artefact.
3. **Nothing was looking for the ATSes we do not support.** The old table is 21 shapes, 19 of them
   ATSes with a scraper. A table of what you already have cannot answer a question about what is
   missing.

## What the new pass does

`fingerprint_careers.py` runs six signals per company, cheapest first, stopping at the first that
names a board. It is the *breadth* that pays, not any single signal.

| Signal | What it is | Resolved |
|---|---|---|
| `cname` | a careers-ish label CNAMEs into a provider zone — one UDP query, no HTTP | 10 |
| `redirect` | a careers host/path lands on an ATS URL after redirects | 1 |
| `page` | ATS host in a careers page's HTML (incl. a second-hop link) | 45 |
| `homepage` | ATS host in the apex page's HTML | 8 |
| `robots` / `sitemap` | the file names the ATS host | 0 |
| `jsbundle` | only the SPA's JS bundle names it | 1 |
| `slugprobe` | the board answers a derived slug **and declares this company's name** | 3 |

Two of these were added *after* a smoke test proved they mattered, and both changed the answer:

- **Second-hop link harvest.** Postman's careers landing page is marketing; the board is one click
  further at `/company/careers/open-positions/`. Without the hop, Postman reads as `none` while
  `boards-api.greenhouse.io/v1/boards/postman/jobs` serves 63 live jobs.
- **Slug probe.** Even with the hop, Postman's site names no ATS host anywhere — not in the HTML,
  not in any bundle. Only asking Greenhouse directly finds it.

## Headline: 68 of 316 resolved (21.5%), and 0 unreachable

| Status | n | % | What it means |
|---|---|---|---|
| `resolved` | 68 | 21.5% | an ATS board identified |
| `none` | 205 | 64.9% | pages fetched cleanly, no ATS reference found — a *settled* negative |
| `diy` | 32 | 10.1% | applications go to a form or an inbox, not a board |
| `jobboard` | 11 | 3.5% | the only apply route is an aggregator listing |
| `unreachable` | 0 | 0% | every probe failed — says nothing about the company |

**`none` and `unreachable` are separate columns on purpose.** Conflating a fetch failure with
"this company has no ATS" is how a headline number goes wrong, and this repo has been burned by
exactly that. Here the distinction happens to cost nothing — every one of the 316 sites was
reachable — but the split is recorded per row (`pages_ok` / `pages_err`) so the claim is auditable
rather than asserted. 176 of the 205 `none` rows are backed by 11+ successful fetches.

**The 21.5% is a lower bound, measured.** Running the identical sweep twice and diffing produced 3
companies resolved in one pass and `none` in the other (Capillary → sensehq, Blue Dart → phenom,
Doubtnut → trakstar) — all transient timeouts, all in the direction of under-reporting, ~1% of the
sample. The published CSV is the union of three passes for that reason; the script's own resume
(delete the non-`resolved` rows and re-run) is how to take another.

## ATS distribution

22 distinct ATSes across the 68 companies — 54 on ATSes HeadStart supports, **14 on ATSes it does
not**.

| ATS | n | Supported |
|---|---|---|
| darwinbox | 13 | yes |
| successfactors | 7 | yes |
| keka | 6 | yes |
| zwayam | 5 | yes |
| **turbohire** | **4** | **no** |
| ashby | 3 | yes |
| trakstar | 3 | yes |
| oracle | 3 | yes |
| greenhouse | 3 | yes |
| zoho | 3 | yes |
| **kula** | **3** | **no** |
| workday | 2 | yes |
| smartrecruiters | 2 | yes |
| sensehq | 2 | yes |
| **dover** | **2** | **no** |
| ripplehire | 1 | yes |
| freshteam | 1 | yes |
| **gem** | **1** | **no** |
| **adrenalin** | **1** | **no** |
| **pyjamahr** | **1** | **no** |
| **skillate** | **1** | **no** |
| **phenom** | **1** | **no** |

## The headline: unsupported ATSes, ranked

Eight providers, 14 companies. Every one was confirmed by hand against the live board, not taken
from the scan's own claim.

| Rank | ATS | Companies | Who | Confirmation |
|---|---|---|---|---|
| 1 | **turbohire** | 4 | Ola, Ola Electric, Cleartrip, Setu | `olacareers.turbohire.co`, `flipkart.turbohire.co`, `pinelabsgroup.turbohire.co` all HTTP 200 |
| 2 | **kula** | 3 | Acko, Cashfree, Rocketlane | `careers.kula.ai/{acko,cashfree,rocketlane}` — 264 KB / 300 KB / 675 KB of live listings |
| 3 | **dover** | 2 | Codingal, SALT | `app.dover.com` and `app.dover.io` embedded in each careers page |
| 4= | **gem** | 1 | Hasura (as PromptQL) | `jobs.gem.com/promptql` HTTP 200 |
| 4= | **adrenalin** | 1 | Intellect Design Arena | `cloud.myadrenalin.com` on `/careers/` |
| 4= | **pyjamahr** | 1 | smallcase | `app.pyjamahr.com` + a "powered by PyjamaHR" careers badge |
| 4= | **skillate** | 1 | Pristyn Care | `pristyncare.skillate.com` on `/company/careers/` |
| 4= | **phenom** | 1 | Blue Dart | four `*.phenompeople.com` hosts on `careers.dhl.com` (DHL's board) |

Read against the existing TODO in `CLAUDE.md`:

- **turbohire and pyjamahr are already on the "endpoint VERIFIED live" build list**, and turbohire
  is now the top-ranked provider gap on this sample — it was ranked M/"72 hosts" from host-mining;
  this is independent per-company evidence for the same call, and it names Ola and Cleartrip, which
  the plan already predicted it would unlock.
- **skillate (Pristyn Care) and kula (Rocketlane) are listed as single-company unlocks.** Kula is
  not: it is three companies here, all with substantial live boards, which moves it out of the
  "manual slug, not worth a scraper" tier.
- **dover and gem are new** — neither appears anywhere in the TODO. Both are small (2 and 1), but
  both are clean modern boards rather than login-walled HRMS.
- **adrenalin** was probed from Common Crawl in `scripts/discover/probe_ats.py`'s candidate list and
  never followed up; this is one confirmed live tenant.
- **phenom** is already ranked M with "poor discoverability, curated seed needed". Blue Dart is a
  curated seed entry, but note the board is DHL's global Phenom site, not a Blue Dart tenant.

Nothing here disturbs the documented dead-ends: no Taleo, iCIMS, greythr, qandle, HirePro,
iSmartRecruit, Ceipal or PeopleStrong tenant turned up in 316 companies.

## What the supported half says about existing gaps

The 54 supported-ATS companies are not a win — they are **boards HeadStart could scrape today and
is not**, because the fingerprinter could not name the tenant. Two clusters stand out:

- **darwinbox, 13 companies** — CarDekho, Licious, Emeritus, Pixxel, FarEye, Happiest Minds
  (`smileshrms`), Vymo (`vymopeopleconnect`), LEAD School (`myleadschool`), LatentView, Games24x7,
  Orange Health, Pepperfry (`trendsys`), CleverTap. This is the exact "top ROI, zero new scraper"
  entry in `CLAUDE.md`, and the list matches its curated 11 almost name for name — independent
  confirmation, from a different method, that the careers-page tenant scan is the missing piece.
- **successfactors, 7 companies, all via CNAME** — Wipro, HCLTech, L&T Technology Services,
  Birlasoft, Chargebee, Byju's, PayU. Every one CNAMEs a vanity careers host at
  `{n}.jobs2web.com`, SAP's own RMK infrastructure. **A `jobs2web` CNAME is a SuccessFactors
  tenant-discovery primitive** and does not appear in `docs/discovery/` today; it costs one UDP
  query and it named seven Indian IT majors in this sample alone.

## Two miss classes worth naming

**Zoho Recruit on a vanity domain.** `careers.yellow.ai` serves a full Zoho Recruit career site —
Zoho's `career-website-*` assets, the `<input type="hidden" ... id="jobs">` blob `ZohoScraper`
parses — with the string `zohorecruit` appearing **nowhere on the page**. Every host-shaped pattern
is blind to it. Fingerprinting the asset paths instead found two (Yellow.ai, Plum Goodness →
`careers.teampureplay.com`), and since zoho.py's slug is the careers host, both are scrapable
as-is. Note the asset match has to be on the *stylesheet* links at byte 4,484 — the equivalent
`<script>` tag sits at byte 1,045,676, past the page cap.

**A CNAME target is often provider infrastructure, not a tenant.** `careers.turtlemint.com` CNAMEs
to `cnameelb3.freshteam.com` (a shared load balancer), `careers.keka.com` to `cin02.hr.keka.com`
(the wildcard pod `mine_keka.py` documents as the *absence* of a tenant record). The script
rewrites these per ATS — for successfactors and zwayam, whose scrapers key on the company's own
careers hostname, the answer is the host we queried, never the target — but for freshteam,
adrenalin, dover, pyjamahr and phenom the ATS is right while the tenant token is provider infra
and still needs a manual slug.

## What remains unresolved, and why

**205 `none`.** Sampling and hand-checking says this is mostly genuine, and genuinely
self-hosted — not a detector failure:

- **Self-hosted job portals at the large end.** Infosys (`infosys.com/careers/`), TCS (iBegin),
  Zoho (`careers.zohocorp.com`) each run their own careers application. There is no third-party
  board to find.
- **SPA shells with no third-party board.** Country Delight's careers route is an Angular chunk;
  fetching *all 21* of its lazy chunks by hand found zero ATS hosts — its careers page talks to its
  own backend. The bounded 3-bundle scan is the right cost/benefit, and this is what it misses:
  nothing.
- **Small startups that never adopted an ATS.** This is what the 32 `diy` and 11 `jobboard` rows
  are: 15 `mailto:careers@`, 10 Google Forms, 3 Notion pages, 3 Typeforms, 1 Tally, plus 6 YC
  company pages and 4 LinkedIn-only.

Known blind spots, stated so the 21.5% is read correctly:

1. **Lazy-loaded route chunks.** The bundle scan takes at most 3 same-origin scripts and does not
   follow chunk manifests. A board named only inside a lazily-loaded careers chunk is missed.
2. **The slug probe is deliberately narrow.** It runs against only the four ATSes that publish the
   board owner's name (greenhouse, smartrecruiters, workable, recruitee) and requires an exact
   normalized name match. Measured on this sample, probing the identity-less boards as well
   (ashby, lever, freshteam, rippling) produced three extra hits — ashby `tilt`, `pulse`, `scribe`
   — and **all three were different companies** (a London bike retailer, an SF startup, a US SaaS
   firm), against zero true positives. Greenhouse alone returned two more namesakes: `tcs` is
   *Thornbury Community Services*, 108 live UK healthcare jobs; `pine` is a Canadian mortgage
   brokerage, not Pine Labs. The strict rule refuses real boards too (a company seeded as "Zetwerk
   Manufacturing" would not match a board named "Zetwerk"), which understates coverage rather than
   corrupting it — every refusal is written to the row's `note` with the name the board gave.
3. **Login-walled and JS-only boards** are out of reach of any HTTP-only fingerprint by
   construction.
4. **`robots.txt` and `sitemap.xml` resolved nothing** (0 of 68). Worth keeping — they are two
   requests and only run when everything else has failed — but they are not the signal this class
   of site leaks through.

## Combined picture over the full 396-company seed

| | Companies | Resolved |
|---|---|---|
| First pass (`fingerprint.py`) | 396 | 80 |
| This pass, over its 316 opaques | 316 | 68 |
| **Combined** | **396** | **148 (37.4%)** |

Companies on an ATS HeadStart does not support: 3 in the first pass (2 turbohire, 1 peoplestrong)
plus 14 here = **17 of 148 resolved boards, 11.5%**. The supported-ATS share of identified boards
therefore holds up under the wider sweep — the original read was directionally right. What it
understated is the *absolute* size of the gap: this pass alone found 54 more companies sitting on
ATSes HeadStart already scrapes, and could not name a tenant for any of them before today.

## Reproducing

```bash
python scripts/discover/fingerprint_careers.py scan  SEED.csv OUT.csv --workers 10
python scripts/discover/fingerprint_careers.py verify OUT.csv
```

`scan` resumes by domain, so a second pass over the misses is: delete the non-`resolved` rows from
`OUT.csv` and re-run the same command. `verify` re-derives each board URL and fetches it — the
clean-JSON boards by their real endpoint, darwinbox by the `alljobs` POST its scraper uses,
everything else by a plain GET with status and size reported.

Two verifier artefacts to read past: Keka's careers page is an SPA shell, so `jobwords=0` there
means nothing (checked separately against `careerportalinfo`, which named Open Financial, Eka.care
and Wingify correctly), and `jobs=0` on a real board (Zetwerk, Games24x7, LEAD School, CleverTap)
means the board is currently empty, not absent.
