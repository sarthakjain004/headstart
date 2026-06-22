# SOURCE: Bloomberry ATS customer directory

- **Source URL:** https://bloomberry.com/data/  (per-ATS pages: https://bloomberry.com/data/greenhouse/ , /lever/ , /ashby/ , /workday/ , /smartrecruiters/ , /workable/ , /recruitee/ , /teamtailor/ , /icims/ , /jobvite/ , /trakstar/). Vendor index: https://bloomberry.com/categories/ATS_and_recruiting/
- **Author / community:** Bloomberry (commercial sales-intelligence / technographics site). Not a community list per se, but a publicly readable web directory of companies-by-ATS.
- **ATS provider(s):** Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee, Teamtailor, iCIMS, Jobvite, Trakstar (32 ATS vendors total in the category, incl. ADP, JazzHR, Breezy, Comeet, Gupy, Paradox, etc.)
- **Approx count:** Provider-level totals Bloomberry CLAIMS to have detected — Greenhouse 4,048 / Ashby 3,446 / Teamtailor 3,042 / Workday 2,846 / iCIMS 2,570 / Lever 1,615 / SmartRecruiters 1,127 / Recruitee 1,077 / Workable 788 / Jobvite 570 / Trakstar 372. **Harvested here: ~210 real company names** (the first/most-recent ~20 per provider that the public page renders).
- **Access:** web, free, no login for the first page of each provider.
- **License / terms:** Proprietary commercial site; data shown publicly. No open license. Treat as a lead/seed source, not a redistributable dataset.
- **Description:** Per-ATS "companies using X" pages. Greenhouse/Lever/Ashby/Workday pages expose the actual ATS slug/tenant in the row link (directly usable by HeadStart); the other providers' pages link to the company's own website, so the slug must still be resolved.

## Limitation / LEAD
The public page is JS-paginated and renders only ~20 rows; `?page=`/`?per_page=` query params are NOT honoured over a plain server fetch, and the full list + CSV export sit behind the gated app at app.bloomberry.com. **LEAD:** the complete per-ATS lists (thousands each, with slugs for the GH/Lever/Ashby/Workday sets) are reachable only via that gated app/export or by hitting whatever JSON endpoint the page's client calls.

Saved file: companies.md (all ~210 captured names + slugs).
