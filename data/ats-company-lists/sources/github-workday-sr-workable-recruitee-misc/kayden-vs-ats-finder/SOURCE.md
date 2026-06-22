# kayden-vs/ats-finder — ats_report.csv (India fintech company->ATS probe, strong Freshteam)

- **Source URL:** https://github.com/kayden-vs/ats-finder (file: ats_report.csv, branch main)
- **Raw:** https://raw.githubusercontent.com/kayden-vs/ats-finder/main/ats_report.csv
- **Author/repo:** kayden-vs
- **ATS providers:** freshteam 225, greenhouse 45, ashby 22, smartrecruiters 15, lever 11,
  bamboohr 5, personio 4, teamtailor 4, rippling 3, recruitee 3, workable 2.
- **Approx count:** 340 companies probed (335 status=found). THIS LANE: smartrecruiters 15,
  workable 2, recruitee 3; secondary: bamboohr 5, teamtailor 4, personio 4. Standout: Freshteam 225
  (a provider not covered by the other sources here).
- **Access method:** raw.githubusercontent.com (curl)
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Schema:** company_name,detected_ats,slug_used,jobs_found,careers_url,probe_time_ms,status.
  `slug_used` is the verified ATS slug; `careers_url` is the live board URL; `status=found` confirms liveness.
- **Description:** Live-probe report mapping (mostly India) companies to ATS + verified slug.
  Heavy India fintech/startup coverage (Razorpay, PhonePe, Paytm, CRED, Groww, Zerodha, Upstox,
  Juspay, Cashfree). Best in-repo source for Freshteam slugs.

## Files
- `ats_report.csv` — 340 rows (company_name, detected_ats, slug_used, jobs_found, careers_url, ...)
