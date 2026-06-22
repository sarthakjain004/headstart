# Source: vishal-yadav111/Job-Finder-main-app

- **Source URL:** https://github.com/vishal-yadav111/Job-Finder-main-app/tree/main/backend/app/config/providers
- **Files pulled:**
  - https://raw.githubusercontent.com/vishal-yadav111/Job-Finder-main-app/main/backend/app/config/providers/greenhouse_companies.py
  - https://raw.githubusercontent.com/vishal-yadav111/Job-Finder-main-app/main/backend/app/config/providers/ashby_companies.py
  - https://raw.githubusercontent.com/vishal-yadav111/Job-Finder-main-app/main/backend/app/config/providers/lever_companies.py
- **Author/repo:** vishal-yadav111
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (+ a workday_companies.py not pulled).
- **Approx entry count (explicit `token` entries):**
  - Greenhouse: **30**
  - Ashby: **30**
  - Lever: **30**
  - (90 total)
- **How accessed:** direct `curl` of each `*_companies.py`; saved verbatim. Each file is a Python list of `{"name","provider","token"}` dicts.
- **Date accessed:** 2026-06-23
- **License:** None declared.
- **Description:** A hand-curated provider config with an explicit board `token` per company. Notably **India-heavy + global** (greenhouse: browserstack, postman, zepto, sharechat, freshworks, chargebee, hasura; lever: zomato, swiggy, razorpay-adjacent, cred, groww, meesho; ashby: razorpay, cred, paytm, pinelabs, zeta, n8n). Good complement to the more US/EU-centric sources.
- **CAVEAT:** Some tokens look like name-derivation guesses (e.g. lever `zomato`, `swiggy`, `meesho`) rather than confirmed live slugs — HeadStart's own notes flag that Zomato's real board is `smartrecruiters:Zomato1` and many India boards have non-derivable slugs. Treat these as candidate tokens needing liveness validation, not verified.

## Files saved
- `greenhouse_companies.py` (30 tokens)
- `ashby_companies.py` (30 tokens)
- `lever_companies.py` (30 tokens)
