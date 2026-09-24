# ADR-0187: A Workday requisition is served once per tenant, not once per site

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the duplicate group widens from one
Board to one Workday tenant), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (its
2026-09-11 amendment's "Workday sites share no postings" was a false negative) · **Relates to:**
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (ids resolve to a Board by prefix; the
native id is what follows it), [ADR-0050](0050-persist-descriptions-across-runs.md) (the re-embed
that deletes a row before sync re-adds it), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md)
and [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (eviction scope),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the grace period),
[ADR-0097](0097-a-postings-id-comes-from-the-listing-never-the-detail.md) (how the native id is
chosen), [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the Trends epoch a dedup
change marks)

## Context

A Workday Board is one *site*, keyed `workday:{company}/{site}`. Workday calls the `{company}`
part the **tenant**, and a tenant with several sites posts one requisition to several of them.
`index_plan.plan_prune` grouped duplicates on `(lowercased Board, native id)` — built for
case-variant spellings of one Board (ADR-0023) — so each site's copy was served.

Measured on the served table, version 654 (2026-09-23; 514,163 rows, 104,849 of them Workday):

- The native id is the requisition id, and it is **identical across sites**: 6,212
  `(company, native id)` groups span more than one site, holding 13,358 rows, so **7,146 rows
  (6.8% of Workday) are duplicates**. No suffix normalisation is needed — Workday's per-site `-N`
  disambiguator lives in the URL only, and a trailing `-N` in a native id is part of a real id
  (`REQ-53`).
- Across the sites of one group, titles agree in 99.81%, locations in 99.24%, descriptions in
  89.17%. The 12 groups whose titles disagree are the same requisition re-titled per site (belk
  `JR-103156`, boeing `JR2026523431`, expedia `R-107932`).
- 34 rows carry a native id with **no digit** — a fallback such as `Texas`, or a title slug like
  `QA-Engineer_` (ADR-0097's last resort). Those are not requisition ids.

Parking whole sites through the alias ledger (ADR-0111) was measured first and removes only 2,644
of the 7,146, because most multi-site tenants overlap only partly — a main site plus sub-sites that
each hold a few requisitions of their own (HPE: 1,490 requisitions across its sites, 1,389 on the
largest). And a prune-only rule churns: 5,940 of the 7,146 copies it would remove sit on a
Board in the latest run's recorded eviction scope (`scraped_boards.json`, 14,822 Boards, pulled
2026-09-24), so that run's sync would re-add them, and every later run's sync would re-add the
copies its own slice re-emits, for prune to take out again.

That record was wrong once already. ADR-0111's 2026-09-11 amendment measured 217 same-company
site pairs and found 0 shared postings, but it compared `externalPath`, whose per-site `-N`
suffix differs between two sites' copies of one requisition in 6,208 of 6,212 cases. On the
native id the sharing is plain.

## Decision

**One served row per `(workday, lowercased {company}, native id)`, enforced both where `index sync`
adds rows and in `index prune`.** Board keys do not change and nothing is re-keyed.

- **The key.** A Workday row whose native id contains a digit is grouped on its tenant. Every other
  row, and a Workday row whose native id has no digit, keeps ADR-0023's `(Board, native id)` group.
- **The survivor.** An **incumbent wins**: a requisition already served from a live Board stays
  there, and a copy arriving from another site is not added. With no incumbent — copies arriving
  together, and the one-time cleanup of today's duplicates — the site with the **most jobs in the
  liveness ledger** keeps it, tie-broken by the lexicographically smallest lowercased Board key.
  Within the kept site, ADR-0023's live-casing rule still picks the row. A copy on the incumbent's
  own site is still added, since that is a case-variant spelling of the same Board, and refusing it
  would make a fossil casing immortal: the loop ADR-0023's amendment exists to break.
- **Where the rule lives.** `index_plan`, in private helpers both planners call: `_placement`
  (an id's duplicate group and site, with `_workday_tenant` as the one place the key widens) and
  `_survivor_board` (the ranking). `plan_sync` gains keyword `site_jobs` and `replaced` and reports
  the ids it declined as `SyncPlan.refused`; `plan_prune` gains keyword `site_jobs`. One place
  decides which site keeps a requisition, so the planner that admits a row and the planner that
  removes rows cannot disagree about it. Sync runs the grouping over every ATS, not just Workday:
  a group keyed on its Board holds one site, so nothing else is ever refused, and the only
  Workday-specific line is the key. There is no per-ATS hook on `BaseScraper`: Workday is the only
  ATS with sites under one tenant sharing an id, and a seam with one adapter is a hypothetical one.
- **No new state.** The ranking reads the committed liveness ledger (`workday_site_jobs`, beside
  `live_keep_set`, which reads the same file); the incumbents are the table's own rows, which
  `sync` already reads.

### Staying inside ADR-0083 and ADR-0053

The incumbent is judged **after this run's evictions**, and only on a **live** Board:

- The survivor's Board stops listing the requisition: its first absence marks it Unconfirmed and it
  keeps serving; the other site's copy is added on the first scrape of that site after the
  survivor's second absence evicts it — the same run, when both are in its slice.
- The survivor's Board is not in this run's slice, or is Unauthoritative: no evidence, so it stays
  the incumbent and the other copy stays out. That is the partial-harvest safety, unchanged — but
  see Consequences for the Board that is Unauthoritative on every run.
- The survivor's Board goes dead or is parked: its rows are no longer incumbents, and prune evicts
  them as off-Board. The other site's copy is added on that site's next scrape — the same run if
  it is in the slice; until then the requisition is not served.

`replaced` exists for one case the tests caught. `index sync` deletes the rows being re-embedded
(ADR-0050) before planning, so without it an upgraded incumbent competes as a new arrival, can lose
to a bigger site, and hands the requisition over with a fresh `first_seen`: a "new" listing, and a
Digest entry, for a Job that never left. Passing those ids back keeps them the incumbent.

**The survivor cannot flip on unchanged inputs.** Once the one-time cleanup has run, sync keeps
every requisition on one site, so prune never has two sites to choose between again. A re-probe
that changes the ledger's job counts affects only requisitions arriving afterwards.

## Consequences

Projected on served v654, running the new planners against the committed ledger at `610578b3`
(130,310-Board keep-set, 10,538 Workday sites with a count; the Workday side is identical to the
pre-rebase ledger the figures were first taken on):

| | measured | expected |
| --- | ---: | ---: |
| rows removed by the first prune | **7,146** | ~7,146 |
| Workday requisitions served before → after | 97,669 → 97,669 | — |
| requisitions lost | **0** | 0 |
| rows the next sync re-adds, every live Board re-emitting every id | **0** (7,146 refused) | 0 |

- **An internal-looking site keeps 806 of the 6,212 collapsed requisitions (13%).** 1,158 groups
  had a copy on a site whose name contains `hidden`, `confidential`, `internal`, `private`,
  `sourcer` or `targeted`. In 806 of them that site won the ledger ranking over a public-looking
  sibling, on four sites: `gevernova/only_confidential_executive_recruiting` (737; 2,360 ledger
  jobs against `vernova_externalsite`'s 2,194), `spgi/spgi_internal` (59), `denver/internal-postings`
  (8) and `baincapital/external_private` (2). The other 5,406 survivors sit on the main or another
  public site. Measured live, one posting per site pair (10 URLs): all 10 answer the CXS detail
  with 200, the same title, and `canApply: true`, and the public pages serve 200. So these links
  work. Whether an outside applicant *should* be sent to a site named "internal" or "confidential"
  is not settled here: it is left as an open question rather than special-cased by name, because
  the names are tenant-chosen and a name rule would be a guess.
- **A survivor on a Board that is Unauthoritative on every run keeps its siblings out for good.**
  ADR-0053's exclusion has no drain, so such a survivor is never evicted, and a sibling's copy is
  refused even if the requisition has left the survivor's site and is live only on the sibling.
  Before this change that sibling's row was served. Exposure, measured against the latest run's
  `unauthoritative_boards.json` (96 Boards, 21 of them Workday, pulled 2026-09-24): **47 of the
  6,212 collapsed requisitions** keep a site on that list, on six Workday Boards (`ms/external`,
  `usbank/us_bank_careers`, `rbc/rbcglobal1`, …). One snapshot, so an upper bound on no single
  run. Not fixed here: admitting the sibling while the survivor is out of scope gives prune two
  sites, and prune, which does not see the scope, would take one of them back out every run — the
  churn this rule exists to remove. The cure is the drain ADR-0053 lacks, not an exception here.
- **The one-time cleanup ranks by ledger count, not by freshness.** It can keep a copy whose own
  site already missed it once (Unconfirmed) and evict a sibling that is still listed: **5 of the
  6,212** against the latest `unconfirmed_ids.txt`. If the survivor's site has really dropped it,
  ADR-0083 evicts it on the next scrape and the sibling comes back on its own site's next scrape;
  the requisition is unserved in between.
- **A handover re-stamps `first_seen`.** When a survivor is evicted and another site's copy comes
  in, that copy is a new row with this run's stamp, so the requisition reads as a new listing and
  can reach a Digest. `replaced` prevents this only for the re-embed case, where nothing was
  handed over; a real handover has the same effect any evict-then-re-add has today.
- **The first run removes 7,146 rows at once**, which `role_trends` reads as negative stock. That
  is what ADR-0188's `DEDUP_VERSION` marks. It is not bumped in this change: ADR-0188's PR adds
  the counter and had not merged when this branch was cut, so the bump is made when this merges.
- **Per-Board counts shift between a tenant's sites.** `role_trends`' Board contributions and the
  Hot tab count a requisition on whichever site serves it, so a main site can show fewer rows than
  its ledger count while a sub-site incumbent holds the rest.
- **Blocked copies are still embedded.** `embed_plan` runs in `join`, before `sync`, and sees only
  the embedding store (`meta.jsonl`), which is append-only and keeps the ids of evicted rows. It has
  no view of which rows are served. Using the store as the incumbent would refuse a copy for good
  once its survivor was evicted, breaking the re-admission above; the nearest thing in
  `data/state/` to a served-id list, `role_assignments.parquet`, omits the rows the role taxonomy
  files as non-tech and is a diagnostic side ledger. So the embed gate would need new persisted
  state (a served-id list written by `merge`), and it is not built. The cost it would save is small:
  1,991 of the 7,146 removed rows were first indexed in v654's last seven days, 1.4% of the 144,874
  rows first indexed in that window. The refused copy's vector is also what lets it be added
  without a re-embed when its survivor goes away. A narrower gate needs no new state — rank only
  the copies arriving in one run's corpus by the ledger, embedding just the top site's — but it is
  a third copy of the rule in a different job, it cannot see incumbents, so its choice can differ
  from sync's (it would embed the bigger site's copy that sync then refuses), and it saves only the
  copies whose higher-ranked sibling happens to share their slice. Not built.
- **`plan_prune` and `plan_sync` still work without `site_jobs`**: every site then ties and the
  lexicographic tie-break alone chooses, which is still a correct dedupe, only a different
  survivor. It is defaulted rather than required, unlike `live`, because omitting it mis-ranks
  rather than mis-evicts; both production call sites are in `index.py`, and a test goes red if
  either stops passing it.
- Cost: on v654's 514,163 ids, `plan_prune` takes 1.1 s with or without the rule, `plan_sync` with
  every Workday row arriving at once 1.6 s, and `workday_site_jobs` 0.17 s — noise beside the
  table's own reads and writes.

## Alternatives rejected

- **Park superset sites through the alias ledger.** Removes 2,644 of 7,146, because most
  overlaps are partial; parking a site that also holds unique requisitions would drop those.
- **The rule in prune only.** Sync re-adds what prune removed for every copy on an in-scope
  Board (5,940 of 7,146 on the latest run's scope), run after run: a churn loop of the kind
  ADR-0023's amendment and ADR-0049 each had to undo.
- **Fixed survivor per tenant (its largest site), with no incumbent rule.** The user's choice
  as first stated, refined to incumbent-first before this was built: a fixed survivor moves a
  served requisition whenever the bigger site's copy arrives, re-stamping `first_seen`, while
  incumbent-first gives the same one-time cleanup and no churn.
- **Exclude internal-looking sites by name.** A guess at tenant-chosen names, and the measured
  links work; left as the open question above.
- **Gate `embed_plan` too.** Needs a served-id list in `data/state/` that nothing writes today; see
  Consequences.
