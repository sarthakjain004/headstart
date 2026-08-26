# ADR-0088: A lost detail is not a truncation — classify it, don't scope-exclude on it

**Status:** accepted · **Date:** 2026-08-26 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the exclusion channel this declines to use),
[ADR-0076](0076-a-lost-page-is-a-truncation-until-most-of-them-are.md) (the listing pass's
equivalent reporting, whose shape this mirrors),
[ADR-0021](0021-re-embed-on-content-change.md) (the null fields a lost detail leaves),
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store that survives one)

## Context

`workday:ngc/Northrop_Grumman_External_Site` reported `3536/3691 details missing` (95.8%) in run
`32942748996` and `3569/3678` (97.1%) in `32936269675`, and became the ADR-0050 gap ledger's #1
Board at 2,695 unsettled. The question it raised — *why does this Board lose 97% of its details
despite 33 egress rotations?* — could not be answered from the logs, because
`report_detail_gaps` emitted a count and nothing else: a 404, a 429, a 503, a spent retry ladder,
a severed connection and a posting the listing gave no `externalPath` for were one output.

`_paginate` has not had that problem since ADR-0076, which made it collect a `Counter` of
`_failure_class(exc)` and report `1 of 185 page(s) failed mid-crawl (HTTP 500 x1)`. The detail
pass never got the same treatment.

Two candidate responses existed, and they are not the same decision:

1. **Say what was lost** — classify each loss and report it per Board.
2. **Act on it** — call `mark_truncated`, which is what `report_detail_gaps`'s return value is
   documented to be for ("so a scraper whose detail pass is *load-bearing* … can mark the Board
   truncated on the same count").

## Decision

**Do (1). Do not do (2). A detail-pass shortfall is classified and reported; it never enters
ADR-0053's eviction-exclusion scope.**

The detail pass records the settled status, the exception class, `no externalPath`, or
`unparseable` per Board and reports one line in `_paginate`'s own shape — `N of M detail(s)
failed mid-crawl (class xN, …)` — at WARNING past `_MAX_LOST_DETAIL_SHARE`, INFO below.

### Why not `mark_truncated`

**It would be a category error.** ADR-0053's Unauthoritative Board means *this Board's scraped
list cannot be read as its complete set of openings*. That is a claim about the **listing**.
NGC's listing was complete — 185/185 pages in run `32942748996`. Every posting on the Board was
read; what is missing is enrichment of postings we already have, which is what ADR-0021's null
fields and ADR-0050's description store exist to carry.

**And it is measurably expensive.** ADR-0053's exclusion has **no bound and no drain**: a Board
short on every run never re-enters eviction scope, and its closed postings are served
indefinitely. Marking NGC would freeze 3,691 rows against eviction on every run, forever.

This is not a hypothetical, and the cost of getting it wrong has been measured. PR #316 (**open at
the time of writing, and shipping no behaviour change yet** — cited here as measurement, not as
settled policy) tracked that exclusion across 16 runs and found a permanent-exclusion set already
accreting on a *different* ATS: 23 SuccessFactors Boards, 82% of the permanent set, 5,643 shielded
rows (44.3%), growing monotonically (`careers.wipro.com` +30%, `careers.hcltech.com` +39%). On
Wipro, **9 "unreadable" detail pages in 4,273 exclude the entire Board from eviction on every
run**, and a 60-page sample of those failures returned 60/60 HTTP 200.

**Be precise about what that does and does not establish**, because it is a narrower finding than
it first looks. #316's root cause is a *classifier* defect, not the truncate-on-detail-gap policy
as such: `successfactors._titled_fields` returns `None` for two different conditions — *we could
not read this page* and *the tenant says this posting is closed* — and truncating on the union
treats closed postings as unread ones. #316's own Option A keeps `mark_truncated` for a page that
is **genuinely** unreadable; it removes only the closed ones from the count. So #316 does *not*
conclude "a detail gap must never truncate", and this ADR must not lean on it as though it did.

The two changes are consistent, and they are consistent for a reason that is stated separately in
each. #316 fixes a Board that is *mistakenly* judged short. This ADR concerns a Board that is not
short at all: Workday's listing is complete, and its detail pass is not load-bearing under
`base.py`'s own definition — "one where `parse` drops the Job without it". SuccessFactors' `parse`
does drop such a Job and so has standing to truncate; Workday's `parse` keeps it. That contract,
not #316, is what carries the decision here. #316 supplies only the price of being wrong, and this
ADR declines to add a 24th Board to the bill.

### Scope of the claim

The reported line's tail says only that *this pass* does not mark the Board truncated. It
deliberately does **not** assert the listing was whole — `_paginate` can `mark_truncated` and
return, so a Board can lose pages *and* details in one run — and it does **not** assert the loss
is harmless. `_posting_key` prefers the detail's `jobReqId`, so on a tenant whose fallback tiers
disagree with it a lost detail *renames* the Job (measured on `roche`: 10/10 renamed when the
detail is absent, because `_looks_like_req_id` rejects `202608-121268`). That churn is
eviction-shaped, and it is a defect in `_posting_key`'s detail-dependence — to be fixed there,
not by widening this line's claim or by scope-excluding the Board.

### The threshold

`_MAX_LOST_DETAIL_SHARE = 0.5` is ADR-0076's half **by analogy, not by derivation**. ADR-0076
justifies its half by a consequence this does not share — past it, too little of the listing was
read to keep the Board's rows. Here nothing turns on the number except which level the line
prints at. It is a reporting threshold with a round value, to be re-set from a run's worth of
`failed mid-crawl` lines rather than defended on principle.

## Consequences

- The question that opened this can be answered from one run's logs instead of a bespoke probe.
- No Board enters eviction-exclusion scope for an enrichment failure; the accretion #316 measured
  does not grow a Workday arm.
- A detail loss still costs ADR-0021 null fields and an ADR-0050 gap-ledger entry. That is the
  intended cost, and it drains on its own once the detail arrives.
- **No throttling fix is implied.** The 429/rotation framing that prompted the investigation was
  false: the endpoint returns 0/3,691 missing on the full board from outside CI, the run's own
  `spare_egress` accounting recorded `walled = 0`, and the misses consumed no retries. Any
  remedy waits on the classes this reporting produces.
