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
- Filed against **#219** ("workday: a new eviction-flap pattern") and **#142**, and **closes
  neither**. Be precise about the overlap: #219's own worst-12 Boards meet this evidence only at
  `pwc/crm`, and 8 of its 10 named Boards were sampled live — all already have a listing tier
  equal to their served id, so they were never renameable by this defect. **#219's reported
  symptom stays unexplained**; this ADR fixes a different, larger rename channel found while
  looking at it.
- Evidence: `docs/pipeline/2026-08-30_posting-key-detail-dependence-flapping.md`

## Context

`flap_audit --runs 12` over runs `33283745755`→`33303633939` returned **RED** — 25%
already-known adds against a 10% bar. One Board, `workday:roche/roche-ext`, was **58%** of every
flapped row, and the twelve worst Boards summed to *exactly* the window total: the churn was not
a broad drift, it was a handful of Boards oscillating.

`_posting_key` preferred the detail response's `jobReqId` over both listing-derived tiers. That
value exists only after the per-job detail pass — which in run `33288099045` lost between 68%
(`roche`, 827/1210) and 97% (`dxctechnology`, 837/860) of a Board's details. So a posting whose detail failed was not *missing* from the scrape; it was **renamed**:

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

**And the listing's own URL, not a shape guess, decides which `bulletFields` entry is the id.**
Workday builds the `externalPath` tail as `{Title}_{req id}`, so a field the tail ends with *is*
the req id, whatever it looks like. `_vouched_by_url` is the new first tier; `_looks_like_req_id`
stays as the second, for the Boards whose req id is not in their URL.

Shape-matching cannot be completed — this defect began with `_looks_like_req_id` not knowing
roche's `202607-119609`, and every new tenant is a chance to not know another. Asking the URL is a
question with a definite answer. Measured 25/25 agreement with the served `jobReqId` on roche,
usbank, mercyhealth, montagehealth and aafp; 0/25 on wisconsin, tutorperini and nkg, whose fields
are a closing-date label and two company names — rejected *by construction*, not by regex tuning.

Two details are load-bearing. The match must follow an underscore: a bare `endswith` returns
`Engineer` for every posting on a board whose title ends in a `bulletFields` employment-type tag,
which is exactly the tutorperini/nkg collision. And a trailing `-N` is tolerated, because that is
Workday's re-post disambiguator and lives in the URL only (`…_26-695-1` serves `26-695`).

This is the same property `fetch_raw` already relied on: its dedup pass runs *before* the detail
fetch, so the listing-derived key was already the de-facto within-run identity. Identity and
dedup now agree instead of disagreeing by one network call.

On the four Boards that motivated this change — **not** a corpus total, see Consequences:

| board | rows | id, detail tier dropped, shape tier alone | with the URL tier |
|---|---:|---|---|
| roche | 1,208 | renamed | **unchanged** |
| pwc/crm | 1,716 | renamed | **unchanged** |
| autodesk | 420 | renamed | **unchanged** |
| saabgroup | 452 | renamed | renamed — nothing for the URL to vouch for |

**An intermediate version of this change widened `_REQ_ID_SHAPE` with three digit-leading
alternatives instead, and it is not in the final one.** With the URL tier in place the shape tier
fired **0 times across 2,161 live postings on 149 Boards** (URL tier 2,119, `externalPath` tail
42), and adding the widened alternatives changed **0** ids across 718 postings. Unreached code
that only widens what can be mistaken for a req id is collision surface bought for nothing, so it
was reverted; `_looks_like_req_id` is unchanged from `main`. That measurement is the reason the
first tier asks the URL a question rather than guessing harder at the shape.

## Why not the alternatives

1. **Widen the regex only.** Fixes ~78% of the flapped rows, leaves saabgroup and every
   no-`bulletFields` Board broken, and leaves the class open: the next tenant with an
   unrecognised req-id shape reintroduces it silently. *Built, measured, reverted* — see the
   Decision. Shape-matching is the approach this whole defect is an argument against.
2. **`mark_truncated` on a heavy detail loss.** ADR-0088 refused this and its reasoning is
   undiminished: the listing was *complete*, so the Board is not Unauthoritative, and ADR-0053's
   exclusion has no bound and no drain (measured: 105 dead rows on `careers.qualcomm.com`, oldest
   22 days). It would freeze roche's 1,208 rows against eviction forever.
3. **Keep `jobReqId` when it agrees with the listing tier.** Conditional identity — the id would
   still be a function of the network, just less often. Strictly harder to reason about.
4. **Derive the req id from the `externalPath` tail's last `_` segment.** Works for roche
   (`202607-119609`) and autodesk, fails for saabgroup (`REQ_40700` → `40700`) and pwc
   (`728635WD-3`). A heuristic that is wrong on half the motivating cases.
5. **~~Accept a `bulletFields` entry when it is a suffix of the posting's own `externalPath`.~~**
   **Adopted** — see the Decision above. An earlier draft of this ADR rejected it, citing 2/5, 4/5
   and 3/5 partial matches and a 1.7% offsetting new migration. Both figures were wrong: the
   partial matches came from a probe that stripped `-\d+$` from the tail before comparing, which
   also eats a req id ending in digits (`2026-02608`), and the "new migration" was measured
   against the shape-tier key rather than the served id — `cree` and `cooley` migrate either way,
   and the URL tier *fixes* `cree`. Recorded because the withdrawn numbers were published.

6. **Fix the 400s instead.** That is the upstream defect and it is real (see Consequences), but it
   is a network-behaviour change whose efficacy cannot be measured locally, and it would leave
   identity fragile against *any* future detail loss. These are separable, and this one is the
   one that can be proven before it ships.

## Consequences

- **A one-time id migration, corpus-wide: ~6% of Workday Boards.** Measured by a 140-Board
  random sweep of the cost ledger (102 returned usable listing+detail data, 27,510 postings):
  **1 Board (1.0%) and 452 postings (1.6%)** rename — `saabgroup/Saab_careers`, which carries no
  `bulletFields` at all and is irreducible by any rule. Projected over Workday's **7,620 Scrapable
  Boards** — `load_active_companies()`, not the cost ledger's 10,538 raw rows, per CLAUDE.md's
  rule against counting a ledger CSV directly — that is **~75 Boards**, and over its ~1,078,700
  postings, **~17,000 raw postings** or **~1,200 served rows** at Workday's ~6.9% tech keep rate
  (ADR-0027). *One migrating Board in 102 is a small numerator; treat the projection as an order
  of magnitude, not a forecast.*

  **Without the URL-vouched tier the shape tier alone leaves roughly 6-8% of postings
  migrating** — 6 Boards / 2,212 postings on one sample, 8 Boards / 1,740 on a reviewer's re-run
  at the same seed. The two disagree on which Boards and by how much, because a Board that fails
  its detail fetch drops out of the sample entirely; the ~5× reduction survives both, the precise
  counterfactual does not, and it is quoted here as a range for that reason. Asking the URL
  rather than guessing the shape is why that tier is in this ADR rather than a later one —
  deferring it would migrate the same Boards twice.

  An earlier draft of this ADR said "one Board, 452 rows". That was a 4-Board sample stated as a
  precise figure, and the diagnosis doc had already flagged that "a precise figure needs a sweep,
  not this sample". The sweep says otherwise; the number above is the sweep.

  What the URL-vouched tier recovered: the four Boards carrying a `YYYY-serial` req id
  (`usbank` 2026-0026665, `mercyhealth` 2026-02608, `montagehealth` 2026-968, `aafp` 37-26) that
  no shape rule admits without also admitting a bare ZIP+4, plus `cree` (`26-167`). What it
  cannot recover: a Board with no `bulletFields` (`saabgroup`, `hoedlmayr`), and one whose field
  is a closing-date label with the real req id nowhere in the listing (`wisconsin/UW_Milwaukee`).
  `cooley` migrates either way — its served id is `Req 5047`, with a space its own URL omits, and
  whitespace is squeezed on both sides of the comparison.

  The ADR-0046 collapse guard caps a Board at shedding a quarter of its rows per run, so an
  affected Board drains over ~4 runs and serves both spellings meanwhile — a transitional
  duplicate, not a loss, but a visible one at this scale.

- **A one-time cost is being paid to stop an ongoing one.** That is the trade, stated plainly:
  ~1,200 rows churn once, against ~216 flapped rows per 12-run window. **Not all 216** — the
  rename channel accounts for the Boards whose tiers disagreed, which is roche (58%) plus the
  four smaller dual-shape Boards. `saabgroup/Saab_careers` (14%) emits only `externalPath`-tail
  ids and its churn is *not* explained by this defect; see the evidence doc's "What this does not
  explain". The payoff is the delta signals ADR-0040/0051 depend on (#142's first stated harm)
  stopping being corrupted by the part this does fix.

- **The migration costs embed budget, which is #142's second stated harm.** A renamed id is a new
  row, so it is re-embedded, and its ADR-0050 description-store entry — keyed by full Job id —
  orphans and must be re-fetched. ~1,200 rows is a bounded, one-off charge against the pipeline's
  dominant cost, but it is not free and #142 names it explicitly.
- **The widening is collision-safe, measured, not argued.** 12 Boards / 2,906 live postings, zero
  reduction in distinct ids — including both Boards the module comment names as the hazard
  (`tutorperini` 235/235, `nkg` 48/48), whose shared `bulletFields[0]` is a company name the new
  shapes reject. `^\d{6,}[-_]\d{3,}$` takes six digits rather than five precisely so a bare US
  ZIP+4 cannot reach it.
- **A lost detail still costs real data** — ADR-0021's null fields and an ADR-0050 gap entry. It
  no longer costs *identity*, which is the part that was eviction-shaped.
- **The upstream defect was separate, and is now addressed by
  [ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md).** Workday expresses
  CI throttling as HTTP **400**, which was in neither `http.TRANSIENT` nor
  `workday.egress_fallback_on` — never retried, never rotating the egress, settling as a lost
  detail on the first attempt. Deliberately not bundled here, because its efficacy cannot be
  measured off-CI while this fix could be verified before shipping; the two are independent, and
  this one makes identity correct whether or not that one works.
