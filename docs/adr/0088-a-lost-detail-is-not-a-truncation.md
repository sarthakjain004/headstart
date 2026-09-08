# ADR-0088: A lost detail is not a truncation — classify it, don't scope-exclude on it

**Status:** accepted · **Date:** 2026-08-26 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the exclusion channel this declines to use),
[ADR-0076](0076-a-lost-page-is-a-truncation-until-most-of-them-are.md) (the listing pass's
equivalent reporting, whose shape this mirrors),
[ADR-0021](0021-re-embed-on-content-change.md) (the null fields a lost detail leaves),
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store that survives one)

> **Amended 2026-08-30 by [ADR-0097](0097-a-postings-id-comes-from-the-listing-never-the-detail.md).**
> The "Scope of the claim" section below predicted that a lost detail *renames* the Job and called
> it "a defect in `_posting_key`'s detail-dependence — to be fixed there". It was, on 2026-08-30.
> Measured cost of the interval: that rename was **58% of all index flapping** across the 12 runs
> `33283745755`→`33303633939`. This ADR's own decision — classify a detail loss, never
> `mark_truncated` on it — is unchanged and was re-affirmed by ADR-0097.

> **Amended 2026-09-08 — the threshold's *semantics* are unchanged; its *reporting* is now
> bounded.** `_MAX_LOST_DETAIL_SHARE` still decides what a Board's gap is, at the same 0.5, and
> the line still states `missing` and `len(details)`, so a reader can still see which side of it
> every Board fell on. What changed is that only the **first** past-threshold Board in a shard
> process warns; every later one states the identical line at INFO, through the shared
> `log.FirstOnly` guard (`workday._DETAIL_LOSS_OVER_SHARE`).
>
> Why: [ADR-0039](0039-pipeline-logging.md)'s amendment of the same date holds that
> a line which can fire once per Board, per shard or per item is never WARNING — under GitHub
> Actions the formatter renders WARNING as a `::warning::` workflow annotation and GitHub keeps
> **10 per step, 50 per job**, dropping the rest from the run page silently, so WARNING there is a
> quota rather than a level. This line was the last per-Board WARNING in the repo, allowlisted in
> `tests/test_log_levels.py` on the grounds that its threshold was an ADR decision. That
> was true, and it was never the same claim as the line being *bounded*.
>
> It is not bounded in the case that matters. This ADR's framing was per-Board — NGC losing 97% of
> its details — and at that scale a handful of annotations is affordable. The failure mode is not
> per-Board:
> [ADR-0115](0115-one-user-agent-identifies-and-hosts-constrain-its-shape.md)'s User-Agent
> denylist emptied the detail pass of **102 Boards at once**, on five consecutive runs. All of
> them trip the threshold in the same step, so the first ten spend the whole budget on ten samples
> of one systemic fault and displace the aborts the annotations exist for. One annotation per
> shard is enough to *raise* a systemic detail outage; the other 101 lines are how you *size* it,
> and INFO carries them intact.
>
> The bound sits in the scraper rather than in a per-shard aggregate in `scrape_run._report`,
> which was the other candidate. An aggregate would have to be fed by a counter workday increments
> anyway, and it would split one fact across two places — restating per-Board numbers in a second
> line somewhere else is precisely what this ADR declined when it *replaced* `report_detail_gaps`'s
> count rather than adding beside it. Nothing about eviction, truncation, or the loss classes
> changes here: this is a level, not a decision about the data.
>
> Same-day, same-file: the parenthesis is now formatted by `base.loss_breakdown`, shared with
> `report_detail_gaps`, instead of by a near-copy inside `_report_detail_losses`. The two had
> drifted within one commit — `unclassified` against `unlabelled` for the same residual, and a
> bare `…` against a tail that states the residual's size — so the reported text changes in two
> ways: the unlabelled remainder is now spelled **`unlabelled`**, and a fifth-and-beyond cause is
> now summarised as **`…N more cause(s) xR`** rather than `…`. The numbers, the threshold and the
> invariant (the tally always totals `missing`) are unchanged.

> **Amended 2026-09-09 (PR #392).** *The cost claim is per-class, not universal.* "A detail loss
> still costs ADR-0021 null fields and an ADR-0050 gap-ledger entry" (Consequences) held for every
> loss class this ADR had when written, all of which are *fetch* failures. The `no externalPath`
> class is not one. On every such posting measured — 38 stubs over 31,028 postings on 22 Boards,
> swept live 2026-09-09 — the listing served `bulletFields` and no other key, so `parse` yields an
> `Untitled` Job that `tech_filter` drops before either the description store or the index sees it,
> and neither cost is paid. That is a 22-of-125-Board sample, so the scraper counts a titled stub
> separately and logs it rather than assuming the shape holds everywhere.
> `docs/workday/2026-09-09_parser-shaped-detail-losses.md`.

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
