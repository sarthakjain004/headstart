# ADR-0023: Prune stale and duplicate index rows — dead-Board sweep + case-variant dedup

- Status: Accepted
- Date: 2026-07-17
- Builds on [ADR-0014](0014-search-index-ingestion-and-freshness.md) (board-scoped incremental
  sync) and [ADR-0020](0020-free-tier-deployment.md) (the state round-trip the Space serves)

## Context

The index accumulated dead weight the incremental sync can't reach. `index_plan` (ADR-0014) only
evicts within the Boards a run scraped, which leaves two classes of stale rows:

1. **Rows on Boards no longer scraped.** A Board that leaves the scrape list — dead in the liveness
   ledger, dropped from it, or on a disabled ATS ([ADR-0020] / `DISABLED_ATS`) — is never revisited,
   so its rows linger forever. Measured: ~5,490 rows (workable, the disabled `join`, personio, …).
2. **Case-variant duplicates.** `index sync` also fed the eviction scope from the *tech* corpus
   (`data/jobs/tech/`), so a scraped Board that dropped to zero tech jobs kept its stale rows. And,
   larger: the liveness ledger holds duplicate rows for one Board differing only by slug casing —
   Workday sites like `.../External` vs `.../external` parse to `company/External` and
   `company/external`, two Board keys, so one job is scraped and indexed two or three times.
   Measured: ~20,459 duplicate rows (~11% of the index).

Together ~26k of 187k rows (~14%) were stale or duplicated. This inflated the LanceDB table, slowed
the nightly pipeline (contributing to 350-min timeouts), and showed users duplicate jobs.

A first cut measured the damage with the wrong key format — `{ats}:{slug_from}` is the full URL for
Workday, which never matches the id's `company/site` Board — making 46% of rows look "absent". The
dry-run against real store metadata corrected this to the ~14% above and prevented an over-eviction.

## Decision

**A `board_key()` seam.** `BaseScraper.board_key()` returns `{ats}:{slug}` (the `board_of` prefix an
id carries); `WorkdayScraper` overrides it to `{ats}:{company}/{site}`, matching how it builds ids.
Index maintenance maps a ledger entry to the exact key its rows use through this.

**Fix the sync scope.** `index sync` derives the scraped-Board set from the *full* scrape
(`data/jobs/`, non-recursive), not the tech subset — so a Board scraped with no tech jobs still has
its closed postings evicted.

**A prune sweep** (`src/headstart/ingest/index.py`, run after sync, before compact). The keep-set is
the live ledger (enabled ATSes) mapped through `board_key()` and lowercased. It evicts (a) rows whose
canonical Board isn't in the keep-set, and (b) per `(lowercased Board, native id)` group, every id but
the lexicographically-smallest Board casing — the case-variant dupes. Dry-run by default; it refuses
to apply when the keep-set has < 1,000 Boards (a broken ledger must not evict a healthy index).

**Prevent recurrence at the scrape list.** `load_active_companies` now collapses Boards to one entry
per canonical `board_key` — the same lex-min representative the prune keeps — so a Board is scraped
under a single casing. Because scrape and prune agree on the representative, a future scrape *re-sees*
the kept rows; there is no id-scheme change and so no mass re-embedding.

**Reliability alongside** ([the failures that prompted this]): `--delete` on the lancedb upload
(#38), a retry around each `hf upload` (the HF API 504s intermittently), and the smaller index from
this ADR all cut the pipeline's runtime and its timeout/upload failures.

## Consequences

- One-time cleanup removes ~26k rows (187k → ~161k); the pipeline prune keeps it clean thereafter.
- No re-embedding in the common case (both casings live → keep lex-min, scrape lex-min). Edge case:
  a job seen only under a non-representative casing re-embeds once over later runs — a small tail,
  accepted.
- The prune is global (not board-scoped), so it depends on the ledger being trustworthy; the
  min-keep-Boards guard and dry-run default bound the blast radius.

## Alternatives considered

- **Last-seen TTL** (a `last_seen` column, evict rows not seen in N days): the most thorough, catches
  everything uniformly — but needs a schema column + backfill and a rewrite of the sync. Deferred;
  the ledger-keyed prune covers the known sources without a schema change.
- **Canonicalise slugs to lowercase at id construction:** changes every Workday id, so the entire
  ~86k Workday corpus would re-embed. Rejected.
- **Keep the tech-corpus eviction scope:** the original bug — strands rows on Boards that lose their
  tech jobs.

## Amendment (2026-08-11): the duplicate representative is the *live* casing, not the lex-min one

**Status:** accepted. Supersedes the lex-min rule in "A prune sweep" (b) above, and that paragraph's
"the keep-set is the live ledger … mapped through `board_key()` **and lowercased**" — the keep-set
now carries the ledger's own casing (matching is still case-insensitive).

Two distinct churn loops were found in the 2026-08-10/11 CI logs, one per prune reason. The
**off-Board** one is a plain defect: `PersonioScraper` ids each Job by the tenant
(`slug.split(".")[0]`) but inherited the default `board_key()` of `{ats}:{slug}`, and personio's
slug is the whole host — so the keep-set held `personio:{tenant}.jobs.personio.com` while the rows
carried `personio:{tenant}`, and all 2,740 live personio Boards pruned as off-Board on every run.
Fixed by overriding `board_key()`; `base.py` already documented that contract. The **duplicate** one
is the design flaw this amendment revises:

The recurrence argument above — "scrape and prune agree on the representative, [so] a future scrape
*re-sees* the kept rows" — is false, and CI logs show it failing every run. Both sides do apply the
same lex-min *rule*, but to **different populations**: `_dedupe_boards` takes lex-min over the
**live ledger's** Board keys, while `plan_prune` took lex-min over the **casings present in the
index**, which include historical rows whose casing left the ledger long ago. When such a *fossil*
casing sorts below the live one, the two disagree permanently:

- the scrape emits the live casing → `sync` adds that row;
- `prune` sees the fossil sorting first, keeps it, and deletes the row just added;
- `sync` cannot evict the fossil, because the fossil's Board is absent from `scraped_boards` and
  ADR-0014's partial-harvest guard therefore protects it.

So the fresh row churns in and out on every run while the fossil is both **immortal and never
refreshed** — a permanently stale row that no code path can remove. Measured over the 6 runs of
2026-08-10/11 that reached the prune step (the other 2 aborted in `state_fetch`): 136–532 duplicate
prunes per run, mean 217, with individual ids
(`workday:tapestry/tapestry_careers:JR2221`) pruned in 3 separate runs. See
`docs/pipeline/2026-08-11_first-logged-runs.md`.

**Amended decision.** `plan_prune` keeps the row whose Board casing the live ledger produces — the
casing the next scrape will emit, and therefore the only row that can stay fresh — falling back to
lex-min only when no row carries it (the group is all fossils, so any single survivor is equivalent
and it will be replaced once the live casing is next scraped). `live_keep_set` consequently returns
Board keys in the ledger's own casing instead of lowercasing them; `plan_prune` still matches Boards
case-insensitively, and resolves ledger collisions by lex-min so it continues to agree with
`_dedupe_boards`.

**Consequences.** Fossils are now evicted rather than preserved, so the first run after this lands
prunes a backlog rather than the steady ~140–190/run. No id-scheme change and no re-embedding: the
surviving id is one already in the embedding store. The "no re-embedding in the common case" claim
above still holds and now covers the fossil case too, since the kept casing is the scraped one.

**Not addressed here:** ADR-0014's partial-harvest guard is what makes a fossil unreachable by
`sync`. Widening the eviction scope to the canonical lowercased Board would fix the class rather
than this instance, but it changes the guard that also protects partial harvests — the same rule the
open per-board-timeout work must revisit. Deferred to that ADR.
