# Technographic ATS company lists — FOUND summary

Lane: technographic / "websites using X" vendors (non-GitHub). Harvested via WebSearch + WebFetch.
Date: 2026-06-23. Access: web. All counts below are companies actually shown on a fetched page.

## Vendors that worked (real named companies)
- **TheirStack** (`theirstack.com/en/technology/<ats>`) — public free view renders ~10 named
  companies (name + domain) per page + a vendor-stated grand total. Works for EVERY ATS tried,
  including India-niche ones. Region subpages exist (e.g. `/greenhouse/in`, `/trakstar/frit`)
  exposing different named samples.
- **Bloomberry** (`bloomberry.com/data/<ats>/`) — public free view renders ~20 named companies
  (name + domain) + a vendor-stated "detected" total (a smaller, recently-active subset than
  TheirStack's). Covers Western/major ATS only; 404s on Darwinbox & Zoho Recruit.

Names from the two vendors are largely DISJOINT, so they stack well for coverage.

## Saved sources (provider | ATS | URL | visible/saved | stated total)

### TheirStack
| ATS | URL | saved | stated total |
|---|---|---|---|
| Greenhouse | theirstack.com/en/technology/greenhouse (+/in) | 20 | 30,429 global / 105 India |
| Lever | theirstack.com/en/technology/lever | 10 | 11,602 |
| Ashby | theirstack.com/en/technology/ashby | 10 | 12,678 |
| Workday | theirstack.com/en/technology/workday | 10 | 70,268 |
| SmartRecruiters | theirstack.com/en/technology/smartrecruiters | 10 | 15,790 |
| Workable | theirstack.com/en/technology/workable | 10 | 36,950 |
| Recruitee | theirstack.com/en/technology/recruitee | 10 | 12,258 |
| BambooHR | theirstack.com/en/technology/bamboohr | 10 | 40,912 |
| Teamtailor | theirstack.com/en/technology/teamtailor | 10 | 9,317 |
| Personio | theirstack.com/en/technology/personio | 10 | 4,895 |
| JazzHR | theirstack.com/en/technology/jazzhr | 10 | 25,130 |
| iCIMS | theirstack.com/en/technology/icims | 10 | 24,417 |
| Jobvite | theirstack.com/en/technology/jobvite | 10 | 4,809 |
| Taleo (Oracle) | theirstack.com/en/technology/taleo | 10 | 3,826 |
| SAP SuccessFactors | theirstack.com/en/technology/sap-successfactors | 10 | 22,603 |
| Oracle Recruiting Cloud (Oracle Cloud HCM) | theirstack.com/en/technology/oracle-recruiting-cloud | 10 | 5,456 |
| Darwinbox | theirstack.com/en/technology/darwinbox | 10 | 1,072 |
| Keka | theirstack.com/en/technology/keka | 10 | 2,149 |
| Zoho Recruit | theirstack.com/en/technology/zoho-recruit | 10 | 4,577 |
| RippleHire | theirstack.com/en/technology/ripplehire | 10 | 26 |
| Recruiterbox = Trakstar Hire | theirstack.com/en/technology/recruiterbox | 10 | 74 |

### Bloomberry
| ATS | URL | saved | stated total |
|---|---|---|---|
| Greenhouse | bloomberry.com/data/greenhouse/ | 20 | 4,048 |
| Lever | bloomberry.com/data/lever/ | 20 | 1,615 |
| Ashby | bloomberry.com/data/ashby/ | 20 | 3,446 |
| Workable | bloomberry.com/data/workable/ | 20 | 788 |
| SmartRecruiters | bloomberry.com/data/smartrecruiters/ | 19 | 1,127 |
| Recruitee | bloomberry.com/data/recruitee/ | 20 | 1,077 |
| Teamtailor | bloomberry.com/data/teamtailor/ | 20 | 3,042 |
| BambooHR | bloomberry.com/data/bamboohr/ | 20 | 8,840 |
| iCIMS | bloomberry.com/data/icims/ | 19 | 2,570 |
| Personio | bloomberry.com/data/personio/ | 20 | 5,674 |
| Jobvite | bloomberry.com/data/jobvite/ | 10 | 570 |
| JazzHR | bloomberry.com/data/jazzhr/ | 20 | 4,509 |

## ATS coverage scorecard (real names obtained?)
Real named companies obtained for 21 distinct ATS providers:
Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee, BambooHR, Teamtailor,
Personio, JazzHR, iCIMS, Jobvite, Taleo, SAP SuccessFactors, Oracle Recruiting Cloud (Oracle
Cloud HCM), Darwinbox, Keka, Zoho Recruit, RippleHire, Trakstar Hire (Recruiterbox).

Of the original target ATS list, the only ones with NO real names from this lane:
- **SenseHQ** — TheirStack `/technology/sense` and Bloomberry `/data/sense/` both 404. No
  technographic page found. (Sense markets "1,000+ orgs; HCA Healthcare, Dell, Carvana" but
  that's a marketing snippet, not a fetched list — NOT saved.)

## LEADS (paywalled / fetch-blocked — recorded, no names saved)
- **enlyft-LEADS/** — Enlyft product pages return HTTP 403 to WebFetch (bot wall). Pages DO have
  named-company tables + market share. Stated totals seen in snippets: Greenhouse ~4,823,
  Lever ~2,329. Needs headless render.
- **builtwith-LEADS/** — BuiltWith `trends.builtwith.com/websitelist/<Tech>` returns only page
  chrome via WebFetch; the domain list is JS-rendered (empty). Large public lists exist. Needs
  headless render. (Best volume opportunity of all the leads.)
- **efficient-app-greenhouse-LEAD/** — stacks.efficient.app shows "Used by 0 companies" + only
  illustrative logos to guests ("guests can only see 3 teams"). Effectively paywalled. Low value.

## Notes / integrity
- Every `companies.txt` holds ONLY names rendered on the exact fetched URL recorded in its
  SOURCE.md. Nothing invented or padded. Blank domain = page showed the name but no domain.
- Totals are vendor-stated. TheirStack totals ≈ all-time detections; Bloomberry totals ≈ a
  smaller recently-active/job-posting subset — they legitimately differ for the same ATS.
- ~418 company entries saved across 33 data files (21 ATS × up to 2 vendors).

## Highest-value next actions (for orchestrator)
1. **Headless-render BuiltWith website lists** (greatest untapped volume; lists are large & public).
2. Paginate **TheirStack** for the small-total India ATS where a full pull is cheap:
   RippleHire (26), Recruiterbox/Trakstar (74) — and `/trakstar/frit` (7) — to capture the
   complete list, not just page 1.
3. Headless-render **Enlyft** pages for the named-customer tables (esp. Greenhouse/Lever).
4. Find a **SenseHQ** technographic source (none on TheirStack/Bloomberry) or fall back to a
   careers-page embed scan for `*.sensehq.com/careers` companies.
