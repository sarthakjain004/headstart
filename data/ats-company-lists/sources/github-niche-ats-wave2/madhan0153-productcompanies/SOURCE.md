# Source: madhan0153/productcompanies  ★ CONFIRMS MULTIPLE CLAUDE.md TODOs

- **URL:** https://github.com/madhan0153/productcompanies
- **Author/repo:** madhan0153/productcompanies
- **Retrieved:** 2026-06-23
- **Access:** public, repo tarball downloaded; `packages/crawler/companies/*.ts` parsed
- **License:** none stated; reference use only
- **ATS providers:** Kula, Trakstar, TurboHire, Keka, MyNextHire, ainterviews/Recruitee-whitelabel,
  Zoho Recruit (niche/TODO) + Greenhouse, Lever, SmartRecruiters, Workday.

## What it is
A Next.js crawler with one hand-written module per company (51, India product-company focused).
Each module hardcodes how that company's board resolves, so it is a curated, high-accuracy
company→ATS:slug map for big Indian unicorns — precisely the **non-derivable-slug** class
HeadStart's CLAUDE.md calls out.

## Why it matters (direct CLAUDE.md hits)
- **dream11 → lever `dreamsports`**  (exact CLAUDE.md example)
- **zomato → zohorecruit `eternal`** (Zomato's legal entity "Eternal" — non-derivable; cf. CLAUDE.md
  `smartrecruiters:Zomato1` is now stale — this repo shows Zomato on Zoho Recruit `.in` as `eternal`)
- **lenskart → ainterviews/Recruitee whitelabel `lenskart_ho`** (exact CLAUDE.md Lenskart TODO:
  `ainterviews.com/api/job_board/lenskart_ho/jobs/`)
- **razorpay → greenhouse `razorpaysoftwareprivatelimited`** (exact CLAUDE.md example)
- **clevertap → Kula** `careers.kula.ai/clevertap` (Kula = HeadStart TODO; real tenant)
- **moengage → Trakstar** `moengage.hire.trakstar.com` (Trakstar = HeadStart TODO)
- **flipkart → TurboHire** `flipkart.turbohire.co`; pine-labs also TurboHire
- **wingify → Keka** `wingify.keka.com`

## NEW ATS provider discovered (not on any HeadStart list)
- **MyNextHire** (`{slug}.mynexthire.com`): **sharechat**, **swiggy**. Recommend adding to HeadStart's
  ATS-providers-to-add TODO. (NB: HeadStart's CLAUDE.md lists ShareChat under Trakstar Hire — this
  source shows ShareChat on MyNextHire; worth re-checking which is live.)

## Counts
- 51 company modules; **24 with a clean ATS:slug** extracted; 27 use own-domain/custom feeds
  (amazon, apple, google, meta, microsoft, paytm, ola, practo, zerodha, …).

## Files saved (artifacts/)
- `company_ats_slug.csv` — all 51 rows (company,ats,slug; blank slug = custom/own-domain).
- `evidence/*.ts` — 9 verbatim company modules for the CLAUDE.md-relevant + MyNextHire mappings.

## One-line
Curated India-unicorn company→ATS:slug map; confirms dream11→lever:dreamsports,
zomato→zohorecruit:eternal, lenskart→ainterviews:lenskart_ho, clevertap→Kula, moengage→Trakstar,
and surfaces a NEW ATS "MyNextHire" (sharechat, swiggy).
