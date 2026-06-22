# _FOUND — community & "awesome" (non-GitHub) ATS company lists

Lane: community-maintained / publicly-published lists of companies-on-ATS, mined via
WebSearch + WebFetch (non-GitHub web). Captured 2026-06-23. Integrity: every name below
was present on a real fetched page; counts are of names actually captured, not vendor
claims. Gated/unreadable items are recorded as LEADs, not data.

## Sources captured (each = a subfolder with companies file + SOURCE.md)

| # | Source (folder) | URL | ATS provider(s) | Names captured | Slugs? |
|---|---|---|---|---|---|
| 1 | bloomberry-ats-directory | https://bloomberry.com/data/<ats>/ | Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee, Teamtailor, iCIMS, Jobvite, Trakstar | ~210 (≈20/ATS × 11) | GH/Lever/Ashby/Workday = yes (real slug); others = company website only |
| 2 | technologychecker-directory | https://technologychecker.io/technology/<ats> | Greenhouse, Lever, Workday, Workable, Personio | ~170 (free-preview rows) | career domain shown (noisy); name is the signal |
| 3 | ats-vendor-customer-pages | ashbyhq.com/customers, lever.co/customers, greenhouse.com/customer-stories, smartrecruiters.com/customers, teamtailor.com/.../customers, keka.com/customer-stories | Ashby, Lever, Greenhouse, SmartRecruiters, Teamtailor, Keka (+Darwinbox via search) | ~290 named customers (Ashby ~100, Keka ~73, TT ~36, SR ~36, Lever ~29, GH ~22) | slug = name-lowercased (verify) |
| 4 | remotive-startups-sheet | docs.google.com/.../1TLJSlNxCbwRNxy14Toe1PYwbCTY7h0CNHeer9J0VRzE (gviz CSV) | Greenhouse, Lever, Recruitee, Breezy, Workable | ~900 names; ~15 with confirmed ATS slug | subset yes (from URL column) |
| 5 | hn-whoishiring-june2026 | https://nchelluri.github.io/hnjobs/ | Ashby, Workable, Breezy, Pinpoint, HR-Manager | ~50 names; ~13 with ATS slug | yes for the 13 (real slugs) |
| 6 | apify-deadlyaccurate-ashby *(sub-agent)* | https://apify.com/deadlyaccurate/ashby-jobs-scraper | Ashby | 7 confirmed slugs + ~23 named | 7 verbatim slugs |
| 7 | apify-multi-ats-examples *(sub-agent)* | apify.com/{bovi,dami_studio,automation-lab,bikram07}/... | Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workday, Personio, Workable | ~14 deduped | yes (demo slugs: stripe, airbnb, figma, spotify, mistral, ramp, openai, notion, bunq, zego, personio, walmart, visa) |
| 8 | theirstack-free-preview *(sub-agent)* | theirstack.com/en/technology/{greenhouse,lever,ashby} | Greenhouse, Lever, Ashby | 30 (10/ATS preview) | names only |

Total distinct real company records captured across the lane: ~1,650 name rows
(with notable overlap of famous names across sources). High-confidence ATS-slug
pairs (directly usable): ~60+ (Ashby/Lever/Greenhouse/Workable/Breezy/Recruitee/
Pinpoint slugs from sources 1,4,5,6,7), plus ~210 GH/Lever/Ashby/Workday slugs from
bloomberry.

## Coverage by ATS provider (which sources name companies on it)
- **Greenhouse:** bloomberry(slug), technologychecker, vendor-pages, remotive(slug), apify, theirstack
- **Lever:** bloomberry, technologychecker, vendor-pages, remotive(slug), apify, theirstack
- **Ashby:** bloomberry(slug), vendor-pages(~100!), hn(slug), apify(slug), theirstack
- **Workday:** bloomberry(tenant), technologychecker(~50 enterprise), apify
- **SmartRecruiters:** bloomberry, vendor-pages(36), apify
- **Workable:** bloomberry, technologychecker(32), remotive(slug), hn(slug), apify
- **Recruitee:** bloomberry, remotive(slug: cargobase1), apify
- **Teamtailor:** bloomberry, vendor-pages(36)
- **iCIMS:** bloomberry(19)
- **Jobvite:** bloomberry(11)
- **Trakstar:** bloomberry(20)
- **Personio:** technologychecker(39), apify
- **Keka (India HRMS+careers):** vendor-pages(~73)
- **Breezy:** remotive(dataquest), hn(rhythmscience)
- **Pinpoint:** hn(ynab, safetywing)
- **Darwinbox (India HRMS):** named via search only (PwC, JSW, TVS, Hero FinCorp…) — HRMS, verify board exists
- BambooHR, Personio(more), Taleo, SuccessFactors, Oracle HCM, Zoho Recruit,
  RippleHire, SenseHQ, Skillate, Param.ai, Kula: NOT found as named community lists
  in this lane (see dead-ends).

## Best LEADS (gated/partial — worth revisiting with an account or JSON endpoint)
1. **bloomberry.com** — full per-ATS lists (4,048 GH / 3,446 Ashby / 2,846 Workday /
   2,570 iCIMS / 3,042 TT / 1,615 Lever … each WITH slugs for GH/Lever/Ashby/Workday)
   sit behind the JS pagination + gated app.bloomberry.com export. Highest-value lead:
   find the client JSON endpoint the page calls, or use the export. THE prize of this lane.
2. **technologychecker.io** — full datasets (2,399 Workday / 2,017 GH / 1,495 Workable /
   872 Lever / 679 Personio) downloadable after free sign-up.
3. **TheirStack** — 30,429 GH / 11,602 Lever / 12,678 Ashby; only 10 names/ATS free, rest paywalled.
4. **Remotive sheet N–Z URLs** — re-pull the gviz CSV with explicit column/gid so the
   careers-URL column for N–Z rows comes through (≈450 more rows to ATS-resolve).
5. **HN "Who is hiring" archive** — recurring monthly; each month's thread = fresh
   ATS-slug batch. Also hnhiring.com / hnhiring.me expose ATS-tagged apply links at scale.
6. **FeaturedCustomers** (greenhousesoftware 135 case studies / lever 86 / darwinbox 75 /
   smartrecruiters) — full reference lists gated; ~10 names/vendor free.
7. **efficient.app/apps/greenhouse, Landbase, appsruntheworld** (Darwinbox/Keka) — gated.

## Dead-ends (checked, no harvestable list in this lane)
- glever.co / hiddenjobs(.netlify/.vercel) / hidden-apply.vercel.app — JS shells, no
  static company directory (some URLs 404/403).
- jobboardsearch.com — 403 to fetch.
- Google Sheets `htmlview`/`/edit` — render empty without JS (must use gviz CSV; worked for Remotive).
- Threads ATS-ecosystem post — JS-only, no content via fetch.
- marcusdubois Substack — method article (Google site-search operators), names NO companies.
- enlyft.com — 403.
- Wikipedia — Greenhouse/Lever articles carry no customer list.
- Searches for community lists of BambooHR/Taleo/SuccessFactors/Oracle HCM/Zoho Recruit/
  RippleHire/SenseHQ/Skillate/Param.ai/Kula returned vendor marketing or gated
  technographic DBs only — no community-curated named list found in this lane.
