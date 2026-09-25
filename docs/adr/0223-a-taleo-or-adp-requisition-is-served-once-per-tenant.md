# ADR-0223: A Taleo or ADP requisition is served once per tenant, not once per Board

**Status:** accepted · **Date:** 2026-09-25 · **Extends:**
[ADR-0187](0187-a-workday-requisition-is-served-once-per-tenant.md) (the per-tenant duplicate
group, now on three more ATSes) · **Relates to:**
[ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (it buries whole
Taleo Enterprise sections and left the partial overlaps to a per-requisition rule),
[ADR-0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) (an
ADP Board is one career center), [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the
Trends epoch), [ADR-0210](0210-an-eightfold-posting-its-backing-board-serves-is-served-once.md)
(the backing match and the dedup eviction ledger)

## Context

ADR-0187 serves a Workday requisition once per **Tenant**, but `index_plan._workday_tenant`
answered None for every other ATS. Three more ATSes have the same shape — a Tenant (CONTEXT.md)
runs several Boards and lists one requisition on more than one of them under the same native id:

| ATS | Board | Tenant | native id |
| --- | --- | --- | --- |
| Taleo Enterprise | one career section | the host | the listing's `jobId` |
| Taleo Business Edition | one career site (`cws`) | the `org` | `rid` |
| ADP Workforce Now | one career center (`ccId`) | the client `cid` | `ExternalJobID` |

ADR-0186 buries a Taleo Enterprise section whose whole listing another section lists, and recorded
that the partial overlaps it leaves need a per-requisition rule. Its "index-side rule only" was
rejected as a *replacement* for the burials; this rule runs alongside them.

**Measured on served v65** (2026-09-25, 518,846 rows, read-only). `(Tenant, native id)` groups
spanning more than one Board:

| ATS | groups | duplicate rows | titles agree | descriptions ≥ 0.9 |
| --- | ---: | ---: | ---: | ---: |
| Taleo Enterprise | 262 | 271 | 262 | 193 of 261 |
| Taleo Business Edition | 199 | 222 | 199 | 194 of 194 |
| ADP Workforce Now | 598 | 627 | 597 | 598 of 598 |

Descriptions are compared by 5-word-shingle containment, the shorter copy inside the longer; groups
with a copy that has no description are left out. The one title that differs is the same ADP
posting retitled per center (`Project Engineer I, Land Development` against `Project Engineer,
Land Development`, identical description). Taleo Enterprise lets a requisition carry a different
description per section: 66 groups score 0.7–0.9 (Hyatt's section `1` adds a sentence its section
`10880` omits) and 2 score below 0.5 (PruittHealth `740761`, a rewritten external description over
the same title and closing paragraph). Every native id on the three ATSes is all digits (2,562,
1,976 and 9,720 rows).

**Measured live** (2026-09-25): 7 groups per ATS, 21 in all, one per Tenant spread across the
alphabet, each Board read through its scraper's own listing and detail code. In 21 of 21 the id is
listed on both Boards under the same title. Description containment was 0.91–1.0 on Taleo
Enterprise, 0.92–1.0 on Taleo Business Edition and 1.0 on all 7 ADP groups. On Taleo Enterprise
the two sections also state the same `contestNo` in 7 of 7, and across those 7 Tenants' listings
each of 4,333 `contestNo`s names exactly one `jobId`.

## Decision

**One served row per `(ATS, Tenant, native id)` on Workday, Taleo Enterprise, Taleo Business
Edition and ADP Workforce Now, enforced by the planners ADR-0187 built, in both `index sync` and
`index prune`.** Board keys do not change.

- **One function, renamed.** `_workday_tenant` becomes `_requisition_tenant`: it returns
  `{ats}:{tenant}` for an ATS in `_TENANT_REQUISITION_ATSES`, and None otherwise. The Tenant is
  `board_identity.tenant`'s, already the one derivation of the term (ADR-0185): a Taleo Enterprise
  host, a Taleo Business Edition `org`, an ADP `cid`. ADR-0187's other rule holds on every ATS: a
  native id with no digit is a fallback, not a requisition, and stays grouped per Board. The key
  never equals a real Board key: it drops the site, and the Taleo forms drop the URL scheme.
- **Taleo Enterprise keys on `jobId`, the native id, not on `contestNo`.** The two are one-to-one
  in the live sample, so they give the same groups, but only `jobId` is on every row: `contestNo`
  reaches the served `requisition` column only on the Boards the Eightfold pairs name (ADR-0210),
  253 of v65's 2,562 Taleo Enterprise rows. `jobId` is also the id Taleo addresses the posting by in
  every section (`jobdetail.ftl?job=`), where `contestNo` is text a recruiter types.
- **Taleo Business Edition's `org` is enough on its own.** Every ledger row states one, and no
  `org` among the Scrapable Boards sits on more than one pod.
- **The survivor is ADR-0187's, unchanged.** An incumbent wins; with none, a public Board first,
  then the most ledger jobs, then the smallest key. `_NON_PUBLIC_SITE_TOKENS` reads the key after
  its first `/`, so a Taleo Enterprise section named `internal` (5 Scrapable Boards, among them
  `mp_internal` and `aicpa_internalcs`) already ranks after the public ones. On v65, 31 rows on four
  of them move onto a public sibling. A Taleo Business Edition `cws` and an ADP `ccId` are numbers,
  so their keys name nothing.
- **Ledger jobs still rank Workday sites only.** `workday_site_jobs` is not widened, so a Taleo or
  ADP Tenant's Boards tie on jobs and the key decides. A Taleo internal section lists the public
  postings plus its own, so it is usually the larger. Read live, 2 of the 72 Taleo Boards in v65's
  groups name themselves internal only in their page title: `dasstateoh/oh_int` ("Internal Career
  Portal", 480 ledger jobs against `oh_ext`'s 457) and Taleo Business Edition ALLETE `cws=44`
  ("Search Results (Internal)", 18 against 15). Ranked by jobs, the cleanup would keep 42 of the
  461 Taleo groups on them. Ranked by key, it keeps none there. No ADP analogue was found. Live,
  59 of the 343 centers in v65's groups were read through their content-links and client-features
  payloads, the only per-center surfaces. None named itself internal: the 10 that matched a word
  like "employee" or "confidential" did so in benefits, branding or policy text.
  `InternalPostingFlag` was false on 2,069 of 2,069 rows (ADR-0180).
- **The dedup eviction ledger names the rule `tenant-requisition`.** Workday keeps
  `workday-tenant`, so its rows in `data/state/dedup_evictions.csv` stay comparable across this
  change.
- **The Eightfold backing match widens with it.** A Taleo Enterprise backing Board is matched on
  its Tenant, as a Workday one already was (ADR-0210). A backing row is also still found on its
  own Board, so a `contestNo` with no digit (the stamp the lookup tests, where the grouping tests
  `jobId`) matches as it did before. The same holds on Workday, where it never arises: a Workday
  stamp is the native id itself, and no stamped row on v654 or v65 lacks a digit. On v65 this
  removes nothing more.

## Evidence

Projected on served v65 against the committed ledger. The baseline runs the same planners with the
rule narrowed back to Workday:

| | Taleo Enterprise | Taleo BE | ADP | total |
| --- | ---: | ---: | ---: | ---: |
| rows removed by the first prune | 271 | 222 | 627 | **1,120** |
| Tenants | 13 | 18 | 153 | 184 |
| tech postings lost | 0 | 0 | 0 | **0** |
| rows the next sync re-adds, every Board re-emitting every id | 0 | 0 | 0 | **0** |

The served table holds tech postings only, so a lost posting is a removed row whose group keeps no
row. There are none. Sync refuses all 1,120 copies, and a second prune removes nothing.

**Workday is unchanged.** On served v654, which still held ADR-0187's duplicates, origin/main's
planners and these remove the same Workday rows under the same rule: 7,146 against the ledger at
`0be9e1ac`, and 7,137 after #676 excluded vendor demo Boards. On v65 neither removes a
Workday row. ADR-0187's tests pass unmodified.

## Consequences

- **A new grouping, so `DEDUP_VERSION` goes from 5 to 6** (ADR-0188). The bump is made by the
  session that merges this, in the merge itself, so the rule and the Trends marker ship in one
  deploy; it is left out of this branch because other dedup branches are in flight against the
  same counter. The first prune removes the 1,120 rows at once. They are recorded in
  the dedup eviction ledger under `tenant-requisition`.
- **An internal section the tokens miss can still keep a requisition.** Incumbent-wins decides
  every requisition after the cleanup, and only a token triggers displacement. So a new requisition
  whose first copy arrives from `oh_int` or ALLETE `cws=44` stays there while that row stands. A
  committed list of non-public Boards would close this, but it is not built.
- **The same employer on two Taleo hosts is still two Tenants.** `pruitthealth.taleo.net` and
  `pruitthealthcareers.taleo.net` share `jobId`s (ADR-0186's cross-host pairs), and this rule never
  compares them.
- **Everything else ADR-0187 records carries over to the three ATSes.** Its Unauthoritative
  survivor gap, `first_seen` re-stamped on a handover, per-Board counts that shift between a
  Tenant's Boards, and refused copies that are still embedded all apply.
- **Cost:** on v65's 518,846 ids, `plan_prune` takes 1.7–1.8 s with or without the rule.

## Alternatives considered

- **Key Taleo Enterprise on `contestNo`.** It gives the same groups in the live sample, but it
  needs the `requisition` column on every Taleo Enterprise row. That is a 2,300-row rewrite the
  ADR-0210 scoping exists to avoid (HF storage, ADR-0168), for no difference in the groups.
- **Rank the new Boards by ledger jobs** (widen `workday_site_jobs` to the three ledgers). This
  was built first. It changes the survivor of 312 of the 1,120 removals, and it ranks Taleo's
  larger internal sections first (the 42 groups above).
- **A grouping of its own per ATS.** That would be two copies of one rule, which can disagree
  about the survivor. One tenant function keeps sync and prune on one ranking.
- **More alias-ledger burials.** ADR-0186 already buries every section whose whole listing another
  lists. What remains is partial overlap, where burying a section would hide the requisitions only
  it lists.
