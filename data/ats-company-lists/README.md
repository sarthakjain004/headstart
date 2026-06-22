# ats-company-lists — harvested external lists of ATS companies

A staging area collecting **other people's published lists** of companies that use ATS job
boards (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee, Zoho Recruit,
Darwinbox, Keka, Trakstar, RippleHire, SenseHQ, BambooHR, Teamtailor, Personio, JazzHR, iCIMS,
Jobvite, SuccessFactors/SAP, Taleo, Oracle Cloud HCM, and any others).

This is a *discovery input*, harvested from GitHub repos, package registries, technographic
sites, public datasets, and community lists. It is distinct from:
- `data/ats-tenants-merged/active/*.csv` — our own liveness-validated active boards.
- `data/ats-companies/` — the (removed) jobhive copy.

Harvested lists can later be normalized and fed into `scripts/merge/merge_tenants.py`.

## Layout

```
sources/<lane>/<source-slug>/
    <saved list file(s)>        # the raw retrieved data (csv/json/txt)
    SOURCE.md                   # provenance: URL, author, ATS coverage, count, access, date, license
by-provider/<ats>.txt           # eventual normalized union per ATS provider (produced by a later merge)
MANIFEST.md                     # index of every source found, with coverage
```

## Integrity rule (applies to every contributor, human or agent)

Save **only** data actually retrieved from a real source, and record that source's exact URL in
its `SOURCE.md`. Never invent, guess, or pad company names/slugs. A promising source you cannot
fetch is recorded as a **LEAD** (URL + why), not as fabricated content.

## Canonical fields

Normalized target matches the pipeline schema: `ats, tenant (slug), url`. When a source gives
only company names or career URLs (no slug), keep what it has and note that resolution is needed.
