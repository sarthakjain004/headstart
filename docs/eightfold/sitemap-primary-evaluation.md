# Eightfold: the sitemap can be the primary listing surface

**Date:** 2026-08-16 · **Follow-up to:** `pcsx-replica-instability.md` (#142, PR #144) ·
**Status:** evaluated and evidenced; not yet built (needs the go-ahead + its own ADR/PR)

The #144 fix compensates for the PCSX search API's replica-unstable pagination (dedupe +
re-sweeps). This investigation asked whether we can stop *compensating* and change surface:
every eightfold tenant also publishes `/careers/sitemap.xml`, and the pipeline discards re-seen
ids' metadata anyway (id-only change detection) — so a complete, stable id list plus per-job
details *only for new ids* is a full scrape.

## What was measured (2026-08-16, `http.fetch` — this codebase's transport for every scraper)

**The sitemap is served, complete, and stable across the fleet.** All 11 probed tenants — the
flap top-10 plus bayer (the API-403 class) — answered `/careers/sitemap.xml` with HTTP 200
through `http.fetch`, the same `curl_cffi`-with-Chrome-TLS-impersonation transport every scraper
already uses (see the corrected "surface sensitivity" note in `pcsx-replica-instability.md`: the
earlier "405s to bare http.fetch" belief was wrong — the 405 is the shared per-origin rate
meter, not a route policy, and `http.fetch` already retries it transparently). Isolating headers
from TLS fingerprint (2026-08-16, direct unwrapped probes) found either alone is sufficient: full
browser-style headers over plain stdlib `urllib` (no TLS spoofing) passed, and Chrome TLS
impersonation with a giveaway `python-requests` UA also passed. Only a genuinely bare client
(stdlib defaults, zero headers) was blocked, with 403. Since every call in this codebase already
goes through `http.fetch`, **no browser (pydoll) is needed anywhere on this path** — that
conclusion holds, just not for the reason originally stated. Counts vs the API's `data.count`:

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

## Intricacies and gotchas (2026-08-16 follow-up investigation)

### Why the ±4: two distinct, small, harmless drift sources — but their mix varies per tenant

Deep-diving three tenants (`nvidia.eightfold.ai`, `appliedmaterials.eightfold.ai`,
`careers.micron.com`) resolved every discrepant id individually via `position_details`. All
three confirm the same **two** causes as the only ones present — but not in a fixed ratio:

| tenant | sitemap vs `data.count` | crawl's own shortfall (3 sweeps) | sitemap-only: closed / still-open-but-missed |
|---|---|---|---|
| nvidia | 2,607 = 2,607 | 2,604/2,607 | 3 closed / 2 still-open |
| appliedmaterials | 1,802 ≈ 1,803 | 1,796/1,803 | **1 closed / 6 still-open** |
| micron | 2,672 vs 2,666 (base gap) | 2,660/2,666 | **5 closed / 1 still-open** |

- **Genuine closure** (`position_details` 404): the sitemap, a batch/cron artifact, hadn't
  regenerated since the posting closed. Ordinary staleness.
- **Real, still-open job the API crawl itself missed** (`position_details` 200, valid
  `creationTs`, and *absent* from the fixed #144 crawl's own result — every one of these tenants'
  truncation reports read `still short after N sweeps`). Not a sitemap problem: a residual
  convergence gap in the compensating fix, which sitemap-primary sidesteps entirely since it
  never depends on the re-sweep to find these ids at all.
- One api-only case per tenant sampled (nvidia: 2, sharing one `postedTs` day-bucket — plausible
  recent reposts not yet reflected in the sitemap's last regen).

**The mix is tenant-dependent, not a fixed split** — appliedmaterials' discrepancy is
6-of-7 "API still missed it" (the #144 fix's own residual gap dominates), while micron's is
5-of-6 "genuinely closed" (ordinary staleness dominates). Don't assume either cause is the
majority on an untested tenant; both need to stay in the trust-gate's mental model. The practical
upshot doesn't change — sitemap-primary would have served every one of these "still-open, API
missed it" jobs correctly, on every tenant, without needing a re-sweep at all.

So the ±4 is small, bidirectional, and has two independent causes — sitemap regen lag (both
directions) and the *search* API's own residual non-convergence — neither of which is a defect
in the proposed sitemap-primary design; if anything it strengthens the case, since sitemap-primary
would not inherit the second cause at all.

**Freshness-gate calibration, now with a real number.** nvidia's healthy sitemap's newest
`<lastmod>` sat ~36–40 hours behind the probe time — a normal regen cadence, not real-time. hp's
rotten sitemap is stuck at 2024-06-13 — **two years**. A gate threshold in the range of 7–14 days
cleanly separates "normal cron lag" from "abandoned," with wide margin either side; do not set it
to anything resembling "recent" without a number, or a routine multi-day regen delay would
falsely trip the gate on a healthy tenant.

### The wall mechanism was misdiagnosed — corrected

See the equivalent correction in `pcsx-replica-instability.md`: the original "tiered
route-sensitivity WAF" story was wrong. The real 405 (commit fixing #121) is the shared
per-origin rate/budget meter tripping under sustained concurrent load — not a static per-path
policy — and `http.fetch` already retries it transparently, so production scrapers never
observed a raw block on any surface. Isolating TLS fingerprint from headers (direct, unwrapped
probes) showed either alone passes a single request; only a fully bare client (no headers, no
TLS spoofing) was blocked, and with 403, not 405. The practical conclusion is unchanged — no
browser transport is needed on this path — but the causal story matters for the proposal's risk
section below: the actual risk under sitemap-primary is the *same* shared rate meter as today,
scoped to far fewer requests, not a route-specific wall this design has to out-fox.

### sitemap_index has cross-host children — untested in practice, works by accident of scope

`robots.txt` declares `Sitemap: https://ngc.eightfold.ai/careers/sitemap_index.xml?domain=ngc.com`
as the canonical entry point — a *different* resource from the bare `/careers/sitemap.xml` the
scraper actually fetches. Probed directly: `sitemap_index.xml?domain=ngc.com` returns an index of
**2 children on a completely different host**, `jobs.northropgrumman.com` (ngc's own domain
carries a corporate-parent redirect). Meanwhile bare `/careers/sitemap.xml` — no `?domain=`, no
index indirection — already returned the full 3,685-job list directly on the *first* fetch, so
`_job_urls()`'s child-following branch never executed for any of the 11 tenants probed today
(every one satisfied `if jobs: return jobs` on the top-level fetch). That branch is real code,
matched by `_CHILD_SITEMAP`, and *should* handle a cross-host child correctly (the regex captures
full absolute URLs, and `_get` fetches whatever URL it's given) — but it is currently **unexercised
by any live evidence**, on any tenant this investigation touched. Before shipping sitemap-primary,
find at least one tenant where the bare endpoint is genuinely incomplete and the child-following
path is load-bearing, and add it as a fixture — an untested branch that becomes load-bearing
infrastructure is exactly the kind of gap that bites in production, not in a probe.

### A separate, more urgent finding: duplicate boards via vanity-hostname aliases

Not a sitemap-primary question — a **currently-live data-quality bug**, discovered while
diagnosing tenant coverage. Eightfold tenants sometimes publish the identical board under two
(or more) vanity hostnames. Every hostname pair sharing a `domain=` `group_id` was fleet-scanned
(110 live tenants, one cheap careers-page fetch each):

| shared tenant (`group_id`) | live hostnames | id overlap |
|---|---|---|
| nvidia.com | `nvidia.eightfold.ai`, `jobs.nvidia.com` | 2,607/2,607 — 100% |
| qualcomm.com | `careers.qualcomm.com`, `qualcomm.eightfold.ai` | 1,951/1,951 — 100% |
| micron.com | `careers.micron.com`, `micron.eightfold.ai` | 2,672/2,672 — 100% |
| hsbc.com | `hsbc.eightfold.ai`, `portal.careers.hsbc.com` | 1,466/1,466 — 100% |
| vodafone.com | `jobs.vodafone.com`, `vodafone.eightfold.ai` | 1,130/1,130 — 100% |
| dsm.com | `dsm-firmenich.eightfold.ai`, `dsm.eightfold.ai` | 414/414 — 100% |

All 6 clusters: 100% identical id sets — genuinely the same board, not near-duplicates. **~10,240
job postings' worth of pure duplication** (the smaller side of each pair; the larger the actual
waste if both scrape successfully every run), against eightfold's ~108,232 total advertised jobs
— roughly **9.5% of the ATS's entire volume**. Confirmed live in production, not theoretical: the
2026-08-16 04:05 UTC merge log shows both `jobs.nvidia.com` (101 add/evict lines) and
`nvidia.eightfold.ai` (66 lines) actively churning as separate boards in the same run, same for
the qualcomm pair.

This is structurally invisible to every existing dedup mechanism. `plan_prune`'s case-variant
dedup (ADR-0049) groups by `(canonical Board, native id)` where the canonical Board comes from
matching the id's own prefix against the live keep-set — but these are **different Board keys
entirely** (`eightfold:nvidia.eightfold.ai:...` vs `eightfold:jobs.nvidia.com:...`), each
resolving to its own live ledger entry, so nothing currently unifies them. The board-discovery /
liveness pipeline treats hostname as identity; `group_id` is the only signal that reveals they're
the same tenant, and nothing today reads it before scrape time.

**Not fixed here — flagged as its own issue** (separate from #150; this affects the *current*
production path regardless of which listing surface wins). The natural fix point is
board-discovery/liveness, not the scraper: when two live eightfold tenants share a `group_id`,
keep one (prefer the vanity domain matching the company's other-ATS naming convention, or
whichever the liveness prober saw first) and mark the other dead, the same shape as the
case-variant dedup but keyed on `group_id` instead of lowercased slug.

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

pydoll/browser_http stays what it is: an escalation for walls. None of the 11 probed tenants
needed it on any surface — see the corrected mechanism note above.

## Why this beats compensating

#144 makes the unstable API *honest*; this makes the instability *irrelevant*: no re-sweeps, no
collapse-guard reliance for this ATS, ~99% fewer listing requests against a per-origin-metered
edge (ADR-0047), a freshness check the current fallback path lacks, and extra fields
(`creationTs` — a true posting timestamp at second resolution, where `postedTs` is day-bucketed)
for free. The cost is one more moving part: the trust gate, whose failure mode is "fall back to
exactly what runs today".
