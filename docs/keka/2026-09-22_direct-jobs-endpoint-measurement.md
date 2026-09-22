# Keka: `/careers/api/jobs/default/active` vs the two-step UUID path

Measured 2026-09-22. This is the measurement `scrapers/keka.py` cites.

**This endpoint is not a new discovery — the scraper was simply the last call site to adopt it.**
`check_liveness.p_keka` and `scripts/discover/mine_keka.py` both migrated on **2026-07-27**, for
the same stated reason (no org UUID needed; portals exposing neither a background image nor a
logo were recorded UNKNOWN while serving jobs) and on a **25-of-25** sample. `p_keka`'s comment
also records the provenance: the careers SPA's own call, read off
`cdn.keka.com/careers/v/2026/scripts/app/app.min.js` — `$.ajax('/api/jobs/${apiPortalName}/active')`.
The numbers below are an independent re-measurement against the *scraper's* path, at larger n.
Keep all three call sites in step.

## What changed
The scraper read a board in two steps: fetch
`/careers/api/organization/default/careerportalinfo` for the tenant UUID, falling back to the
`/careers` page HTML when absent, then fetch
`/careers/api/embedjobs/default/active/{tenant_uuid}`.

`GET /careers/api/jobs/default/active` returns the same job array in **one request with no UUID
at all**.

## Why the old path lost boards
The UUID only rides inside `careersBackgroundPath` — the portal's background-image URL. A portal
with no custom background sends `"careersBackgroundPath":""`, and on these tenants the `/careers`
fallback is a ~4.5 KB shell with no scripts and no UUID in it. **Both documented sources fail at
once**, while `careerportalinfo` still returns a real company name ("Cokonet Technologies",
"Thoughtwin It Solutions Private Limited"). The board is live; the scraper simply cannot open it.

## Parity, n=150 random Hiring Boards
Sampled from `data/validate/liveness/keka.csv` (`status=live`, `jobs>0` — i.e. Hiring
Boards, CONTEXT.md §Counting Boards), both paths run
back-to-back per board:

| outcome | n |
|---|---|
| both answered, **identical job count** | 125 |
| **direct only** (two-step returned nothing) | **23** |
| two-step only | **0** |
| counts differed | **0** |
| both failed | 2 |

Field sets are **identical between the two paths**. Across 8 boards re-measured directly
(`artifacts/2026-09-22_field-sets-by-board.json`) the payload carries **14 keys on every board**
plus an optional `jobNumber` (present on 3 of 8); every field `parse()` reads is in the
universal 14, so `parse()` is unchanged. Largest direct-only board in
the sample: `aksharfoundation` (32 jobs); across the wider run `thoughtfocusinc` (62) and
`jungheinrich` (27).

**The two-step path never won once in 150 boards**, which is why it was removed outright rather
than kept as a fallback. Re-adding it needs a measured counter-example, not a hunch.

An earlier n=30 pass agreed: 25 match, 5 direct-only, 0 two-step-only, 0 differ.

Raw probe output for the two runs below:
`artifacts/2026-09-22_direct-endpoint-probe-results.json` (`unknowns` = the 22 recoveries,
`dead_sample` = the 120-row dead-pool sample). The one-off probe scripts are not committed.

## Recovery of UUID-unreachable tenants
22 tenants found during discovery whose UUID was unreachable by both old sources were re-probed
through the direct endpoint: **22/22 live**, 541 jobs — `inoptra` 175, `finvasia` 76, `mensa` 49,
`minitek` 40, `proclinkconsulting` 39, `comprinttechsolutions` 35.

## Clean negative: the dead ledger rows are genuinely dead
A 120-row random sample of the 883 `dead` keka ledger rows, re-probed through the direct
endpoint: **0 live** (117 served a dead marker, 2x HTTP 404, 1 transport error). The liveness
prober is correct and there is no recoverable coverage hiding in the dead pool. Do not re-probe
it on this hypothesis again.

## No cap to come up short on
`mark_truncated` was dropped with the UUID branch. Verified there is no page cap to reinstate it
for: `kpgroup` returns **927 jobs in a single array** (its ledger row says 902) and `csdemo` 682.
The endpoint is unpaginated.

## One behaviour change, stated
A non-marker, non-JSON 200 now raises `JSONDecodeError` out of `fetch`, where the old path
degraded to `[]` + `mark_truncated`. A raised board surfaces as a per-company failure and is
**not** evicted, so this is the safer read of an unclassifiable body, and it matches how sibling
scrapers behave (`tests/test_jazzhr.py`).

## Soft-error behaviour is unchanged
The direct endpoint returns the same HTTP-200 HTML soft-errors as the old path — "Invalid Tenant"
for an unknown slug, "Forbidden Access" for a disabled portal — so dead-board detection still
reads the body, never the status code.
