# Eightfold: the sitemap can be the primary listing surface

**Date:** 2026-08-16 · **Follow-up to:** `pcsx-replica-instability.md` (#142, PR #144) ·
**Status:** evaluated and evidenced; not yet built (needs the go-ahead + its own ADR/PR)

The #144 fix compensates for the PCSX search API's replica-unstable pagination (dedupe +
re-sweeps). This investigation asked whether we can stop *compensating* and change surface:
every eightfold tenant also publishes `/careers/sitemap.xml`, and the pipeline discards re-seen
ids' metadata anyway (id-only change detection) — so a complete, stable id list plus per-job
details *only for new ids* is a full scrape.

## What was measured (2026-08-16, plain `http.fetch` with the scraper's own headers)

**The sitemap is served, complete, and stable across the fleet.** All 11 probed tenants — the
flap top-10 plus bayer (the API-403 class) — answered `/careers/sitemap.xml` with HTTP 200 to a
plain fetch carrying the scraper's usual headers (UA + `Referer: /careers` + Accept). The
earlier "eightfold 405s sitemap/job pages to bare http.fetch" observation was from *headerless*
probes; with headers there is no wall, and **no browser (pydoll) is needed anywhere on this
path**. Counts vs the API's `data.count`:

| tenant | sitemap | api count |
|---|---|---|
| citi.eightfold.ai | 3,408 | 3,408 |
| nvidia.eightfold.ai / jobs.nvidia.com | 2,607 | 2,605 |
| careers.qualcomm.com / qualcomm.eightfold.ai | 1,951 | 1,951 |
| caci.eightfold.ai | 1,710 | 1,710 |
| careers.micron.com | 2,672 | 2,668 |
| appliedmaterials.eightfold.ai | 1,802 | 1,803 |
| morganstanley.eightfold.ai | 1,380 | 1,380 |
| ngc.eightfold.ai | 3,685 | 3,685 |
| bayer.eightfold.ai | 610 | API 403 (fallback class) |
| **hp.eightfold.ai** | **1,286** | **712 — disjoint; see rot** |

Two consecutive fetches of ngc's sitemap differed by **zero ids** (the search API differed by
~200 per crawl pre-#144). The sitemap is batch-generated — replica ordering can't touch it.

**`position_details` is a complete metadata record**, not just the description: `name`,
`department`, `location`/`locations`/`standardizedLocations`, `postedTs`, `workLocationOption`,
`locationFlexibility`, `positionUrl`, `jobDescription` — everything the search row carries, plus
fields we've never had (`creationTs`, `atsJobId`, `displayJobId`, `isHot`). ~8 KB JSON. So the
sitemap path loses nothing vs the API path, including `department` (which the JSON-LD fallback
lacks).

**The rot case, and its detector.** hp.eightfold.ai's sitemap is abandoned: 1,285 of its 1,286
ids are unknown to the API (**position_details 404** — a different, older id era; 8-digit ids vs
the current 13-digit), and every `<lastmod>` stops at **2024-06-13**, two years ago. Worse, the
stale job *pages* still render HTTP 200 with JSON-LD — zombie pages, so page-level checks can't
catch this. Two cheap guards do: `|sitemap ids| ≈ data.count` (one API page fetch) and
`max(lastmod)` recent. hp fails both loudly. (hp's API is also the worst behaved of the fleet —
even 3 re-sweeps left a 3-posting gap, which #144 now correctly reports as truncated.)

## Doors probed and closed

- **Page size**: fixed at 10; `num_items`/`limit`/`size`/`per_page`/`rows` ignored,
  `page_size=100` returns *zero* positions. No help.
- **Stable sort**: `sort_by=id`/`position_id`/`title`, `sort`, `order` — all ignored (identical
  head to the default's momentary order). No deterministic ordering exists.
- **robots.txt namespaces**: `/api/career_hub`, `/api/events` → 404 on ngc;
  `/careerhub/explore/jobs` is an HTML shell. Dead on this tenant class.
- **Facet partitioning**: `query` and `location` are honored (could partition the list), but
  `department` is ignored — and the whole approach is moot if the sitemap is primary.
  (Likewise #145's day-bucket targeting — superseded.)

## Proposed architecture (for the ADR, when built)

Per tenant, cheapest-authoritative-first:

1. Fetch `/careers/sitemap.xml` (following index children, as today) → candidate id set.
   One ~1 MB GET replaces ~370 search pages on the largest boards (~99% fewer listing calls).
2. Fetch API search page 0 only → `data.count` (and group_id already comes from `/careers`).
3. **Trust gate**: sitemap is authoritative iff `|ids| ≈ count` (small tolerance for posting
   lag; observed drift ≤ 4) **and** `max(lastmod)` is recent. Then: details
   (`position_details`) for new ids only (ADR-0048 already skips held ones), evictions scoped
   to the sitemap set.
4. Gate fails (hp-class rot, count mismatch) → fall back to today's API path (#144's
   dedupe+re-sweep), which stays fully intact.
5. API 403s entirely (bayer class) → sitemap + per-page JSON-LD, exactly today's fallback —
   with the `lastmod` freshness check now guarding it too (it previously had no rot detector).

pydoll/browser_http stays what it is: an escalation for walls, needed by zero of the 11 probed
tenants on any surface once headers are right.

## Why this beats compensating

#144 makes the unstable API *honest*; this makes the instability *irrelevant*: no re-sweeps, no
collapse-guard reliance for this ATS, ~99% fewer listing requests against a per-origin-metered
edge (ADR-0047), a freshness check the current fallback path lacks, and extra fields
(`creationTs` — a true posting timestamp at second resolution, where `postedTs` is day-bucketed)
for free. The cost is one more moving part: the trust gate, whose failure mode is "fall back to
exactly what runs today".
