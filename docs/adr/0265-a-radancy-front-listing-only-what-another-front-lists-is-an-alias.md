# ADR-0265: A Radancy front listing only what another front lists is an alias

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) (Radancy fronts, and the owner's no-gate decision on Front duplication), [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (the `subset-reqs` election)

## Context

CLAUDE.md's Radancy rule holds canonical front hosts only. Language and country twins of one
front probe `live` and were landed as separate Boards. On 2026-09-28 they listed the same
TalentBrew job ids, so each posting was served once per twin:

| twin | lists | of the front it copies |
| --- | ---: | --- |
| `jobs.jabil.cn` | 1,954 | all 1,954 of `jobs.jabil.com`'s |
| `empleos.greystar.com` | 500 | all 500 of `jobs.greystar.com`'s |
| `jobs.mt.com.cn` | 531 | all 531 of `jobs.mt.com`'s |

`jobs.carnival.com`'s 91 all sit inside `jobs.carnivalcorp.com`'s 232. This is not the Front
duplication the owner allowed on 2026-09-26, which is a front over a Backing Board on another ATS.
Here both copies are Radancy.

## Decision

**A front whose every sitemap job id another live front also lists is buried onto it**, in
`data/validate/aliases/radancy.csv` with signal `subset-reqs`.

- TalentBrew job ids are platform-wide, so every front is compared with every other through
  `alias_ledger.bury_contained`.
- Of two mirrors, the English `.com` front that the employer's own site links to is kept
  (`PREFERRED_FRONTS`), not the lowest host, and it is never buried onto its translated twin: a
  twin that lists one posting more would otherwise take its place.
- The writer is `scripts/validate/radancy_subset_fronts.py`. Re-run it after every refresh of the
  Radancy ledger. It re-reads every front, so a twin that starts listing its own postings comes
  back.

Two fronts that overlap without either containing the other both stay: `careers.astrazeneca.com`
and `careers.alexion.com` share 73 postings.

## Evidence

Re-run on 2026-09-28 after the ledger gained 32 fronts (#772): 201 fronts read, 5 buried —
`jobs.jabil.cn`, `empleos.greystar.com`, `jobs.carnival.com` (91 inside `jobs.carnivalcorp.com`'s
236), `emplois.disneycareers.com` (500 inside `www.disneycareers.com`'s 753; hours earlier it listed
2 of its own, so a twin comes and goes with the day's postings) and `empleos.allianceautomotive.es`
(1, onto `jobs.genpt.com`). `jobs.mt.com.cn` listed one posting `jobs.mt.com` did not (533 against
532), so neither is buried that run, and their 532 shared postings are served twice until the next
re-run. Rakuten's two hosts share no ids, so they are left alone.
