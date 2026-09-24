# ADR-0206: Prune evicts a Board parole re-confirmed gone, and a replaced scraper voids its verdicts

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0058](0058-consecutive-gone-quarantine.md) (quarantine no longer stays out of prune's
keep-set entirely), [ADR-0162](0162-a-gone-verdict-expires-quarantine-parole.md) (the verdict
parole re-earns now also evicts), [ADR-0170](0170-a-provider-outage-is-not-a-gone-verdict.md)
(this ships a drain for served rows on its third prerequisite alone) ·
**Relates to:** [ADR-0200](0200-a-board-scraped-empty-is-in-the-eviction-scope.md) (why `sync`
never scopes a Board that raises), [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (`prune`)

## Context

A Board that raises lands in the shard report's `errors`, never in `boards_ok`, so `index sync`
never puts it in the eviction scope (ADR-0200). A quarantined Board (ADR-0058: 5+ consecutive
HTTP 404/410 scrapes) is also skipped by the scrape plan, and its liveness row still says live, so
`index prune`'s keep-set holds it. Nothing evicts its rows: measured on the served table (v121,
520,566 rows, 2026-09-24), the **910** quarantined Boards served **6,004** rows —
`greenhouse:hyphenconnect` 602 of them, `greenhouse:clickhouse` 129.

ADR-0058 and ADR-0162 kept quarantine out of the keep-set on purpose, and ADR-0170 refused any
eviction until an outage guard exists: a zwayam outage on 2026-09-19 quarantined the provider's
whole cohort at exactly five strikes while its Boards stayed live. That cohort is **3,832** of
the 6,004 rows.

### A sixth strike is a second verdict a week later

A quarantined Board is out of the slice, so it is scraped again only on parole, 7+ days after
its fifth strike (ADR-0162). `board_failures.update` adds a strike to a parole scrape that 404s
again (5 → 6) and deletes the row on any live answer. So `strikes > QUARANTINE_AT` means *two
gone-verdicts at least a week apart*, which is ADR-0170's third prerequisite ("repeated agreement
over time"), and it is already in the ledger.

HF ledger pulled 2026-09-24 (1,061 rows): 173 Boards at 5 strikes, 218 at 6, 519 at 7. All 59
zwayam rows sit at 5. hyphenconnect and clickhouse sit at 7; `ashby:shield-ai` (296 rows) sits at
5, last struck 2026-09-18, so it is kept until its parole scrape 404s again.

### A replaced scraper leaves its verdicts stale

906 of the 910 quarantined Boards were probed through their own scrapers
(`get_scraper(...).fetch()`, 2026-09-24); the four left out are slow zwayam Boards at 5 strikes.
All 186 Boards at 6+ strikes that serve rows were among them, and three of those listed jobs, and two of them were trakstar: `twonice` (144 postings, 10
of its 13 served rows still listed) and `scishis` (1, still listed). Both were struck on
2026-09-17 by the old scraper, which read the HTML board. #564 (merged 2026-09-22T15:07:27Z)
moved trakstar onto `jsapi.recruiterbox.com`, and the HTML board still answers 404 for both.
**All seven** trakstar Boards in the ledger answer through today's scraper; every one was struck
before #564.

So a verdict earned against a surface the scraper no longer reads says nothing about the Board.
No other quarantined ATS had its listing surface replaced since its verdicts were earned: the
scraper changes since 2026-09-10 on those ATSes add fields or reroute requests through the same
URLs, and the probe agrees — outside trakstar, every Board at 6+ strikes answers 404 except
`ashby:sievo` (its one served row is closed) and `greenhouse:weavixinc` (alive and empty, no rows).

## Decision

1. **`index prune` takes a Board out of its keep-set when `board_failures.reconfirmed` names it**
   (`strikes > QUARANTINE_AT`), matched through `lower_key`. Its rows then go as off-Board. A
   first-time quarantine at exactly five strikes keeps its rows. A missing or unreadable ledger
   evicts nothing: `cleanup-index` never fetches `data/state`, so its prune keeps every row, and
   the pipeline's `merge` reads the ledger from the join's `corpus-state` artifact.
2. **`board_failures._VOID_BEFORE` maps an ATS to the instant its listing surface was replaced**,
   and `load` drops that ATS's rows struck before it. A dropped row reads as never struck: the
   Board is back in the slice, and its next save removes the row. The one entry is
   `trakstar: 2026-09-22T15:07:27+00:00`. An entry is added only on measurement: the replaced
   surface still 404s for Boards the new one lists.

Re-measured on the pulled ledger with both in place: **732 Boards evicted, 1,695 rows** (184
Boards serving rows); **171 Boards kept, 4,295 rows** (zwayam 3,832). All 732 were probed: 730
answer 404, `ashby:sievo`'s one served row is closed, and `greenhouse:weavixinc` serves none. So
no still-open posting is evicted.

## Options considered

1. **Evict every quarantined Board** (the unguarded prototype). Rejected: it evicts the zwayam
   cohort's 3,832 rows, most of them live (36 of 55 zwayam Boards probed listed jobs; 17 answered
   403, blocked rather than gone).
2. **Evict only a re-confirmed verdict (chosen).** Needs no new state. It cannot tell an outage
   that lasts past a parole cycle from a gone Board, but that is ADR-0170's calibration problem,
   and a week-long provider-wide 404 is a far rarer event than an afternoon's.
3. **Build ADR-0170's correlated-gone guard first.** Still uncalibrated at one outage, which is
   why ADR-0170 refused it.
4. **Void stale trakstar verdicts by editing the HF ledger by hand.** Rejected: an outward write
   racing the pipeline's own writer (ADR-0129), and the next scraper rewrite would need the same
   edit with no record of why. The map is reviewed code and names its reason.

## Consequences

- A Board whose verdict parole re-confirms loses its served rows at the next `prune`. If it later
  answers, the parole scrape clears its row and `sync` adds its postings back — re-embedded, since
  ADR-0190 drops the vectors of unserved Jobs.
- The zwayam cohort and every other first-time quarantine keep their rows until parole decides.
  A provider outage that outlasts a parole cycle would still evict; ADR-0170's guard remains the
  fix for that and is still unbuilt.
- **A scraper rewrite that replaces a listing surface must add a `_VOID_BEFORE` entry** if Boards
  it now lists sit in the ledger. Nothing detects this: a stale verdict looks exactly like a
  fresh one. The run after this ships drops the seven trakstar rows without counting them as
  `cleared` — `load` never hands them to `update_ledgers failures`.
- A row struck both before and after a cutoff keeps its earlier strikes, since the ledger stores
  only the last stamp. Harmless for trakstar, where no Board has been struck since.
