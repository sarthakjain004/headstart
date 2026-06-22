# Source: vishal-yadav111/Job-Finder-main-app

- **Source URL (repo):** https://github.com/vishal-yadav111/Job-Finder-main-app
- **Raw dir:** https://raw.githubusercontent.com/vishal-yadav111/Job-Finder-main-app/3b699439aa9a02bf8e2ce02c07d8a0da9f513326/backend/app/config/providers/
- **Author:** GitHub: vishal-yadav111
- **ATS provider(s):** Greenhouse, Lever, Ashby, Workday
- **Access method:** `gh search code` discovery -> `curl` raw.githubusercontent.com (per-file probe)
- **Date retrieved:** 2026-06-23
- **License:** No LICENSE file in repo (unlicensed / all-rights-reserved by default). Data is
  publicly visible; treat as reference only.
- **Description:** Python config modules, one list per ATS, each a list of dicts. India-heavy
  company set (Zomato, Swiggy, Razorpay, CRED, BrowserStack, Postman, Freshworks, ...), which
  complements the US/EU-heavy Feashliaa and jd-intel sources.

## Files & shapes (raw `.py` saved as-is)

| File | ATS | Count | Shape |
|------|-----|-------|-------|
| `greenhouse_companies.py` | Greenhouse | ~30 | `{name, provider, token}` (token = slug) |
| `lever_companies.py` | Lever | ~30 | `{name, provider, token}` |
| `ashby_companies.py` | Ashby | ~30 | `{name, provider, token}` |
| `workday_companies.py` | Workday | ~25 | `{name, provider, base_url}` (full myworkdayjobs URL) |

**Total: ~115 entries across 4 ATS.**

## Notes
- Probed for `smartrecruiters_companies.py`, `recruitee_companies.py`, `workable_companies.py`,
  `teamtailor_companies.py`, `zoho_companies.py`, `icims_companies.py` — all HTTP 404. Only the
  four above exist.
- Workday entries carry the full `base_url` (e.g. `https://dell.wd1.myworkdayjobs.com/External`),
  so tenant/instance/site are recoverable.
