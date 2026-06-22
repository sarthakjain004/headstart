# andreasasprou/claude-code-ats-finder — companies_with_ats.csv (company->ATS detection, 100+ ATS types)

- **Source URL:** https://github.com/andreasasprou/claude-code-ats-finder (file: companies_with_ats.csv, branch main)
- **Raw:** https://raw.githubusercontent.com/andreasasprou/claude-code-ats-finder/main/companies_with_ats.csv
- **Author/repo:** andreasasprou
- **ATS providers:** very long tail (~100 distinct). Top: Greenhouse 69, UNKNOWN 52, Workday 34,
  SmartRecruiters 23, iCIMS 14, Lever 14, Jobvite 10, Workable 10, UltiPro/UKG 13, SuccessFactors 9,
  Rippling 4, Zoho Recruit 4, JazzHR/Jazz 5, Breezy 3, Avature 2, BambooHR 2, Teamtailor 2,
  Ashby 3, Pinpoint 1, Taleo 1, Trakstar/Trakstar Hire 2, Eightfold 1, Cornerstone 1, Comeet 1, etc.
- **Approx count:** 396 companies (rows), each {domain, ats, confidence, url, explanation}.
  THIS LANE: Workday 34+1, SmartRecruiters 23+1, Workable 10, Recruitee 1; secondary:
  iCIMS 14, BambooHR 2, Teamtailor 2, Breezy 3, SuccessFactors 9, Taleo 1, Trakstar 2.
- **Access method:** raw.githubusercontent.com (curl)
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Schema:** domain,ats,confidence(high/low),url(verified careers/apply URL),explanation(LLM rationale).
- **Description:** LLM-driven company->ATS detector output. The `url` column carries the actual
  ATS apply URL (slug embedded) for high-confidence rows. Exceptional ATS-type breadth — covers
  many providers absent elsewhere (ADP, Paylocity, Paycom, BrassRing, NEOGOV, Dayforce, SilkRoad,
  Comeet) plus the project's TODO targets (Trakstar Hire, Oracle HCM, Eightfold). Verify slugs from `url`.

## Files
- `companies_with_ats.csv` — 396 rows (domain, ats, confidence, url, explanation)
