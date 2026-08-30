# ADR-0097: A posting's id comes from the listing, never from the detail

- Status: Accepted
- Date: 2026-08-30
- **Fulfils the deferral in [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md)**, which named
  this defect verbatim — "a defect in `_posting_key`'s detail-dependence — to be fixed there" —
  and declined to paper over it by scope-excluding the Board.
- Relates to [ADR-0083](0083-a-grace-period-before-eviction.md) (the grace period the rename
  defeats), [ADR-0023](0023-canonical-board-identity.md) (Board identity; this is *Job* identity),
  [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the exclusion channel ADR-0088 refused,
  and this ADR still refuses).
- Evidence: `docs/pipeline/2026-08-30_posting-key-detail-dependence-flapping.md`

## Context

`flap_audit --runs 12` over runs `33283745755`→`33303633939` returned **RED** — 25%
already-known adds against a 10% bar. One Board, `workday:roche/roche-ext`, was **58%** of every
flapped row, and the twelve worst Boards summed to *exactly* the window total: the churn was not
a broad drift, it was a handful of Boards oscillating.

`_posting_key` preferred the detail response's `jobReqId` over both listing-derived tiers. That
value exists only after the per-job detail pass — which loses 68–97% of a Board's details on a
bad run. So a posting whose detail failed was not *missing* from the scrape; it was **renamed**:

```
detail OK   -> 202607-119609
detail FAIL -> ERP-Solution-Consultant---EHS_202607-119609
```

The old id then went Unconfirmed, evicted on its second consecutive absence, and was re-added the
moment the detail pass recovered. Measured directly: of the 77 roche postings evicted in run
`33288099045`, **75 (97%)** were re-added by `33289938377` — the same postings.

Two independent doors led in, which is why widening a regex alone would not have been a fix:
`_looks_like_req_id` rejected roche's `202607-119609`, pwc's `726071WD` and autodesk's
`26WD100347`; and `saabgroup/Saab_careers` carries **no `bulletFields` at all**, so no regex
could ever have helped it.

## Decision

**A posting's id is a pure function of the listing. `_posting_key` no longer reads
`item["_detail"]`, and `_extract_detail` no longer returns `jobReqId` — nothing consumed it.**

This is the same property `fetch_raw` already relied on: its dedup pass runs *before* the detail
fetch, so the listing-derived key was already the de-facto within-run identity. Identity and
dedup now agree instead of disagreeing by one network call.

**Paired with it, and in the same change on purpose:** `_REQ_ID_SHAPE` gains the three
digit-leading shapes above. This is not a second fix — it is what makes the change **free**.

| board | rows | id after, if only the detail tier is dropped | with the widening too |
|---|---:|---|---|
| roche | 1,208 | renamed | **unchanged** |
| pwc/crm | 1,716 | renamed | **unchanged** |
| autodesk | 420 | renamed | **unchanged** |
| saabgroup | 452 | renamed | renamed (no bulletFields) |
| **total renamed** | | **3,796** | **452** |

Shipping them separately would have renamed roche, pwc and autodesk **twice** — once onto the
`externalPath` tail, then back onto `bulletFields` — so the ordering is load-bearing, not tidiness.

## Why not the alternatives

1. **Widen the regex only.** Zero migration, fixes ~78% of the flapped rows, leaves saabgroup and
   every no-`bulletFields` Board broken and the class open: the next tenant with an unrecognised
   req-id shape reintroduces it silently. Rejected — it treats the symptom's biggest instance.
2. **`mark_truncated` on a heavy detail loss.** ADR-0088 refused this and its reasoning is
   undiminished: the listing was *complete*, so the Board is not Unauthoritative, and ADR-0053's
   exclusion has no bound and no drain (measured: 105 dead rows on `careers.qualcomm.com`, oldest
   22 days). It would freeze roche's 1,208 rows against eviction forever.
3. **Keep `jobReqId` when it agrees with the listing tier.** Conditional identity — the id would
   still be a function of the network, just less often. Strictly harder to reason about.
4. **Derive the req id from the `externalPath` tail's last `_` segment.** Works for roche
   (`202607-119609`) and autodesk, fails for saabgroup (`REQ_40700` → `40700`) and pwc
   (`728635WD-3`). A heuristic that is wrong on half the motivating cases.
5. **Fix the 400s instead.** That is the upstream defect and it is real (see Consequences), but it
   is a network-behaviour change whose efficacy cannot be measured locally, and it would leave
   identity fragile against *any* future detail loss. These are separable, and this one is the
   one that can be proven before it ships.

## Consequences

- **One Board migrates: 452 rows on `saabgroup/Saab_careers`**, once. Verified live — roche,
  pwc/crm and autodesk keep byte-identical ids. The ADR-0046 collapse guard caps a Board at
  shedding a quarter of its rows per run, so saabgroup drains over ~4 runs and serves both
  spellings meanwhile. That is a transitional duplicate, not a loss.
- **The widening is collision-safe, measured, not argued.** 12 Boards / 2,906 live postings, zero
  reduction in distinct ids — including both Boards the module comment names as the hazard
  (`tutorperini` 235/235, `nkg` 48/48), whose shared `bulletFields[0]` is a company name the new
  shapes reject. `^\d{6,}[-_]\d{3,}$` takes six digits rather than five precisely so a bare US
  ZIP+4 cannot reach it.
- **A lost detail still costs real data** — ADR-0021's null fields and an ADR-0050 gap entry. It
  no longer costs *identity*, which is the part that was eviction-shaped.
- **The upstream defect is untouched and still live.** Workday expresses CI throttling as HTTP
  **400**, which is in neither `http._TRANSIENT` nor `workday.egress_fallback_on` — so it is
  never retried and never rotates the egress, and settles as a lost detail on the first attempt.
  The same load from a residential IP returns `{200: 1124, 429: 84}`. That is a separate change,
  deliberately not bundled here: its efficacy cannot be measured off-CI, and this fix makes the
  system correct regardless of whether it lands.
