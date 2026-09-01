# ADR-0099: A 404'd Workday detail falls back to the public page's JSON-LD

- Status: Accepted
- Date: 2026-09-01
- Follows [ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md), which put
  400 on the retry ladder and left 404 settling first-attempt — correctly, since a 404 really is
  permanent. This closes the case where *permanent* is true of the endpoint, not the posting.
- Relates to [ADR-0050](0050-a-description-store-so-a-fetched-description-is-never-refetched.md)
  (the detail pass exists to feed the store), [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md)
  (a detail gap is classified, never scope-excluded — unchanged by this).

## Context

A twelve-run log review (2026-08-31 → 2026-09-01, runs `33427383367`–`33481327341`) found a
failure class the 400-throttle work does not touch: **whole sub-sites whose every CXS detail
returns 404 while their listings work**. `workday:iheartmedia/iHM_Corporate_Site` lost 1,968 of
1,968 details across 12/12 runs; seven sibling iHeartMedia sub-sites, `sonoco/SonocoWORKS`,
`liantis` and `canadiansolar` show the same shape — ~3,355 detail 404s in the window, 99.6% of
them iHeartMedia. Every affected Job is indexed title-only, permanently, because the store
(ADR-0050) can only repay text somebody once fetched.

Measured before building (the CLAUDE.md live-verification rule):

- **Reproduced from a residential IP** — 164/164 details 404 locally too, so this is not the CI
  egress and not ADR-0098's throttle wearing a different number.
- **The construction is right** — the same code path returns 200 on citi/2, hitachi, tapestry
  (3/3 controls).
- **No CXS invocation works**: the primary site (`External_iHM`), lowercase variants,
  `Accept-Language`, `?lang=en-US`, and alternate company tokens all 404 or 422. The sub-site's
  posting set is disjoint from the primary's (0 of 164 Req ids overlap), so no cross-site
  resolution exists either.
- **The postings are live**: the public job pages return 200, `postedOn: Posted Yesterday`, and a
  full server-rendered JSON-LD `JobPosting` (9,702-char description on the probe posting). The
  page's own embedded config (`window.workday`) names exactly the tenant/site the scraper uses —
  the tenant's own SPA would 404 on the same CXS URL. The detail endpoint simply does not exist
  for these sites; the SSR page is the only source.
- **The JSON-LD description is the CXS description's text content** — measured on citi/2: same
  content, tag-stripped and entity-escaped, which `html_to_text` already normalises.

## Decision

When a detail GET **settles at 404** (after the ladder), fetch the posting's public job page —
the same URL `parse()` already hands users as `Job.url` — and take the description from its
JSON-LD `JobPosting`, sync and async paths alike. A recovered detail carries only
`description`: the other detail fields (startDate, timeType, locations) map loosely or not at
all from JSON-LD, and guessing them would be worse than the None they are today.

Only 404 triggers it. 400 is ADR-0098's throttle and already retried — a second URL mid-throttle
is extra load, not recovery; 5xx/429 are transient and retried; a page fetch on those would
double traffic exactly when the origin is asking for less.

Recoveries are visible without reading as losses: the pass counts them under a reserved class
label, and `_report_detail_losses` pops that label out before its loss tally — whose invariant
is that it may only fall *short* of the missing count — and reports them on their own INFO line.

## Alternatives considered

- **Mark the boards dead / quarantine them.** Wrong on the facts: the boards list live postings
  (168–286 each) that users can open and apply to. Dropping them trades a description gap for a
  coverage gap.
- **Accept title-only rows** (the status quo). The Jobs embed on title alone — permanently, once
  indexed (ADR-0050's own motivation) — and stay invisible to description-dependent extraction
  (experience, salary, tech gate quality).
- **Per-tenant special-case.** The class is not one tenant (sonoco, liantis, canadiansolar
  already show it small), and nothing about the mechanism is iHeartMedia-specific.
- **Route the whole board through page scraping.** ~2× the requests for the ~99% of tenants whose
  CXS works; the fallback costs one extra GET only where the CXS has already failed permanently.

## Consequences

- One extra GET per settled 404 — bounded by the 404 volume itself (~280/run in the measured
  window), spent only where the alternative is a permanently empty description.
- A genuinely delisted posting (page 404s too, or carries no JSON-LD) is counted under
  `HTTP 404` exactly as before.
- Recovered descriptions are plain text rather than raw HTML — `html_to_text` normalises both
  shapes at parse time, so downstream sees no difference.
- The recovered-count line gives the run logs a direct measure of whether this keeps earning its
  requests; if it trends to zero the fallback can be retired on evidence.
