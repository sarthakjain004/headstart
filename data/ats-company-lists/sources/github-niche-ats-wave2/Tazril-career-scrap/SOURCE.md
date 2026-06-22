# Source: Tazril/career-scrap

- **URL:** https://github.com/Tazril/career-scrap
- **Author/repo:** Tazril/career-scrap
- **Retrieved:** 2026-06-23
- **Access:** public, repo tarball downloaded; per-company `.py` scrapers parsed
- **License:** none stated; reference use only
- **ATS providers:** Darwinbox, Keka, Eightfold, Freshteam (niche targets) + Greenhouse, Lever, Workday.

## What it is
Python job-scraper with one module per company under `companies/` (30 files, India-focused:
razorpay, paytm, cred, groww, juspay, lenskart, phonepe, epifi, zeta, spinny, upgrad, nutanix, …).
Each module hardcodes the company's live ATS job-feed URL, so the company→ATS:slug mapping is exact.

## Why it matters to HeadStart
Confirms several **non-derivable slugs** (the miss-class called out in CLAUDE.md):
- razorpay → greenhouse `razorpaysoftwareprivatelimited` (the exact example in HeadStart's CLAUDE.md)
- upgrad → darwinbox `upgrad`; frontrow → keka `frontrow` (`frontrow.kekahire.com`)
- nutanix, paypal → eightfold; spinny → freshteam; cred/paytm/epifi/zeta → lever; coursera/phonepe → greenhouse

## Counts
- 30 company modules total; **13 with a clean, verified ATS:slug** extracted (rest use bespoke /
  Azure-table / unparsed feeds — e.g. nagarro uses an Azure Table backend, NOT smartrecruiters,
  so it was excluded to preserve integrity).

## Niche-target slugs (subset of the 13)
- **Darwinbox:** upgrad
- **Keka:** frontrow
- **Eightfold:** nutanix, paypal
- **Freshteam:** spinny

## Files saved (artifacts/)
- `company_ats_slug.csv` — 13 verified company,ats,slug rows.
- `README.md` — repo readme (context).

## One-line
India-company scrapers; 13 exact company→ATS:slug rows incl. non-derivable slugs
(razorpaysoftwareprivatelimited, upgrad/darwinbox, frontrow/keka, nutanix+paypal/eightfold).
