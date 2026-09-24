# ADR-0186: A Taleo Enterprise section that another section already lists is an alias

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (a Board off the scrape list is evicted by `prune`), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the alias ledger), [ADR-0177](0177-an-unknown-reprobe-keeps-a-live-verdict.md) (the ledger is refreshed by hand), [ADR-0182](0182-a-clearcompany-board-is-its-hrm-direct-feed.md) (`shared-reqs`, the precedent followed here), [ADR-0183](0183-a-cornerstone-board-is-the-tenant-read-across-every-career-site.md) (Cornerstone took the other road)

## Context

A Taleo Enterprise Board is one career section, `{zone}.taleo.net/careersection/{section}`. A
tenant (the section URL's host) runs several sections, and each lists some or all of the tenant's
requisitions under one tenant-wide req id. `index_plan.evict_duplicate` groups only within a Board,
so a req listed on 15 sections is served 15 times. Served index v654 (2026-09-23) held **22,929**
taleo_enterprise rows for **2,475** distinct (tenant, req) pairs: **20,454 duplicate rows, 89%**.
The redirect scan cannot see it (`docs/taleo_enterprise/2026-09-13_redirect-dedupe-audit.md`
found 0 clusters): no section redirects to another.

Measured before deciding (2026-09-23/24):

- **The req id is tenant-wide.** Titles agree on 1,443 of 1,443 reqs listed on more than one
  section; descriptions on 1,311 (129 differ).
- **A job URL only works on a section that lists the req.** `jobdetail.ftl?job={id}` on a section
  that does not list it answers 200 with no posting (13 of 13, 5 tenants). So a req must be served
  under a section that lists it.
- **Sections cannot be enumerated from the tenant.** `robots.txt` and `sitemap.xml` answer 404, and
  `/careersection/sitemap.jss` lists only the SEO sections (HDR 3 of 15, easyJet 2 of 28). The
  set of sections is whatever the liveness ledger holds.
- **The shapes vary.** Of the 43 tenants with more than one section holding served rows: 22
  mirrors, 11 with one superset section, 4 partial overlaps, 6 partitions.

## Decision

**A section is an alias when its full live requisition set is non-empty and contained in the set
of another section of the same tenant, both on live ledger rows.** It is buried in the ADR-0111
alias ledger, `data/validate/aliases/taleo_enterprise.csv`, with signal `subset-reqs`, onto a
maximal section,
which lists every req the buried one does, so every served job URL still resolves. Kept
sections' `board_key`s do not change. `load_active_companies` drops buried sections, and `prune`
evicts their rows through the existing off-Board path; no new eviction machinery.

- **Containment is on the full listing**, every role, not the tech subset. The listing is the one
  the scraper reads (`TaleoEnterpriseScraper._listing`), so everything a scrape would read from a
  buried section, it reads from the kept one.
- **Chains collapse to the top:** A ⊂ B ⊂ C buries A and B onto C.
- **Mirrors keep the lowest section URL.** The same sets always elect the same section, whatever
  order they are read in.
- **A section under several maximal sections** goes to the largest, then the lowest URL.
- **Two maximal sections that overlap both stay.** Burying either would hide the reqs only it
  lists. Their overlap is served twice; see Consequences.
- **An empty section is never buried.** The empty set is a subset of everything, so it is no
  evidence.
- **An unreadable section is neither buried nor kept for anything.**

**The writer is `scripts/validate/taleo_enterprise_subset_sections.py`, outside
`board_aliases.py`**, as ClearCompany's `shared-reqs` writer is (ADR-0182). `board_aliases.py`
only loads the result. `dedupe_boards.py --apply` now refuses this ATS, because it would rewrite
the file from redirects and erase every row. The script reads the listing of each section on a
live row, 16 sections at a time, and replaces the file.

**It runs by hand, after every refresh of `data/validate/liveness/taleo_enterprise.csv`.** The
user decided this on 2026-09-24. It is the same footing as ClearCompany's writer and as the
ledger itself: only the offline prober writes the ledger (ADR-0177), and no workflow runs any
`scripts/validate/` writer. CLAUDE.md's landing rules carry the step. Each run re-derives every
verdict. It re-reads the sections the last run buried (the prober leaves their liveness rows in
place, ADR-0111), so a buried section that has started listing a req of its own is un-buried by
the next run.

**The gap:** a burial is bounded by how often the ledger is refreshed, not by wall-clock time.
Nothing scrapes a buried section, so a req that only it lists stays hidden until someone re-runs
the writer. The same holds for a whole group if its kept section goes: a refresh that marks the
kept section `dead` drops it from the scrape list, `load_active_companies` still drops the
sections buried onto it, and the prober leaves their rows untouched, so the group's reqs leave
the index until the writer runs and elects a new kept section from them. That is why the landing
rule says to re-run after *every* refresh. A newly opened section is already bounded the same
way: nothing finds it until the next ledger refresh.

`pmg.taleo.net`, Oracle's own demo tenant ("Director of Finance (DEMO)", "TEST 2 EPredix
Assessment", read 2026-09-24), is added to `config.EXCLUDED_BOARDS`, all four of its sections.

## Evidence

Two full runs against the committed ledger on 2026-09-24, 3.5 h apart, over the 559 sections its
live rows name (561 rows less pmg's two):

| | read | unreadable | buried | kept targets |
| --- | ---: | ---: | ---: | ---: |
| 04:45 UTC | 530 | 29 | 306 | 43 |
| 08:15 UTC | 557 | 2 | 318 | 50 |

- **306 of 306 first-run burials held, onto the same kept section.** None were un-buried. The first
  run's ledger, applied to the second run's listings, hides **0** reqs.
- **11 of the 12 new burials are sections the first run could not read.** Their shell served no
  `portalNo` at 04:45 and did at 08:15 (edmonton, agnicoeagle, chicagotransit, drhorton,
  followmont), so "no portalNo" is intermittent, not a fixed property of those tenants.
- **The twelfth is Daimler, and it shows the gap.** At 04:45 `ex` listed 25 reqs no other Daimler
  section did (2 tech by `tech_filter.is_tech`). By 08:15 all four sections listed the same 446, so
  `ex` was buried onto `dnac`. Every one of those 25 was on `dnac` by then: this was propagation lag
  between sections, not divergence. Had the second run's ledger been in force at 04:45, those 25
  would have been hidden for those hours.

**A walk reads the same set every time, even when it is short of the stated total.** 390 of the
557 sections read fewer unique ids than Taleo's own `totalCount` (the 340 short multi-page ones
read 89.1% of it in total). Pages come back under their 25-row size with no id repeated (Daimler
`ex`: 446 over 20 pages against a stated 499), so the gap is rows Taleo counts but never lists,
not pages lost. Twelve short, buried sections on twelve tenants were each read twice back to back:
12 of 12 returned identical sets, among them `hdr/highway_bridges` (2,280 of 2,283),
`hyatt/clearwater_internal` (3,230 of 3,333) and `aa308/ex_busop` (254 of 666). Twelve re-reads
are evidence, not proof, but none showed a walk that was short by chance. A completeness check
against `totalCount` would refuse 243 of the 318 burials for a count the listing never meets, so
none is built.

Projected onto served v654 (opened read-only), using the second run:

| | this writer (live full listings) | the served sets |
| --- | ---: | ---: |
| sections buried | 318 | 221 |
| served rows evicted | 20,176 | 20,338 |
| duplicate rows left, of 20,454 | 278 | 116 |
| served (tenant, req) pairs left with no row | 0 | 0 |

**No tech req is lost, not even until the next scrape.** Each of the 2,475 served pairs keeps a
row on a kept section. pmg's exclusion evicts its 12 served rows as well.

The right-hand column re-derives the pre-decision projection by running the same rules over the
served sets, and matches it exactly. The live run buries 114 sections the served sets do not; 111
of them hold no served row at all. It keeps 17 that the served sets bury, and so leaves 162 more
duplicate rows. On the full listing, 10 of those 17 list reqs no other section of their tenant
lists (tenet `10121` 44, molgroup `internal` 9, BAE `cssiksa_internal` 6), 6 are covered only by
a union of sections and not by any one, and 1 was unreadable. The served tech-only sets cannot see
the first 10 reqs, which is why containment is computed on the full listing.

## Alternatives considered

- **The tenant as the Board**, as Cornerstone does (ADR-0183). Rejected by the user in favour of
  this. The tenant's sections cannot be enumerated, so a tenant Board would still be a union of
  whatever the ledger holds. It would also re-key every Taleo Enterprise row, and each req's URL
  must still name a section that lists it.
- **An index-side rule only** (dedupe a tenant's sections inside `index_plan`). Rejected by the user.
  Every section would still be scraped, and the rule would sit in the served-index path.
- **Containment on the served (tech) sets.** Reproduces the 221 above, but it sees only sections a
  scrape has indexed and only their tech subset. That is how it buries 10 sections that list reqs
  of their own.
- **Expire a burial on read after N days**, to bound it in wall-clock time. Rejected:
  `load_active_companies` would then depend on today's date, so **Scrapable Board** would move
  without a commit and `tests/test_board_counts.py` would fail by itself. A lapsed burial would
  also re-serve ~20k duplicate rows until someone re-ran the writer.
- **Run the writer in the pipeline.** This would give a real bound, but it is a pipeline change,
  and the user chose the manual footing.

## Consequences

- **Scrapable Board** 130,310 → 129,990 (318 sections aliased, 2 pmg sections excluded); **Hiring
  Board** 84,865 → 84,546.
- **274 duplicate rows stay served** (the table's 278 less pmg's 4, which its exclusion evicts),
  where maximal sections overlap: Hyatt 109 (`10780` and `10880` list only reqs other sections
  also list, but no single section lists all of either), BAE 83, dasstateoh 34, molgroup 15,
  edmonton 11, and 22 more across eight tenants. Reaching them needs a per-req rule rather than a
  per-section one.
- **A failed read un-buries**: its section is not buried, and nothing is buried onto it. That
  serves duplicates until the next run and never hides a req. If the unreadable section was a kept
  mirror, the rest of its mirrors elect the next-lowest URL for that run and move back on the next
  clean one, which evicts and re-indexes that group's rows once each way. Check a run's unreadable
  count before committing its ledger, and re-run if it is not near zero (29 of 559, then 2). A
  buried section that has since died fails its read the same way and returns to the scrape list on
  its stale `live` row until the prober next reaches it.
- **Known cross-host duplicates this rule cannot see.** `pruitthealth.taleo.net` and
  `pruitthealthcareers.taleo.net` each have a section `2`, and both list the same 1,440 reqs
  (measured 2026-09-24). Seven more host pairs have sections with identical sets in the second run:
  daimler/tas-daimler (446), percepta/ttec (102), manpower/manpowergroup (87),
  careerglobalhc/hyundaicapital (75), gb-corporation/ghabbour (62), aa010/elsewedyelectric (40)
  and hkmu/ouhk. v654 serves 82 reqs on both hosts of these pairs. A tenant is one host, so this
  signal never compares them, and no redirect joins them either; the 274 above counts same-host
  duplicates only. Both sides stay scraped and served. It is out of scope here.
