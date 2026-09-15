# ADR-0144: Oracle Taleo was a dead-end only for India

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** [ADR-0139](0139-a-single-source-board-is-its-own-ats.md) (a prior new-`ats` addition), `scrapers/taleo_be.py` (#452), `scrapers/taleo_enterprise.py` (#453)

## Context

`experiment/ats-provider-expansion/PLAN.md` §4c verdicted "Oracle Taleo — do not build for India,"
sourced from a 2026-07-21 India-host mining pass: ~1 live Indian tenant (Genpact), declining product,
GCCs already migrated to Oracle Cloud HCM (which this repo supports via `scrapers/oracle.py`).
CLAUDE.md's TODO section repeated that verdict without the "for India" qualifier, listing bare
**"Oracle Taleo"** under "Verified dead-ends (do not build)" alongside genuinely dead surfaces
(login-only HRMS, ephemeral hiring-drive portals).

Two PRs (#452 `taleo_be.py`, #453 `taleo_enterprise.py`) built Taleo scrapers anyway, discovering
1,760 and 7,443 live boards respectively via global (not India-scoped) Common Crawl + Wayback
mining — D.R. Horton, TTEC, Valero, easyJet, Hyundai Capital America among them. Neither PR updated
the CLAUDE.md line or recorded why the "do not build" verdict didn't apply, leaving the roadmap
actively contradicting the code it sits above. This is the same shape as iCIMS (CLAUDE.md TODO,
above this line): PLAN.md §4c called it "low priority" for the same India-tenant reasoning, and it
was built regardless once discovery went global (#icims, 2026-09-08) — that reversal also went
unrecorded, which is what let this one repeat.

## Decision

The India-mining verdict never applied to Taleo worldwide — CLAUDE.md's Project Scope section is
explicit that "the target is companies worldwide," so a host-mining pass scoped to India was never
positioned to answer whether Taleo was worth building. Taleo Business Edition and Taleo Enterprise
are two different platforms Oracle Taleo split into (confirmed by both scrapers' own URL families:
`{tenant}.tbe.taleo.net/.../ats/careers/v2/searchResults` vs `{tenant}.taleo.net/careersection/...`),
each measured and built on its own global discovery pass, independent of the original India-scoped
verdict.

CLAUDE.md's TODO section is corrected in the same change as this ADR: "Oracle Taleo" is removed from
the dead-ends list, and Taleo Business Edition / Taleo Enterprise get their own ✅ DONE entries
alongside Freshteam/SuccessFactors/iCIMS/Oracle/Zwayam, in the same format.

## Consequences

- **The dead-ends list now needs the same "scope of the verdict" discipline the rest of CLAUDE.md
  already asks for.** A future India-scoped research pass that says "do not build X" should say so
  explicitly in the TODO entry, not just in the linked PLAN.md — otherwise the next scraper PR
  repeats this exact gap rather than reading the qualifier.
- **No code changes follow from this ADR.** Both scrapers already exist, are wired through liveness,
  and passed their own PRs' verification. This ADR is purely the reconciliation CLAUDE.md's own
  tactical rule 6 asks for on a non-obvious reversal, written after the fact because it wasn't
  written at merge time.
- **The India-tenant verdict and India-relevance are different questions, and PLAN.md only answered
  the first.** Verified live, 2026-09-15, two passes (`experiment/taleo-india-relevance/` has the
  second pass's script and captured output). First: none of the 22 Taleo Enterprise Career
  Sections whose *slug* names India (`.../careersection/india_en`, `.../india_apac_...`, etc.), nor
  Genpact's 3 sections, resolve to a working listing API today — the narrow India-*tenant* reading
  of PLAN.md's verdict still holds. Second, broader: the served LanceDB table holds 168 currently
  live India-*located* job rows across both scrapers (127 `taleo_enterprise`: Mercedes-Benz 92,
  TTEC-via-its-Percepta-tenant 30, UFlex 5; 41 `taleo_be`: Milestone Technologies 12, plus 5 more
  employers) — every one of
  them on a Career Section with no "india" in its name. These are India offices of global tenants,
  not India-branded ones, which is exactly what a slug-based or India-host-mining check cannot see.
  So the reversal this ADR records is doubly justified: not merely "global discovery found boards
  elsewhere," but "the platform is already serving real India job-seekers today, through tenants
  a India-scoped or slug-scoped search would never have found."
