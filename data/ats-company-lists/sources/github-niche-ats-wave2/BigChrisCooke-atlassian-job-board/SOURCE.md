# Source: BigChrisCooke/atlassian-job-board

- **URL:** https://github.com/BigChrisCooke/atlassian-job-board
- **Author/repo:** BigChrisCooke/atlassian-job-board
- **Retrieved:** 2026-06-23
- **Access:** public, repo tarball downloaded; per-ATS config files parsed
- **License:** none stated; reference use only
- **ATS providers:** Join.com, Personio, Teamtailor, Workable, BambooHR, Ashby (niche/mid-market
  targets) + Greenhouse, Lever, SmartRecruiters.

## What it is
An Atlassian-ecosystem job aggregator. `scripts/sources/config/<ats>-sources.ts` files are clean,
hand-curated **per-ATS slug lists** of companies in the Atlassian partner ecosystem (mostly EU /
global IT consultancies — Adaptavist, Devoteam, K15t, Eficode, cPrime, ServiceRocket, …). Scope is
narrow (Atlassian shops) but every slug is real and the lists are explicit `{ slug | companyId |
baseUrl, name }` records.

## Counts (51 slug rows total)
- smartrecruiters: 11 (companyId)
- teamtailor: 11 (baseUrl; subset are `{slug}.teamtailor.com`, rest custom domains)
- greenhouse: 8
- workable: 6
- personio: 5
- lever: 4
- join.com: 3
- ashby: 2
- bamboohr: 1

## Why it matters to HeadStart
Adds real slugs for **mid-market niche targets** Join.com (meskru, medialine, nangasystems),
Personio (k15t, automation-consultants-ltd, demicon, hiq, tngtech), Workable (nimaworks, praecipio,
cententia, mastek, ease-solutions-pte-ltd, grazitti-interactive), BambooHR (isostech), Ashby
(tempo-io, rewind) — all global, supporting the non-India mandate.

## Files saved (artifacts/)
- `ats_slugs.csv` — 51 ats,slug/companyId,baseUrl rows (all configs merged).
- `config/*-sources.ts` — the 9 verbatim per-ATS config files.

## NB
There are also 26 `scripts/sources/custom/*.ts` per-company scrapers (bespoke career-site domains,
not ATS-slug-addressable) — not extracted as slugs.

## One-line
Atlassian-ecosystem aggregator; 51 real slugs across Join/Personio/Teamtailor/Workable/BambooHR/
Ashby/Greenhouse/Lever/SmartRecruiters (global IT consultancies).
