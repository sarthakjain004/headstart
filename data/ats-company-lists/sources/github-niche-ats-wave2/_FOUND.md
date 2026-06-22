# GitHub niche-ATS code-search sweep — wave 2 (FOUND)

Date: 2026-06-23 · Method: `gh search code` + raw.githubusercontent.com / codeload tarballs +
local SQLite parse. Goal: maximize per-provider coverage for under-represented / HeadStart-TODO ATS
providers. All data below was actually retrieved and parsed; nothing invented.

## Sources captured (7)
| # | source | kind | headline |
|---|---|---|---|
| 1 | **Masterjx9/OpenPostings** | SQLite `companies` (61,610 rows) | ★ biggest: per-provider slug lists for 24 providers |
| 2 | **ever-jobs/ever-jobs** | 175-ATS adapter monorepo | endpoints + 1 verified tenant per provider; 622 Greenhouse slugs |
| 3 | **madhan0153/productcompanies** | curated India company→ATS map | ★ confirms dream11/zomato/lenskart/clevertap/moengage TODOs; new ATS "MyNextHire" |
| 4 | **mluggy/techmap** | daily Israeli tech CSVs | ★ 106 live Comeet slugs |
| 5 | **Kajaasfaq/Company-list-Chennai** | Chennai careers directory | 21 India-tier niche-ATS career URLs |
| 6 | **Tazril/career-scrap** | India company scrapers | 13 exact company→ATS:slug (non-derivable) |
| 7 | **BigChrisCooke/atlassian-job-board** | per-ATS config lists | 51 slugs (global IT consultancies) |
| (8)| **cboyd0319/JobSentinel** | fingerprinter ruleset | 28-provider URL→ATS patterns (NOT slugs) |

---

## PER-PROVIDER COVERAGE (HeadStart targets)

### HeadStart TODO providers (were missed / no fingerprint)
- **Trakstar Hire** — **726 slugs** (OpenPostings `slugs_trakstar.csv`) + happyfox (Chennai) +
  moengage (productcompanies). Host `{slug}.hire.trakstar.com`. ✅ BIG win — was 4 companies before.
- **SenseHQ / Skillate** — NO bulk list found. ever-jobs adapter (endpoint + tenant `sensehr`).
  LEAD: code search returns scrapers, not slug lists.
- **Oracle Cloud HCM** — **90 slugs** (OpenPostings `slugs_oracle.csv`), host
  `{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/...` incl. the `.fa.ocs.` variant
  (Icertis-class). ever-jobs adapter + JobSentinel pattern. ✅
- **Param.ai** — NONE found (only Practo known; Practo uses own-domain here). LEAD.
- **Kula** — clevertap → `careers.kula.ai/clevertap` (productcompanies). ever-jobs has no Kula. Only
  1 tenant; LEAD for more.
- **ainterviews / Recruitee white-label** — lenskart → `ainterviews.com/api/job_board/lenskart_ho`
  (productcompanies) — confirms the exact CLAUDE.md Lenskart TODO. ✅
- **PeopleStrong** — Chennai gives careers-bmwtechworks, matrimonycareers, careers-qualitykiosk
  (`{slug}.peoplestrong.com` + `careers-{x}` host variant — confirms "widen the pattern" note).
  ever-jobs adapter. (OpenPostings: 0.)
- **CareerSiteManager** — NONE found. LEAD.

### India-tier
- **Darwinbox** — latentview, mindsprint, msd (Chennai) + upgrad (Tazril/productcompanies). Host
  `{slug}.darwinbox.in/ms/candidate/careers`. ever-jobs adapter (tenant `dbox`). (OpenPostings: 0.)
- **Keka** — calibraint, growfin, zocket (Chennai) + frontrow (`.kekahire.com`), wingify (Tazril/
  productcompanies). Host `{slug}.keka.com/careers`. ever-jobs adapter. (OpenPostings: 0.)
- **Zoho Recruit** — **1,751 slugs** (OpenPostings `slugs_zohorecruit.csv`) + crayondata, squareshift,
  hdsupply(.in), staples(.in) (Chennai) + zomato→`eternal`(.in) (productcompanies). ✅
- **Freshteam** — **986 slugs** (OpenPostings) + 6 Chennai + spinny (Tazril). ✅
- **RippleHire** — usource (Chennai, `usource.ripplehire.com`). No bulk list. LEAD.
- **Manatal** — **193 slugs** (OpenPostings, `{slug}.careers-page.com`). ✅
- **Oorwin** — **83 slugs** (OpenPostings). ✅
- **greytHR / HROne / Zimyo / PyjamaHR / TurboHire / Zwayam / Snaphunt / Talentera** — ever-jobs
  adapters (endpoints + ~1 tenant each, in `constants/`). TurboHire also: flipkart, pine-labs
  (productcompanies). No bulk lists.
- **MyNextHire** (NEW, not on any HeadStart list) — sharechat, swiggy (productcompanies). Host
  `{slug}.mynexthire.com`. → RECOMMEND adding to HeadStart TODO.

### Mid-market
- **iCIMS 2,386** · **Rippling 1,378** · **Recruitee 2,733** · **Personio 1,691** · **Join.com 1,543**
  · **Ashby 1,440** · **Teamtailor 1,008** · **Jobvite 454** · **Pinpoint 416** · **Paylocity 294**
  · **Taleo 243** · **Comeet 184(OpenPostings)+106(techmap, live)** · **Avature 92** · **Eightfold 56**
  — all OpenPostings per-provider CSVs (`slugs_*.csv`). Also large bonus lists: **BambooHR 5,138**,
  **BreezyHR 4,316**, **JazzHR 3,463**, **SoftGarden 2,366**.
- **Phenom / Polymer / Ceipal** — ever-jobs adapters only (`constants/`); no slug lists found via
  code search (returned scraper templates). LEAD.
- **Cornerstone / SuccessFactors/SAP / Gem** — ever-jobs adapters; OpenPostings has gem(364, not
  sliced), no Cornerstone. Gem/SF/Cornerstone bulk: LEAD.

---

## DEAD-ENDS / LEADS (URL + reason)
- **SenseHQ, Param.ai, CareerSiteManager, RippleHire, Phenom, Polymer, Ceipal, Cornerstone, Gem
  bulk lists** — `gh search code` on each host returns individual scrapers / parser modules, not
  enumerated slug lists. No published list exists in indexed code (as of 2026-06-23).
- **Darwinbox / Keka / SenseHQ / PeopleStrong** — absent from OpenPostings DB (0 rows each); their
  slug data this wave is only the small India sources (Chennai, Tazril, productcompanies).
- `RISHABH72git/companies_in_software_engineering/companies_name_urls.csv` — large company→careers-URL
  list but mostly own-domain career pages (not ATS slugs); same genre as wave-1 awesome-career-pages.
  Not harvested. URL: https://github.com/RISHABH72git/companies_in_software_engineering
- `mcwitt/job-scraper/job_scraper/scraper/kula.py` — Kula RSC parser, takes URL at runtime, no slug
  list. URL: https://github.com/mcwitt/job-scraper
- `prince776/JAlert` — only 5 company handlers (Amazon/BharatPe/Google/Sharechat/Uber); too small.
- `OpenPostings.Postings` table — empty (no job rows shipped); only `companies` is populated.

## Notes for HeadStart pipeline
- OpenPostings `companies_full.csv` (61,610 rows, company\turl\tats) is a ready-made `resolve/`
  seed across ~80 ATS providers, global. Per-provider `slugs_*.csv` are pre-deduped.
- `cboyd0319-JobSentinel/artifacts/ats_detector.rs` = ready host→ATS rules for the fingerprinter
  TODO (Comeet+sparkhire, Phenom/phenompeople, Oracle Cloud, Eightfold, Jobylon, ZohoRecruit, …).
- `ever-jobs .../constants/*.constants.ts` document exact public job-feed endpoints + pagination for
  every HeadStart niche/TODO provider — the "how to scrape provider X" recipes.
- Non-derivable slugs confirmed: dream11→lever:`dreamsports`, zomato→zohorecruit:`eternal`,
  razorpay→greenhouse:`razorpaysoftwareprivatelimited`, lenskart→ainterviews:`lenskart_ho`.
